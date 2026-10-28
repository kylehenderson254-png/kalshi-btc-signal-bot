
"""QUEEN'S EDGE | 254 — Strong LOCK paper-entry tracker.

Read-only Kalshi analysis. No real orders.
Conservative experimental filters, not a proven trading edge.
"""

from datetime import datetime, timezone
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"


class StrongLockStore:
    def __init__(self, url, key):
        self.endpoint = (
            url.rstrip("/") + "/rest/v1/strong_lock_entries"
        )
        self.headers = {
            "apikey": key,
            "Authorization": "Bearer " + key,
        }

    def request(self, method, **kwargs):
        headers = {
            **self.headers,
            **kwargs.pop("headers", {}),
        }
        response = requests.request(
            method,
            self.endpoint,
            headers=headers,
            timeout=12,
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.content else []

    def all(self):
        return self.request(
            "GET",
            params={
                "select": "*",
                "order": "observed_at.desc",
                "limit": "1000",
            },
        )

    def record_once(self, row):
        self.request(
            "POST",
            json=row,
            params={"on_conflict": "ticker"},
            headers={
                "Prefer":
                "resolution=ignore-duplicates,return=minimal"
            },
        )

    def settle(self, ticker, result, side):
        won = (
            (result == "yes" and side == "ABOVE")
            or (result == "no" and side == "BELOW")
        )
        self.request(
            "PATCH",
            params={
                "ticker": "eq." + ticker,
                "decision": "eq.ENTER",
                "status": "eq.pending",
            },
            json={
                "status": "win" if won else "loss",
                "official_result": result,
            },
            headers={"Prefer": "return=minimal"},
        )


def from_secrets(secrets):
    url = secrets.get("SUPABASE_URL")
    key = secrets.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    return StrongLockStore(url, key)


def numeric(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def side_quote(market, side, kind):
    """Prefer Kalshi decimal-dollar fields.

    Legacy integer-cent fields are converted to dollars.
    """
    prefix = "yes" if side == "ABOVE" else "no"
    dollar_field = f"{prefix}_{kind}_dollars"
    cent_field = f"{prefix}_{kind}"

    dollar_value = numeric(market.get(dollar_field))
    if dollar_value is not None:
        return dollar_value

    cent_value = numeric(market.get(cent_field))
    if cent_value is not None:
        return cent_value / 100

    return None


def evaluate(locked, market, history, verified, now=None):
    """Return decision, explanation, ask, quote strength.

    Filters:
    - Verified YES = ABOVE settlement mapping
    - Locked-side quote strength >= 70 cents
    - Entry ask <= 80 cents
    - Bid/ask spread <= 4 cents
    - Two consecutive confirming momentum moves
    - Positive gross payout room after assumed 2-cent fee

    ENTER means an experimental paper signal, not an order.
    """
    now = now or datetime.now(timezone.utc)

    side = str(locked.get("call") or "").upper()
    yes_mid = numeric(locked.get("yes_midpoint"))
    reasons = []

    if side not in ("ABOVE", "BELOW") or not verified:
        reasons.append("Contract direction unverified")

    if yes_mid is None or not 0 <= yes_mid <= 1:
        reasons.append("Locked YES midpoint unavailable")
        confidence = 0.0
    else:
        confidence = (
            yes_mid if side == "ABOVE"
            else 1 - yes_mid
        )

    if confidence < 0.70:
        reasons.append(
            "Locked quote strength below 70¢"
        )

    ask = None
    bid = None

    if side in ("ABOVE", "BELOW"):
        ask = side_quote(market, side, "ask")
        bid = side_quote(market, side, "bid")

    valid_quotes = (
        ask is not None
        and bid is not None
        and 0 < bid <= ask < 1
    )

    if not valid_quotes:
        reasons.append(
            "Valid side bid/ask unavailable"
        )
    else:
        if ask > 0.80:
            reasons.append(
                "Entry ask exceeds 80¢ cap"
            )

        if ask - bid > 0.04:
            reasons.append(
                "Spread exceeds 4¢"
            )

        if ask + 0.02 >= 1:
            reasons.append(
                "No upside after assumed 2¢ fee"
            )

    # History stores YES midpoints.
    # BELOW momentum must be evaluated in the
    # opposite direction from ABOVE momentum.
    if not history or len(history) < 3:
        reasons.append(
            "Insufficient live quote history"
        )
    else:
        latest_time = history[-1][0]
        moves = []

        for i in range(1, len(history)):
            previous = history[i - 1]
            current = history[i]

            try:
                age = (
                    latest_time - current[0]
                ).total_seconds()

                if age > 90:
                    continue

                delta = (
                    float(current[1])
                    - float(previous[1])
                )

                if delta >= 0.005:
                    moves.append(1)
                elif delta <= -0.005:
                    moves.append(-1)

            except (TypeError, ValueError, IndexError):
                continue

        if side == "ABOVE":
            required = [1, 1]
        elif side == "BELOW":
            required = [-1, -1]
        else:
            required = []

        if moves[-2:] != required:
            reasons.append(
                "Recent momentum not confirmed"
            )

    decision = "SKIP" if reasons else "ENTER"

    explanation = (
        "; ".join(reasons)
        if reasons
        else "All experimental filters passed"
    )

    return decision, explanation, ask, confidence


def grade_pending(store, rows, max_checks=8):
    """Grade pending paper entries using official Kalshi results."""
    now = datetime.now(timezone.utc)
    checks = 0

    for row in rows:
        if checks >= max_checks:
            break

        if row.get("decision") != "ENTER":
            continue

        if row.get("status") != "pending":
            continue

        ticker = row.get("ticker")
        side = row.get("side")

        if not ticker or side not in ("ABOVE", "BELOW"):
            continue

        try:
            raw_close = row.get("close_time")
            close = datetime.fromisoformat(
                str(raw_close).replace("Z", "+00:00")
            )

            if close.tzinfo is None:
                close = close.replace(
                    tzinfo=timezone.utc
                )

            if close > now:
                continue

            checks += 1

            response = requests.get(
                f"{API}/markets/{ticker}",
                timeout=12,
            )
            response.raise_for_status()

            market = response.json().get(
                "market", {}
            )

            status = str(
                market.get("status") or ""
            ).lower()

            result = str(
                market.get("result") or ""
            ).lower()

            if (
                status in ("settled", "finalized")
                and result in ("yes", "no")
            ):
                store.settle(
                    ticker,
                    result,
                    side,
                )

        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ):
            continue
