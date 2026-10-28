
"""QUEEN'S EDGE | 254 — read-only Strong LOCK paper tracker."""

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

        if not response.ok:
            try:
                details = response.json()
                message = (
                    details.get("message")
                    or details.get("details")
                    or str(details)
                )
                code = details.get("code", "")
            except ValueError:
                message = response.text[:400]
                code = ""

            raise RuntimeError(
                f"Strong LOCK database error "
                f"{response.status_code} {code}: {message}"
            )

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
        allowed = (
            "ticker",
            "market_name",
            "series_ticker",
            "side",
            "decision",
            "entry_price",
            "yes_midpoint",
            "close_time",
            "status",
            "observed_at",
        )

        payload = {
            key: row[key]
            for key in allowed
            if key in row
        }

        payload.setdefault(
            "observed_at",
            datetime.now(timezone.utc).isoformat(),
        )
        payload.setdefault("status", "pending")
        payload.setdefault("decision", "ENTER")

        self.request(
            "POST",
            json=payload,
            params={"on_conflict": "ticker"},
            headers={
                "Prefer":
                "resolution=ignore-duplicates,return=minimal"
            },
        )

    def settle(self, ticker, result, side):
        result = str(result).lower()
        side = str(side).upper()

        if (
            result not in ("yes", "no")
            or side not in ("ABOVE", "BELOW")
        ):
            return

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

    return (
        StrongLockStore(url, key)
        if url and key
        else None
    )


def numeric(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def side_quote(market, side, kind):
    prefix = "yes" if side == "ABOVE" else "no"

    dollar_value = numeric(
        market.get(f"{prefix}_{kind}_dollars")
    )

    if dollar_value is not None:
        return dollar_value

    cents = numeric(
        market.get(f"{prefix}_{kind}")
    )

    return cents / 100 if cents is not None else None


def evaluate(locked, market, history, verified, now=None):
    """Experimental paper signal; no real orders."""

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
        reasons.append("Locked quote strength below 70¢")

    ask = (
        side_quote(market, side, "ask")
        if side in ("ABOVE", "BELOW")
        else None
    )

    bid = (
        side_quote(market, side, "bid")
        if side in ("ABOVE", "BELOW")
        else None
    )

    if not (
        ask is not None
        and bid is not None
        and 0 < bid <= ask < 1
    ):
        reasons.append("Valid side bid/ask unavailable")
    else:
        if ask > 0.80:
            reasons.append("Entry ask exceeds 80¢ cap")

        if ask - bid > 0.04:
            reasons.append("Spread exceeds 4¢")

        if ask + 0.02 >= 1:
            reasons.append("No upside after assumed 2¢ fee")

    if not history or len(history) < 3:
        reasons.append("Insufficient live quote history")
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

            except (
                TypeError,
                ValueError,
                IndexError,
            ):
                continue

        if side == "ABOVE":
            required = [1, 1]
        elif side == "BELOW":
            required = [-1, -1]
        else:
            required = []

        if moves[-2:] != required:
            reasons.append("Recent momentum not confirmed")

    decision = "SKIP" if reasons else "ENTER"

    explanation = (
        "; ".join(reasons)
        if reasons
        else "All experimental filters passed"
    )

    return decision, explanation, ask, confidence


def grade_pending(store, rows, max_checks=8):
    now = datetime.now(timezone.utc)
    checks = 0

    for row in rows:
        if checks >= max_checks:
            break

        if (
            row.get("decision") != "ENTER"
            or row.get("status") != "pending"
        ):
            continue

        ticker = row.get("ticker")
        side = str(row.get("side") or "").upper()

        if (
            not ticker
            or side not in ("ABOVE", "BELOW")
        ):
            continue

        try:
            close = datetime.fromisoformat(
                str(row.get("close_time")).replace(
                    "Z", "+00:00"
                )
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
                store.settle(ticker, result, side)

        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ):
            continue
