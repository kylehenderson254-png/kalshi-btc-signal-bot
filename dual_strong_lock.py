"""QUEEN'S EDGE | 254 — Dual Strong LOCK.
Public Kalshi data. Paper signals only. No real trades.
"""

from datetime import datetime, timezone
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
TABLE = "dual_strong_lock_entries"


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
    """Evaluate observed YES midpoint movement and quote quality."""

    if not verified:
        return {"decision": "WAIT", "reason": "Contract direction unverified"}

    if seconds_left is None or seconds_left <= 0:
        return {"decision": "WAIT", "reason": "Contract closed"}

    if seconds_left > 660:
        return {"decision": "WAIT", "reason": "Collecting market history"}

    points = []
    for item in history:
        if len(item) < 2:
            continue
        value = number(item[1])
        if value is not None:
            points.append((item[0], value))

    if len(points) < 8:
        return {"decision": "WAIT", "reason": "Collecting observations"}

    now = points[-1][0]
    older = next(
        (
            value for stamp, value in reversed(points)
            if (now - stamp).total_seconds() >= 25
        ),
        None,
    )

    if older is None:
        return {"decision": "WAIT", "reason": "Need 25 seconds of history"}

    midpoint = points[-1][1]
    change = midpoint - older

    recent = [value for _, value in points[-8:]]
    differences = [
        b - a for a, b in zip(recent, recent[1:])
    ]

    rising = sum(d > 0.002 for d in differences)
    falling = sum(d < -0.002 for d in differences)

    if midpoint >= 0.62 and change >= 0.012 and rising >= 3:
        side = "ABOVE"
        key = "yes"
        aligned = rising
    elif midpoint <= 0.38 and change <= -0.012 and falling >= 3:
        side = "BELOW"
        key = "no"
        aligned = falling
    else:
        return {
            "decision": "WAIT",
            "reason": (
                f"Momentum not confirmed: "
                f"{change * 100:+.1f}¢ over 25s"
            ),
        }

    bid = quote(market, key, "bid")
    ask = quote(market, key, "ask")

    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        return {"decision": "WAIT", "reason": "Invalid bid/ask"}

    spread = ask - bid

    if spread > 0.04:
        return {"decision": "WAIT", "reason": "Spread exceeds 4¢"}

    if ask > 0.88:
        return {"decision": "WAIT", "reason": "Entry exceeds 88¢"}

    flow = number(pressure)

    if flow is not None and -1 <= flow <= 1:
        if side == "ABOVE" and flow < -0.15:
            return {"decision": "WAIT", "reason": "Selling flow opposes call"}
        if side == "BELOW" and flow > 0.15:
            return {"decision": "WAIT", "reason": "Buying flow opposes call"}

    return {
        "decision": "ENTER",
        "reason": (
            f"{side} · momentum {change * 100:+.1f}¢ · "
            f"{aligned}/7 aligned moves · spread {spread * 100:.1f}¢"
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
                f"{response.status_code}: {response.text[:600]}"
            )

        return response.json() if response.content else []

    def all(self):
        return self.request(
            "GET",
            {"select": "*", "order": "observed_at.desc", "limit": 1000},
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
            "ticker", "stage", "market_name", "series_ticker",
            "side", "entry_price", "yes_midpoint",
            "close_time", "observed_at", "status",
        )

        payload = {key: entry[key] for key in fields if key in entry}

        try:
            self.request("POST", payload=payload)
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
            raise RuntimeError("Entry was not found after saving")

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
                    response.json().get("market", {}).get("result", "")
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
