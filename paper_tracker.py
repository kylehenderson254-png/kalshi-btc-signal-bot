
"""Read-only hypothetical WATCH entry ledger; no actual orders or fills.

Settlement grading uses the recorded ABOVE/BELOW (YES/NO) side and
Kalshi's official YES/NO result. Existing rows with an official_result
are reconciled automatically when history is loaded.
"""

from datetime import datetime, timezone
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"


def normalized_side(side):
    value = str(side or "").strip().lower().replace("_", " ").replace("-", " ")

    if value in ("above", "above target", "yes", "up"):
        return "yes"

    if value in ("below", "below target", "no", "down"):
        return "no"

    return None


def normalized_result(result):
    value = str(result or "").strip().lower()

    return value if value in ("yes", "no") else None


def correct_status(side, outcome):
    side_yes_no = normalized_side(side)
    outcome_yes_no = normalized_result(outcome)

    if not side_yes_no or not outcome_yes_no:
        return None

    return "win" if side_yes_no == outcome_yes_no else "loss"


class PaperStore:
    def __init__(self, url, key):
        self.endpoint = url.rstrip("/") + "/rest/v1/paper_entries"

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
        rows = self.request(
            "GET",
            params={
                "select": "*",
                "order": "observed_at.desc",
                "limit": "1000",
            },
        )

        # Reconcile older records against their stored
        # official Kalshi settlement results.

        for row in rows:
            expected = correct_status(
                row.get("side"),
                row.get("official_result"),
            )

            if expected is None:
                continue

            if row.get("status") not in ("win", "loss"):
                continue

            if row["status"] != expected:
                ticker = row.get("ticker")

                if not ticker:
                    continue

                self.request(
                    "PATCH",
                    params={
                        "ticker": "eq." + str(ticker),
                    },
                    json={
                        "status": expected,
                    },
                    headers={
                        "Prefer": "return=minimal",
                    },
                )

                row["status"] = expected

        return rows

    def record_once(
        self,
        market,
        ticker,
        side,
        ask,
        close_time,
    ):
        row = {
            "ticker": ticker,
            "market_name": market,
            "side": side,
            "entry_price": round(float(ask), 4),
            "observed_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "close_time": (
                close_time.isoformat()
                if close_time
                else None
            ),
            "status": "pending",
        }

        self.request(
            "POST",
            json=row,
            params={
                "on_conflict": "ticker",
            },
            headers={
                "Prefer": (
                    "resolution=ignore-duplicates,"
                    "return=minimal"
                ),
            },
        )

    def settle(self, ticker, outcome):
        result = normalized_result(outcome)

        if result is None:
            return

        # Read the recorded ABOVE/BELOW side first.
        # A YES settlement is not automatically a win.

        matches = self.request(
            "GET",
            params={
                "select": "ticker,side,status",
                "ticker": "eq." + str(ticker),
                "limit": "1",
            },
        )

        if not matches:
            return

        if matches[0].get("status") != "pending":
            return

        status = correct_status(
            matches[0].get("side"),
            result,
        )

        if status is None:
            return

        self.request(
            "PATCH",
            params={
                "ticker": "eq." + str(ticker),
                "status": "eq.pending",
            },
            json={
                "status": status,
                "official_result": result,
            },
            headers={
                "Prefer": "return=minimal",
            },
        )


def from_secrets(secrets):
    url = secrets.get("SUPABASE_URL")
    key = secrets.get("SUPABASE_SERVICE_ROLE_KEY")

    return (
        PaperStore(url, key)
        if url and key
        else None
    )


def official_outcome(ticker):
    response = requests.get(
        f"{API}/markets/{ticker}",
        timeout=12,
    )

    response.raise_for_status()

    market = response.json().get(
        "market", {}
    )

    if str(
        market.get("status", "")
    ).lower() not in (
        "settled",
        "finalized",
    ):
        return None

    return normalized_result(
        market.get("result")
    )


def grade_pending(
    store,
    rows,
    max_checks=8,
):
    now = datetime.now(timezone.utc)
    checks = 0

    for row in rows:
        if row.get("status") != "pending":
            continue

        if checks >= max_checks:
            break

        close_raw = row.get("close_time")

        try:
            close = datetime.fromisoformat(
                str(close_raw).replace(
                    "Z", "+00:00"
                )
            )
        except (TypeError, ValueError):
            continue

        if close.tzinfo is None:
            close = close.replace(
                tzinfo=timezone.utc
            )

        if close > now:
            continue

        checks += 1

        try:
            result = official_outcome(
                row["ticker"]
            )

            if result:
                store.settle(
                    row["ticker"],
                    result,
                )

        except (
            requests.RequestException,
            ValueError,
        ):
            continue


def pnl(
    row,
    fee_per_contract=0.02,
):
    if row.get("status") not in (
        "win",
        "loss",
    ):
        return None

    entry = float(
        row["entry_price"]
    )

    return (
        (
            1.0
            if row["status"] == "win"
            else 0.0
        )
        - entry
        - fee_per_contract
    )
