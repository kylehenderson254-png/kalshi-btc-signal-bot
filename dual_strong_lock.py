
"""QUEEN'S EDGE | 254 — Dual Strong LOCK.

Read-only Kalshi market data.
Paper signals only. No real orders or automatic trading.
"""

from datetime import datetime, timezone
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
TABLE = "dual_strong_lock_entries"

MOMENTUM_SECONDS = 25
ALIGNMENT_SECONDS = 40
MIN_HISTORY_SECONDS = 30
MIN_OBSERVATIONS = 8

MIN_MIDPOINT_ABOVE = 0.62
MAX_MIDPOINT_BELOW = 0.38
MIN_MOVE = 0.012
MIN_ALIGNED_MOVES = 3
MIN_TICK_CHANGE = 0.002

MAX_SPREAD = 0.04
MAX_ENTRY_ASK = 0.88


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def quote(market, side, kind):
    value = number(market.get(f"{side}_{kind}_dollars"))

    if value is not None:
        return value

    value = number(market.get(f"{side}_{kind}"))

    return value / 100 if value is not None else None


def analyze(market, history, seconds_left, verified, pressure=None):
    """Evaluate market direction, momentum, and quote quality."""

    if not verified:
        return {
            "decision": "WAIT",
            "reason": "Contract direction unverified",
        }

    if seconds_left is None or seconds_left <= 0:
        return {
            "decision": "WAIT",
            "reason": "Contract closed",
        }

    if seconds_left > 660:
        return {
            "decision": "WAIT",
            "reason": "Collecting market history",
        }

    points = []

    for item in history:
        if len(item) < 2:
            continue

        stamp = item[0]
        value = number(item[1])

        if not isinstance(stamp, datetime) or value is None:
            continue

        if not 0 <= value <= 1:
            continue

        points.append((stamp, value))

    points.sort(key=lambda item: item[0])

    # Remove duplicate timestamps so they cannot create
    # artificial momentum observations.
    unique_points = []

    for stamp, value in points:
        if unique_points and stamp == unique_points[-1][0]:
            unique_points[-1] = (stamp, value)
        else:
            unique_points.append((stamp, value))

    points = unique_points

    if len(points) < MIN_OBSERVATIONS:
        return {
            "decision": "WAIT",
            "reason": (
                f"Collecting observations "
                f"{len(points)}/{MIN_OBSERVATIONS}"
            ),
        }

    now = points[-1][0]
    midpoint = points[-1][1]

    history_span = (now - points[0][0]).total_seconds()

    if history_span < MIN_HISTORY_SECONDS:
        return {
            "decision": "WAIT",
            "reason": (
                f"Collecting history "
                f"{history_span:.0f}/{MIN_HISTORY_SECONDS}s"
            ),
        }

    # Find the most recent observation at least
    # 25 seconds older than the latest quote.
    older = next(
        (
            value
            for stamp, value in reversed(points)
            if (now - stamp).total_seconds() >= MOMENTUM_SECONDS
        ),
        None,
    )

    if older is None:
        return {
            "decision": "WAIT",
            "reason": "Need 25 seconds of history",
        }

    change = midpoint - older

    # BALANCED MOMENTUM:
    # Count actual directional changes observed during
    # the last 40 seconds, rather than just eight samples.
    recent_points = [
        (stamp, value)
        for stamp, value in points
        if 0 <= (now - stamp).total_seconds() <= ALIGNMENT_SECONDS
    ]

    differences = [
        current[1] - previous[1]
        for previous, current in zip(
            recent_points,
            recent_points[1:],
        )
    ]

    rising = sum(
        difference > MIN_TICK_CHANGE
        for difference in differences
    )

    falling = sum(
        difference < -MIN_TICK_CHANGE
        for difference in differences
    )

    unchanged = sum(
        abs(difference) <= MIN_TICK_CHANGE
        for difference in differences
    )

    # Determine the direction supported by the midpoint.
    if midpoint >= MIN_MIDPOINT_ABOVE:
        side = "ABOVE"
        key = "yes"
        aligned = rising
        opposing = falling
        momentum_ok = change >= MIN_MOVE
        required_move = "+1.2¢"

    elif midpoint <= MAX_MIDPOINT_BELOW:
        side = "BELOW"
        key = "no"
        aligned = falling
        opposing = rising
        momentum_ok = change <= -MIN_MOVE
        required_move = "-1.2¢"

    else:
        return {
            "decision": "WAIT",
            "reason": (
                f"NONE | Midpoint {midpoint * 100:.1f}¢ "
                f"(needs >=62¢ or <=38¢) | "
                f"Move {change * 100:+.1f}¢ | "
                f"Up {rising} / Down {falling}"
            ),
        }

    # Do not enter unless both net momentum and
    # three observed directional changes qualify.
    if not momentum_ok or aligned < MIN_ALIGNED_MOVES:
        return {
            "decision": "WAIT",
            "reason": (
                f"{side} | "
                f"Midpoint {midpoint * 100:.1f}¢ | "
                f"Move {change * 100:+.1f}¢ "
                f"(needs {required_move}) | "
                f"Aligned ticks {aligned}/3 required | "
                f"Opposing {opposing} | "
                f"Flat {unchanged} | "
                f"Window {ALIGNMENT_SECONDS}s"
            ),
        }

    bid = quote(market, key, "bid")
    ask = quote(market, key, "ask")

    if (
        bid is None
        or ask is None
        or not 0 <= bid <= ask <= 1
    ):
        return {
            "decision": "WAIT",
            "reason": "Invalid bid/ask",
        }

    spread = ask - bid

    if spread > MAX_SPREAD:
        return {
            "decision": "WAIT",
            "reason": (
                f"Spread {spread * 100:.1f}¢ "
                f"exceeds 4¢ maximum"
            ),
        }

    if ask > MAX_ENTRY_ASK:
        return {
            "decision": "WAIT",
            "reason": (
                f"Entry ask {ask * 100:.1f}¢ "
                f"exceeds 88¢ maximum"
            ),
        }

    flow = number(pressure)

    if flow is not None and -1 <= flow <= 1:
        if side == "ABOVE" and flow < -0.15:
            return {
                "decision": "WAIT",
                "reason": "Selling flow opposes call",
            }

        if side == "BELOW" and flow > 0.15:
            return {
                "decision": "WAIT",
                "reason": "Buying flow opposes call",
            }

    return {
        "decision": "ENTER",
        "reason": (
            f"{side} | "
            f"Momentum {change * 100:+.1f}¢ | "
            f"Aligned {aligned} moves in "
            f"{ALIGNMENT_SECONDS}s | "
            f"Opposing {opposing} | "
            f"Spread {spread * 100:.1f}¢"
        ),
        "side": side,
        "entry_price": ask,
        "yes_midpoint": midpoint,
        "momentum_cents": round(change * 100, 3),
        "spread_cents": round(spread * 100, 3),
        "flow_score": flow,
    }


class Store:

    def __init__(self, url, key):
        self.url = url.rstrip("/")
        self.key = key

    def request(self, method, params=None, payload=None):
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

        response = requests.request(
            method,
            f"{self.url}/rest/v1/{TABLE}",
            headers=headers,
            params=params,
            json=payload,
            timeout=12,
        )

        if not response.ok:
            raise RuntimeError(
                f"Dual Strong LOCK Supabase HTTP "
                f"{response.status_code}: "
                f"{response.text[:600]}"
            )

        return response.json() if response.content else []

    def all(self):
        return self.request(
            "GET",
            {
                "select": "*",
                "order": "observed_at.desc",
                "limit": 1000,
            },
        )

    def record_once(self, entry):
        ticker = entry["ticker"]
        stage = entry["stage"]

        existing = self.request(
            "GET",
            {
                "select": "ticker",
                "ticker": f"eq.{ticker}",
                "stage": f"eq.{stage}",
                "limit": 1,
            },
        )

        if existing:
            return "ALREADY SAVED"

        fields = (
            "ticker",
            "stage",
            "market_name",
            "series_ticker",
            "side",
            "entry_price",
            "yes_midpoint",
            "close_time",
            "observed_at",
            "status",
        )

        payload = {
            key: entry[key]
            for key in fields
            if key in entry
        }

        try:
            self.request(
                "POST",
                payload=payload,
            )

        except RuntimeError:
            existing = self.request(
                "GET",
                {
                    "select": "ticker",
                    "ticker": f"eq.{ticker}",
                    "stage": f"eq.{stage}",
                    "limit": 1,
                },
            )

            if existing:
                return "ALREADY SAVED"

            raise

        verified = self.request(
            "GET",
            {
                "select": "ticker",
                "ticker": f"eq.{ticker}",
                "stage": f"eq.{stage}",
                "limit": 1,
            },
        )

        if not verified:
            raise RuntimeError(
                "Entry was not found after saving"
            )

        return "SAVED"

    def grade(self, rows):
        for row in rows:
            if str(row.get("status", "")).lower() != "pending":
                continue

            ticker = row.get("ticker")
            stage = row.get("stage")
            side = str(row.get("side", "")).upper()

            if not ticker or not stage:
                continue

            try:
                response = requests.get(
                    f"{API}/markets/{ticker}",
                    timeout=7,
                )

                response.raise_for_status()

                result = str(
                    response.json()
                    .get("market", {})
                    .get("result", "")
                ).lower()

            except requests.RequestException:
                continue

            if result not in ("yes", "no"):
                continue

            won = (
                (side == "ABOVE" and result == "yes")
                or (side == "BELOW" and result == "no")
            )

            self.request(
                "PATCH",
                {
                    "ticker": f"eq.{ticker}",
                    "stage": f"eq.{stage}",
                },
                {
                    "official_result": result,
                    "status": "win" if won else "loss",
                },
            )


def from_secrets(secrets):
    url = secrets.get("SUPABASE_URL")
    key = secrets.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        return None

    return Store(url, key)
