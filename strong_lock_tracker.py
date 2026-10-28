
"""Strong LOCK paper-only tracker.

Compatible with QUEEN'S EDGE | 254 app.py.
No Kalshi account access and no real orders.
"""

from datetime import datetime, timezone
import requests


API = "https://api.elections.kalshi.com/trade-api/v2"
TABLE = "strong_lock_calls"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def as_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def normalized_side(value):
    text = str(value or "").strip().upper()

    if text in ("ABOVE", "YES", "UP", "ABOVE TARGET"):
        return "yes"

    if text in ("BELOW", "NO", "DOWN", "BELOW TARGET"):
        return "no"

    return None


def market_quote(market, side, kind):
    dollars = as_float(
        market.get(f"{side}_{kind}_dollars")
    )

    if dollars is not None:
        return dollars

    cents = as_float(
        market.get(f"{side}_{kind}")
    )

    if cents is not None:
        return cents / 100

    return None


class StrongLockStore:
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
            detail = response.text[:1000]

            raise RuntimeError(
                f"Strong LOCK Supabase HTTP "
                f"{response.status_code}: {detail}"
            )

        if not response.content:
            return []

        try:
            return response.json()
        except ValueError:
            return []

    def all(self):
        return self.request(
            "GET",
            params={
                "select": "*",
                "order": "observed_at.desc",
                "limit": 1000,
            },
        )

    def record_once(self, entry):
        ticker = entry.get("ticker")

        if not ticker:
            raise ValueError(
                "Strong LOCK entry is missing ticker"
            )

        # Check for an existing paper entry first.
        existing = self.request(
            "GET",
            params={
                "select": "ticker",
                "ticker": f"eq.{ticker}",
                "limit": 1,
            },
        )

        if existing:
            return {
                "saved": True,
                "created": False,
                "message": "Already saved",
                "ticker": ticker,
            }

        payload = {
            "ticker": ticker,
            "market_name": entry.get("market_name"),
            "series_ticker": entry.get("series_ticker"),
            "side": entry.get("side"),
            "decision": "ENTER",
            "entry_price": entry.get("entry_price"),
            "yes_midpoint": entry.get("yes_midpoint"),
            "close_time": entry.get("close_time"),
            "status": "pending",
            "observed_at": (
                entry.get("observed_at")
                or utc_now()
            ),
        }

        saved = self.request(
            "POST",
            params={
                "on_conflict": "ticker",
            },
            payload=payload,
        )

        # Verify the entry is actually present.
        verified = self.request(
            "GET",
            params={
                "select": "ticker",
                "ticker": f"eq.{ticker}",
                "limit": 1,
            },
        )

        if not verified:
            raise RuntimeError(
                "Supabase returned success, but "
                "the Strong LOCK entry could not "
                "be found afterward."
            )

        return {
            "saved": True,
            "created": bool(saved),
            "message": "Entry saved and verified",
            "ticker": ticker,
        }

    def update_result(self, ticker, result, status):
        return self.request(
            "PATCH",
            params={
                "ticker": f"eq.{ticker}",
            },
            payload={
                "official_result": result,
                "status": status,
            },
        )


def from_secrets(secrets):
    url = secrets.get("SUPABASE_URL")
    key = secrets.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        return None

    return StrongLockStore(url, key)


def evaluate(locked, market, history, verified):
    """Evaluate experimental Strong LOCK paper entry.

    Returns:
        decision, reason, entry_ask, strength
    """

    reasons = []

    if not verified:
        reasons.append("Contract direction not verified")

    side = normalized_side(
        locked.get("call")
    )

    if side is None:
        reasons.append("Locked call side unavailable")

    midpoint = as_float(
        locked.get("yes_midpoint")
    )

    if midpoint is None:
        reasons.append("Locked midpoint unavailable")

    if reasons:
        return "SKIP", "; ".join(reasons), None, 0.0

    strength = (
        midpoint
        if side == "yes"
        else 1.0 - midpoint
    )

    if strength < 0.70:
        reasons.append(
            "Locked quote strength below 70¢"
        )

    bid = market_quote(
        market, side, "bid"
    )

    ask = market_quote(
        market, side, "ask"
    )

    if (
        bid is None
        or ask is None
        or not (0 <= bid <= ask <= 1)
    ):
        reasons.append(
            "Valid side bid/ask unavailable"
        )
        return (
            "SKIP",
            "; ".join(reasons),
            None,
            strength,
        )

    if ask > 0.80:
        reasons.append(
            "Entry ask exceeds 80¢ cap"
        )

    if ask - bid > 0.04:
        reasons.append(
            "Bid/ask spread exceeds 4¢"
        )

    # A winning paper contract pays $1.
    # Assume a flat 2¢ fee.
    if 1.0 - ask - 0.02 <= 0:
        reasons.append(
            "No upside after assumed 2¢ fee"
        )

    # history entries:
    # (datetime, yes_midpoint, source)
    values = []

    for item in history[-5:]:
        if len(item) >= 2:
            value = as_float(item[1])
            if value is not None:
                values.append(value)

    momentum_confirmed = False

    if len(values) >= 3:
        first = values[-2] - values[-3]
        second = values[-1] - values[-2]

        if side == "yes":
            momentum_confirmed = (
                first > 0 and second > 0
            )
        else:
            momentum_confirmed = (
                first < 0 and second < 0
            )

    if not momentum_confirmed:
        reasons.append(
            "Recent momentum not confirmed"
        )

    if reasons:
        return (
            "SKIP",
            "; ".join(reasons),
            ask,
            strength,
        )

    return (
        "ENTER",
        "All experimental filters passed",
        ask,
        strength,
    )


def fetch_official_result(ticker):
    response = requests.get(
        f"{API}/markets/{ticker}",
        timeout=10,
    )

    response.raise_for_status()

    market = response.json().get(
        "market", {}
    )

    result = str(
        market.get("result") or ""
    ).lower()

    if result in ("yes", "no"):
        return result

    return None


def grade_pending(store, rows):
    """Grade saved paper entries using Kalshi results."""

    for row in rows:
        if str(
            row.get("status") or ""
        ).lower() != "pending":
            continue

        ticker = row.get("ticker")

        if not ticker:
            continue

        try:
            official = fetch_official_result(
                ticker
            )

            if official is None:
                continue

            side = normalized_side(
                row.get("side")
            )

            if side is None:
                continue

            status = (
                "win"
                if side == official
                else "loss"
            )

            store.update_result(
                ticker,
                official,
                status,
            )

        except requests.RequestException:
            # Temporary Kalshi API failure:
            # leave entry pending.
            continue
