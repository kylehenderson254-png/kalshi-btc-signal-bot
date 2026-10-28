
"""Dual Strong LOCK 2.0 — paper-only Supabase tracker.

Separate from original Strong LOCK and Dual Strong LOCK.
No Kalshi account access. No real orders.
"""

from datetime import datetime, timezone
import requests


API = "https://api.elections.kalshi.com/trade-api/v2"
TABLE = "dual_v2_entries"
VALID_STAGES = ("EARLY_10M", "FINAL_5M")


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
    value = str(value or "").strip().upper()

    if value in ("ABOVE", "YES", "UP", "ABOVE TARGET"):
        return "yes"

    if value in ("BELOW", "NO", "DOWN", "BELOW TARGET"):
        return "no"

    return None


class DualV2Store:
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
                f"Dual V2 Supabase HTTP "
                f"{response.status_code}: "
                f"{response.text[:1000]}"
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
        ticker = str(entry.get("ticker") or "").strip()
        stage = str(entry.get("stage") or "").strip()

        if not ticker:
            raise ValueError("V2 entry is missing ticker")

        if stage not in VALID_STAGES:
            raise ValueError("Invalid V2 entry stage")

        side = normalized_side(entry.get("side"))

        if side is None:
            raise ValueError("Invalid V2 entry side")

        if as_float(entry.get("entry_price")) is None:
            raise ValueError("V2 entry price is missing")

        filters = {
            "ticker": f"eq.{ticker}",
            "stage": f"eq.{stage}",
        }

        existing = self.request(
            "GET",
            params={
                "select": "id",
                **filters,
                "limit": 1,
            },
        )

        if existing:
            return {
                "saved": True,
                "created": False,
                "message": "Already saved",
            }

        payload = {
            "ticker": ticker,
            "stage": stage,
            "market_name": entry.get("market_name")
                           or entry.get("market"),
            "series_ticker": entry.get("series_ticker"),
            "side": side,
            "decision": "ENTER",
            "entry_price": as_float(entry.get("entry_price")),
            "score": as_float(entry.get("score")),
            "reason": entry.get("reason"),
            "observed_at": entry.get("observed_at")
                           or utc_now(),
            "close_time": entry.get("close_time"),
            "status": "pending",
        }

        # The database has a UNIQUE(ticker, stage) constraint.
        # Upsert with ignore-duplicates protects against races.
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=ignore-duplicates,return=minimal",
        }

        response = requests.post(
            f"{self.url}/rest/v1/{TABLE}",
            headers=headers,
            params={"on_conflict": "ticker,stage"},
            json=payload,
            timeout=12,
        )

        if not response.ok:
            raise RuntimeError(
                f"Dual V2 save failed: HTTP "
                f"{response.status_code}: "
                f"{response.text[:1000]}"
            )

        verified = self.request(
            "GET",
            params={
                "select": "id",
                **filters,
                "limit": 1,
            },
        )

        if not verified:
            raise RuntimeError(
                "V2 entry could not be verified in Supabase"
            )

        return {
            "saved": True,
            "created": not bool(existing),
            "message": "Entry saved and verified",
        }

    def update_result(self, ticker, stage, result, status):
        if stage not in VALID_STAGES:
            raise ValueError("Invalid V2 stage")

        if result not in ("yes", "no"):
            raise ValueError("Invalid official result")

        if status not in ("win", "loss"):
            raise ValueError("Invalid grading status")

        return self.request(
            "PATCH",
            params={
                "ticker": f"eq.{ticker}",
                "stage": f"eq.{stage}",
                "status": "eq.pending",
            },
            payload={
                "official_result": result,
                "status": status,
                "graded_at": utc_now(),
            },
        )


def from_secrets(secrets):
    url = secrets.get("SUPABASE_URL")
    key = secrets.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        return None

    return DualV2Store(url, key)


def fetch_official_result(ticker):
    response = requests.get(
        f"{API}/markets/{ticker}",
        timeout=10,
    )

    response.raise_for_status()

    market = response.json().get("market", {})
    result = str(market.get("result") or "").lower()

    if result in ("yes", "no"):
        return result

    return None


def grade_pending(store, rows):
    """Grade pending V2 predictions using official Kalshi results.

    Returns number of entries successfully updated.
    """

    graded = 0
    result_cache = {}

    for row in rows:
        if str(row.get("status") or "").lower() != "pending":
            continue

        ticker = row.get("ticker")
        stage = row.get("stage")

        if not ticker or stage not in VALID_STAGES:
            continue

        side = normalized_side(row.get("side"))

        if side is None:
            continue

        try:
            if ticker not in result_cache:
                result_cache[ticker] = fetch_official_result(ticker)

            official = result_cache[ticker]

            if official is None:
                continue

            status = "win" if side == official else "loss"

            store.update_result(
                ticker, stage, official, status
            )

            graded += 1

        except requests.RequestException:
            # Kalshi unavailable: keep entry pending.
            continue

    return graded


def performance(rows, stage=None, fee=0.02):
    """Calculate V2 paper-only performance.

    Paper P&L assumes one contract per prediction,
    $1 payout for wins, and illustrative flat fees.
    """

    selected = [
        row for row in rows
        if stage is None or row.get("stage") == stage
    ]

    wins = sum(
        str(row.get("status") or "").lower() == "win"
        for row in selected
    )

    losses = sum(
        str(row.get("status") or "").lower() == "loss"
        for row in selected
    )

    pending = sum(
        str(row.get("status") or "").lower() == "pending"
        for row in selected
    )

    total = wins + losses
    win_rate = (wins / total * 100) if total else None

    pnl = 0.0

    for row in selected:
        status = str(row.get("status") or "").lower()
        price = as_float(row.get("entry_price"))

        if status not in ("win", "loss") or price is None:
            continue

        if status == "win":
            pnl += 1.0 - price - fee
        else:
            pnl -= price + fee

    return {
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": win_rate,
        "paper_pnl": round(pnl, 2),
    }
