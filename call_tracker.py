"""Read-only Kalshi call locking and official-settlement grading.

Storage: Supabase PostgREST using a server-side secret (never exposed in UI).
Only records a call when an active contract is <= 5 minutes from its close.
"""
from datetime import datetime, timezone
import os
import re
import requests

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
LOCK_SECONDS = 300
SERIES = [("Bitcoin", "KXBTC15M"), ("Gold", "KXGOLD15M"), ("Silver", "KXSILVER15M"), ("WTI Oil", "KXWTI15M")]


def timestamp(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def numeric(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def quote(m, dollars, cents):
    n = numeric(m.get(dollars))
    if n is not None:
        return n
    n = numeric(m.get(cents))
    return n / 100 if n is not None else None


def candlestick_yes_is_at_least_target(m):
    """Verify the official Kalshi 15m Gold/Silver/WTI candlestick YES rule.

    Do not rely on title/strike alone; only accept explicit settlement wording.
    """
    ticker = str(m.get("ticker") or "").upper()
    series_assets = {"KXGOLD15M": "gold", "KXSILVER15M": "silver", "KXWTI15M": "wti oil"}
    series = next((key for key in series_assets if ticker.startswith(key + "-")), None)
    if not series:
        return False
    rules = " ".join(str(m.get(k) or "") for k in ("rules_primary", "rules_secondary"))
    rules = " ".join(rules.lower().split())
    asset = re.escape(series_assets[series])
    return bool(re.search(
        rf"\bif the close price of the 1-minute candlestick for {asset}\b"
        rf".{{0,180}}?\bis at least\s+\$?\d[\d,]*(?:\.\d+)?\b"
        rf".{{0,150}}?\bthen the market resolves to yes\b",
        rules,
    ))


def comparison_yes_is_higher_or_equal(m):
    """Verify a YES result for final >= initial 60-second CF Benchmarks average.

    Match the explicit supplied Kalshi BRTI rule form; do not infer semantics
    from a generic title, a strike, or unrelated asset contract.
    """
    rules = " ".join(str(m.get(k) or "") for k in ("rules_primary", "rules_secondary"))
    rules = " ".join(rules.lower().split())
    return bool(
        "cf benchmarks" in rules
        and re.search(
            r"\bif\b.{0,300}\bsimple average\b.{0,300}"
            r"\bis at least the simple average\b.{0,300}"
            r"\bthen the market resolves to yes\b",
            rules,
        )
    )


def direction_verified(m):
    """Fail closed unless API rules explicitly map YES to above target."""
    rules = " ".join(str(m.get(k) or "") for k in ("rules_primary", "rules_secondary", "yes_sub_title", "yes_subtitle"))
    rules = " ".join(rules.lower().split())
    return comparison_yes_is_higher_or_equal(m) or candlestick_yes_is_at_least_target(m) or bool(
        re.search(r"\byes\s+(?:if|means|wins?\s+if|settles?\s+if)\s+.{0,100}\b(?:above|higher than|greater than|exceeds)\b", rules)
        or re.search(r"\b(?:above|higher than|greater than|exceeds)\b.{0,70}\b(?:settles?\s+yes|results?\s+in\s+yes)\b", rules)
    )


def candidate(m, name, series, now=None):
    """Return immutable prediction payload or (None, reason)."""
    now = now or datetime.now(timezone.utc)
    close = timestamp(m.get("close_time") or m.get("latest_expiration_time"))
    opening = timestamp(m.get("open_time"))
    ticker = m.get("ticker")
    if not ticker or not close:
        return None, "Missing contract ticker or close time"
    if opening and now < opening:
        return None, "Contract not open yet"
    seconds = (close - now).total_seconds()
    if not (0 < seconds <= LOCK_SECONDS):
        return None, "Outside the final 5-minute lock window"
    if not direction_verified(m):
        return None, "ABOVE/BELOW settlement direction not verified"
    bid = quote(m, "yes_bid_dollars", "yes_bid")
    ask = quote(m, "yes_ask_dollars", "yes_ask")
    if bid is None or ask is None or not (0 <= bid <= ask <= 1):
        return None, "Valid YES bid and ask unavailable"
    if ask - bid > 0.15 or (bid == 0 and ask == 1):
        return None, "Quote spread too wide"
    midpoint = (bid + ask) / 2
    if midpoint >= 0.60:
        call = "ABOVE"
    elif midpoint <= 0.40:
        call = "BELOW"
    else:
        return None, "No clear call (40–60¢ neutral zone)"
    return {
        "ticker": ticker, "market_name": name, "series_ticker": series,
        "call": call, "locked_at": now.isoformat(), "close_time": close.isoformat(),
        "yes_bid": round(bid, 5), "yes_ask": round(ask, 5),
        "yes_midpoint": round(midpoint, 5), "method": "yes_midpoint_60_40_v1",
        "status": "pending",
    }, None


class Store:
    def __init__(self, url, key):
        self.url = url.rstrip("/") + "/rest/v1/locked_calls"
        self.headers = {"apikey": key, "Authorization": "Bearer " + key}

    def _request(self, method, **kwargs):
        r = requests.request(method, self.url, headers={**self.headers, **kwargs.pop("headers", {})}, timeout=12, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else []

    def all(self):
        return self._request("GET", params={"select": "*", "order": "locked_at.desc", "limit": "1000"})

    def insert_once(self, row):
        # ticker PRIMARY KEY and ignore-duplicates make this atomic across users/worker.
        return self._request("POST", json=row, headers={"Prefer": "resolution=ignore-duplicates,return=minimal"}, params={"on_conflict": "ticker"})



def store_from_secrets(secrets):
    url = secrets.get("SUPABASE_URL")
    key = secrets.get("SUPABASE_SERVICE_ROLE_KEY")
    return Store(url, key) if url and key else None


def fetch_current(series):
    r = requests.get(f"{API_BASE}/markets", params={"series_ticker": series, "status": "open", "limit": 200}, timeout=12)
    r.raise_for_status()
    markets = r.json().get("markets", [])
    now = datetime.now(timezone.utc)
    active = [m for m in markets if (not timestamp(m.get("open_time")) or timestamp(m.get("open_time")) <= now)
              and (timestamp(m.get("close_time")) or timestamp(m.get("latest_expiration_time")))
              and (timestamp(m.get("close_time")) or timestamp(m.get("latest_expiration_time"))) > now]
    return min(active, key=lambda m: timestamp(m.get("close_time")) or timestamp(m.get("latest_expiration_time"))) if active else None


def get_official_result(ticker):
    r = requests.get(f"{API_BASE}/markets/{ticker}", timeout=12)
    r.raise_for_status()
    m = r.json().get("market", {})
    result = str(m.get("result") or "").strip().lower()
    status = str(m.get("status") or "").strip().lower()
    # Only official settled result; never grade from close, prices, or expired status alone.
    if status not in ("settled", "finalized") or result not in ("yes", "no"):
        return None
    return result


def process(store, current_by_series=None):
    """Scan and grade. Returns human-readable activity, never places trades."""
    events = []
    current_by_series = current_by_series or {}
    for name, series in SERIES:
        try:
            m = current_by_series.get(series) if series in current_by_series else fetch_current(series)
            if m:
                row, reason = candidate(m, name, series)
                if row:
                    store.insert_once(row)
                    events.append(f"{name}: lock window scanned ({row['call']}, if not previously locked)")
                else:
                    events.append(f"{name}: {reason}")
            else:
                events.append(f"{name}: no active contract returned")
        except (requests.RequestException, ValueError) as e:
            events.append(f"{name}: API/storage error: {e}")
    try:
        pending = [r for r in store.all() if r.get("status") == "pending"]
        for row in pending:
            close = timestamp(row.get("close_time"))
            if close and close > datetime.now(timezone.utc):
                continue
            try:
                result = get_official_result(row["ticker"])
                if result is None:
                    continue
                actual = "ABOVE" if result == "yes" else "BELOW"
                status = "win" if row["call"] == actual else "loss"
                store._request("PATCH", params={"ticker": "eq." + row["ticker"], "status": "eq.pending"}, json={
                    "status": status, "settlement_result": result, "graded_at": datetime.now(timezone.utc).isoformat()
                }, headers={"Prefer": "return=minimal"})
                events.append(f"Graded {row['ticker']}: {status.upper()}")
            except (requests.RequestException, ValueError) as e:
                events.append(f"Grading delayed for {row['ticker']}: {e}")
    except (requests.RequestException, ValueError) as e:
        events.append(f"Unable to load pending calls: {e}")
    return events


if __name__ == "__main__":
    store = store_from_secrets(os.environ)
    if not store:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
    for item in process(store):
        print(item)
