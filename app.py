"""QUEEN'S EDGE | 254 — Kalshi dual paper strategy dashboard.

Replace Kalshi app.py with this file. Does NOT transmit orders.
Original EARLY/LOCK settlement predictions remain in their original Supabase tables.
Separate hypothetical $5 positions use append-only objects in the existing
private Supabase Storage bucket queens-edge-paper-v4.

IMPORTANT: Observed public quotes are NOT executable fills. The application
only monitors while an actual Streamlit session is running. An unobserved
cash-out target is never claimed to have filled.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo
from html import escape
import json
import math
import requests
import pandas as pd
import streamlit as st

from call_tracker import (
    fetch_current, candidate, early_candidate, get_official_result,
    store_from_secrets, early_store_from_secrets, timestamp,
    direction_verified,
)
from queens_live_chart import render_live_chart

SERIES = "KXBTC15M"
BUCKET = "queens-edge-paper-v4"  # Existing private bucket from prior paper engine.
CT = ZoneInfo("America/Chicago")
ROOT = Path(__file__).resolve().parent
BUDGET = 5.00                  # Separate paper $5 budgets per strategy.
ENTRY_MIN = 0.45
ENTRY_MAX = 0.75
MAX_SPREAD = 0.04
MODEL_FEE = 0.02                # Illustrative all-in fee PER CONTRACT, not actual Kalshi fees.
TARGET_RETURN = 0.20           # Net return on modeled cost.
STOP_RETURN = -0.20            # Exit when observed net return falls below this level.
REVIEW_SECONDS = 30           # Attempt cash exit near expiration if observable.

st.set_page_config(page_title="QUEEN'S EDGE | 254 · KALSHI", page_icon="👑",
                   layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
.stApp{background:radial-gradient(ellipse at top,#21113f 0%,#0b091a 57%,#05050c 100%);color:#eee9ff}
.block-container{max-width:1480px;padding:.55rem 1rem 1rem}
h1,h2,h3{color:#dfc7ff!important}
.qe-card{background:linear-gradient(145deg,#281638,#140d23);border:1px solid #65438f;border-radius:13px;padding:11px 7px;text-align:center}
.qe-label{font-size:.78rem;font-weight:800;color:#bfa9dd}
.qe-value{font-size:clamp(1.0rem,2.6vw,1.7rem);font-weight:900;margin:6px 0}
.qe-note{font-size:.72rem;color:#b7a7cc;overflow-wrap:anywhere}
.qe-up{color:#47e3a9}.qe-down{color:#ff7c9c}.qe-wait{color:#ffd27e}
[data-testid="stMetric"]{background:#20132f;border:1px solid #4b326b;border-radius:12px;padding:8px}
[data-testid="stMetricValue"]{color:#f5eaff}
</style>""", unsafe_allow_html=True)


def num(x):
    try:
        value = float(x)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def now():
    return datetime.now(timezone.utc)


def price(m, side, kind):
    key = "yes" if side == "ABOVE" else "no"
    value = num(m.get(f"{key}_{kind}_dollars"))
    if value is None:
        cents = num(m.get(f"{key}_{kind}"))
        value = cents / 100 if cents is not None else None
    return value if value is not None and 0 <= value <= 1 else None


def target(m):
    for key in ("floor_strike", "functional_strike", "custom_strike", "cap_strike"):
        value = num(m.get(key))
        if value is not None and value > 0:
            return value
    return None


def load_market():
    try:
        return fetch_current(SERIES), None
    except requests.RequestException:
        return None, "Kalshi connection temporarily unavailable"


def store_for(factory):
    try:
        return factory(st.secrets)
    except Exception:
        return None


def btc_rows(items):
    return [r for r in items if str(r.get("ticker") or "").upper().startswith(SERIES + "-")]


def safe_rows(store):
    if store is None:
        return [], False
    try:
        return btc_rows(store.all()), True
    except requests.RequestException:
        return [], False


def record_and_grade(store, market, builder):
    """Keep pre-existing market-implied prediction and official grade rules."""
    new, _ = builder(market, "Bitcoin", SERIES)
    if new:
        store.insert_once(new)
    for row in btc_rows(store.all()):
        if str(row.get("status") or "").lower() != "pending":
            continue
        close = timestamp(row.get("close_time"))
        if close is None or close > now():
            continue
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        try:
            result = get_official_result(ticker)
            if result not in ("yes", "no"):
                continue
            outcome = "win" if str(row.get("call") or "").upper() == ("ABOVE" if result == "yes" else "BELOW") else "loss"
            store._request("PATCH", params={"ticker": "eq." + ticker, "status": "eq.pending"},
                           json={"status": outcome, "settlement_result": result, "graded_at": now().isoformat()},
                           headers={"Prefer": "return=minimal"})
        except requests.RequestException:
            continue


class PaperJournal:
    """Immutable per-round entry and exit events. NO order or account endpoints."""

    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/storage/v1"
        self.headers = {"apikey": key, "Authorization": "Bearer " + key}

    def request(self, method, path, payload=None, missing=False):
        resp = requests.request(method, self.base + path, timeout=12,
                                headers={**self.headers, "Content-Type": "application/json",
                                         "Cache-Control": "no-cache, no-store", "x-upsert": "false"},
                                json=payload)
        if missing and resp.status_code == 404:
            return None
        if resp.status_code == 409:
            return {"duplicate": True}
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def ready(self):
        bucket = self.request("GET", "/bucket/" + BUCKET, missing=True)
        return isinstance(bucket, dict) and bucket.get("public") is False

    def read(self, folder, ticker):
        key = ticker + ".json"
        result = self.request("GET", f"/object/authenticated/{BUCKET}/{folder}/{key}", missing=True)
        return result if isinstance(result, dict) else None

    def once(self, folder, ticker, payload):
        """Append-only; reread remote to handle duplicates and verify persistence."""
        self.request("POST", f"/object/{BUCKET}/{folder}/{ticker}.json", payload)
        saved = self.read(folder, ticker)
        if not isinstance(saved, dict):
            raise RuntimeError("Paper entry/exit was not verified in Supabase")
        return saved

    def list_events(self, folder):
        # Storage object/list returns names relative to the prefix.
        items = self.request("POST", "/object/list/" + BUCKET, {
            "prefix": folder, "limit": 1000, "offset": 0,
            "sortBy": {"column": "name", "order": "desc"},
        })
        if not isinstance(items, list):
            raise RuntimeError("Cannot list paper events")
        rows = []
        for item in items:
            filename = str(item.get("name") or "")
            if not filename.startswith(SERIES + "-") or not filename.endswith(".json"):
                continue
            record = self.read(folder, filename[:-5])
            if record:
                rows.append(record)
        return rows


def journal_from_secrets():
    try:
        url = st.secrets.get("SUPABASE_URL")
        key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return None, "Private paper journal not configured"
        journal = PaperJournal(str(url), str(key))
        if not journal.ready():
            return None, "Existing private paper bucket missing; paper trades paused"
        return journal, None
    except (requests.RequestException, ValueError, TypeError):
        return None, "Paper journal unavailable; paper trades paused"


def qty_at(ask):
    """$5 total, including fee estimate (per contract)."""
    if ask is None or ask <= 0:
        return 0
    return max(0, int(BUDGET / (ask + MODEL_FEE)))


def entry_snapshot(market, ticker, call, strategy):
    if call not in ("ABOVE", "BELOW") or not direction_verified(market):
        return None, "Direction unavailable"
    ask = price(market, call, "ask")
    bid = price(market, call, "bid")
    if ask is None or bid is None or not (0 < bid <= ask < 1):
        return None, "Valid side quotes unavailable"
    if ask - bid > MAX_SPREAD:
        return None, "Spread too wide"
    if not (ENTRY_MIN <= ask <= ENTRY_MAX):
        return None, "Price outside 45–75¢ entry range"
    quantity = qty_at(ask)
    if quantity < 1:
        return None, "Insufficient modeled budget"
    return {
        "ticker": ticker, "strategy": strategy, "side": call,
        "observed_at": now().isoformat(), "ask": ask, "bid": bid,
        "qty": quantity, "assumed_fee_each": MODEL_FEE,
        "modeled_cost": round(quantity * (ask + MODEL_FEE), 5),
        "target_net_return": TARGET_RETURN,
        "status": "PAPER_OBSERVED_ENTRY",
    }, None


def pnl_at_bid(entry, bid):
    qty = int(entry["qty"])
    cost = float(entry["modeled_cost"])
    # Modeled total round-trip fee assigned at entry, not charged a second time.
    pnl = qty * bid - cost
    return round(pnl, 5), pnl / cost if cost > 0 else None


def paper_exit_for(strategy, entry, market, seconds_left):
    """Only close on an actually observed valid current bid; never manufacture a fill."""
    side = entry["side"]
    bid = price(market, side, "bid")
    ask = price(market, side, "ask")
    if bid is None or ask is None or not (0 <= bid <= ask <= 1):
        return None, "No verified exit quote"
    value, roi = pnl_at_bid(entry, bid)
    if roi is None:
        return None, "No return calculation"
    reason = None
    if strategy == "early":
        if roi >= TARGET_RETURN:
            reason = "20% net target observed"
        elif roi <= STOP_RETURN:
            reason = "20% model stop observed"
        elif seconds_left is not None and seconds_left <= REVIEW_SECONDS:
            reason = "Expiry protection: observed bid"
    # The settlement LOCK position is intentionally NOT cash-exited.
    if reason is None:
        return None, f"HOLD · observed net {roi:+.1%}"
    return {
        "ticker": entry["ticker"], "strategy": strategy,
        "observed_at": now().isoformat(), "exit_bid": bid,
        "modeled_pnl": value, "modeled_roi": round(roi, 6),
        "reason": reason, "kind": "OBSERVED_BID_EXIT",
    }, reason


def settle_if_official(entry):
    ticker = entry.get("ticker")
    close = timestamp(entry.get("close_time"))
    if not ticker or (close and close > now()):
        return None
    official = get_official_result(ticker)
    if official not in ("yes", "no"):
        return None
    correct = (official == "yes" and entry["side"] == "ABOVE") or (official == "no" and entry["side"] == "BELOW")
    qty = int(entry["qty"])
    cost = float(entry["modeled_cost"])
    pnl = round((qty if correct else 0) - cost, 5)
    return {"ticker": ticker, "strategy": entry["strategy"],
            "observed_at": now().isoformat(), "kind": "OFFICIAL_SETTLEMENT_MODEL",
            "official_result": official, "modeled_pnl": pnl,
            "modeled_roi": round(pnl / cost, 6) if cost else None,
            "reason": "Official settlement modeled; no sale observed"}


def update_paper(journal, market, seconds_left):
    """One independent entry and exit max per ticker per strategy."""
    ticker = str(market.get("ticker") or "")
    if not ticker:
        return ["Missing contract identity"]
    notes = []
    for strategy, builder in (("early", early_candidate), ("lock", candidate)):
        folder = "qe_dual_" + strategy
        entry_dir = folder + "_entries"
        exit_dir = folder + "_exits"
        existing = journal.read(entry_dir, ticker)
        closed = journal.read(exit_dir, ticker)
        if existing is None:
            original_call, reason = builder(market, "Bitcoin", SERIES)
            if original_call:
                payload, why = entry_snapshot(market, ticker, original_call["call"], strategy)
                if payload:
                    payload["close_time"] = original_call["close_time"]
                    existing = journal.once(entry_dir, ticker, payload)
                    notes.append(strategy.upper() + " paper entry observed")
                else:
                    notes.append(strategy.upper() + " skip · " + str(why))
            # WAIT is not a fake entry.
        if existing is not None and closed is None:
            if strategy == "early" and seconds_left is not None and seconds_left > 0:
                exit_payload, _ = paper_exit_for(strategy, existing, market, seconds_left)
                if exit_payload:
                    journal.once(exit_dir, ticker, exit_payload)
                    notes.append("EARLY observed exit")
    return notes


def settle_pending_paper(journal, entries, exits):
    """Only when official settlement is available; read-only market API."""
    exit_map = {(r.get("strategy"), r.get("ticker")) for r in exits}
    for entry in entries:
        identity = (entry.get("strategy"), entry.get("ticker"))
        if identity in exit_map:
            continue
        try:
            settled = settle_if_official(entry)
            if settled:
                journal.once("qe_dual_" + entry["strategy"] + "_exits", entry["ticker"], settled)
        except (requests.RequestException, RuntimeError, ValueError):
            continue


def records(journal):
    early = journal.list_events("qe_dual_early_entries")
    lock = journal.list_events("qe_dual_lock_entries")
    exits = journal.list_events("qe_dual_early_exits") + journal.list_events("qe_dual_lock_exits")
    return early + lock, exits


def performance(entries, exits, strategy):
    match = [e for e in entries if e.get("strategy") == strategy]
    exit_map = {(e.get("strategy"), e.get("ticker")): e for e in exits}
    done = [exit_map[(strategy, e["ticker"])] for e in match if (strategy, e.get("ticker")) in exit_map]
    values = [num(e.get("modeled_pnl")) for e in done]
    values = [v for v in values if v is not None]
    wins = sum(p > 0 for p in values)
    losses = sum(p < 0 for p in values)
    return {"Strategy": "Early Cash-Out" if strategy == "early" else "Settlement LOCK",
            "Completed": len(values), "Open": len(match) - len(done),
            "Wins": wins, "Losses": losses,
            "Win rate": f"{wins / len(values):.1%}" if values else "—",
            "Paper P&L": f"${sum(values):+.2f}" if values else "—"}


def call_time(row):
    if row is None:
        return "Waiting for call"
    moment = timestamp(row.get("locked_at"))
    return moment.astimezone(CT).strftime("%I:%M:%S %p CT") if moment else "Time unavailable"


def card(label, call, note):
    call = str(call or "WAIT").upper()
    if call not in ("ABOVE", "BELOW"):
        call = "WAIT"
    css = "qe-up" if call == "ABOVE" else "qe-down" if call == "BELOW" else "qe-wait"
    st.markdown(f'<div class="qe-card"><div class="qe-label">{escape(label)}</div>'
                f'<div class="qe-value {css}">{escape(call)}</div>'
                f'<div class="qe-note">{escape(note)}</div></div>', unsafe_allow_html=True)


def prediction_stats(rows):
    w = sum(str(r.get("status") or "").lower() == "win" for r in rows)
    l = sum(str(r.get("status") or "").lower() == "loss" for r in rows)
    return w, l, (f"{w/(w+l):.1%}" if w+l else "—")


banner = next((p for p in (ROOT / "queens_banner.jpg", ROOT / "queens_banner.png") if p.is_file()), None)
if banner:
    st.image(str(banner), use_container_width=True)
else:
    st.title("👑 QUEEN'S EDGE | 254")
st.caption("KALSHI · BTC 15M · DUAL PAPER STRATEGY · NO REAL ORDERS")
market, error = load_market()
chart_ticker = str(market.get("ticker") or "") if market else ""
if market:
    strike = target(market)
    end = timestamp(market.get("close_time") or market.get("latest_expiration_time"))
    st.markdown("#### 📈 LIVE BITCOIN")
    if strike is not None and end is not None:
        try:
            render_live_chart(strike, close_time=end, market_ticker=chart_ticker)
        except Exception:
            st.warning("Live chart unavailable. Paper histories remain intact.")
    else:
        st.warning("Kalshi target/contract end unavailable; chart target withheld.")
else:
    st.warning(error or "No active Kalshi BTC contract")


@st.fragment(run_every="10s")
def dashboard():
    market, err = load_market()
    if market is None:
        st.warning(err or "Waiting for Kalshi contract")
        return
    ticker = str(market.get("ticker") or "")
    if ticker != chart_ticker:
        st.rerun()  # Chart only rebuilt when a new contract opens.
    close = timestamp(market.get("close_time") or market.get("latest_expiration_time"))
    seconds = max(0, int((close - now()).total_seconds())) if close else None

    st.markdown("#### 👑 CURRENT CONTRACT")
    st.caption(ticker)
    a, b, c, d = st.columns(4)
    a.metric("Settlement", f"{seconds // 60:02d}:{seconds % 60:02d}" if seconds is not None else "—")
    target_value = target(market)
    b.metric("Kalshi target", f"${target_value:,.2f}" if target_value is not None else "—")
    up = price(market, "ABOVE", "ask")
    down = price(market, "BELOW", "ask")
    c.metric("ABOVE ask", f"{100 * up:.1f}¢" if up is not None else "—")
    d.metric("BELOW ask", f"{100 * down:.1f}¢" if down is not None else "—")

    alerts = []
    early_store = store_for(early_store_from_secrets)
    lock_store = store_for(store_from_secrets)
    for label, store, builder in (("EARLY", early_store, early_candidate), ("LOCK", lock_store, candidate)):
        if store is None:
            alerts.append(label + " prediction journal unavailable")
            continue
        try:
            record_and_grade(store, market, builder)
        except (requests.RequestException, RuntimeError):
            alerts.append(label + " recording delayed")
    early_rows, early_ok = safe_rows(early_store)
    lock_rows, lock_ok = safe_rows(lock_store)
    early_row = next((r for r in early_rows if r.get("ticker") == ticker), None)
    lock_row = next((r for r in lock_rows if r.get("ticker") == ticker), None)

    mid = "WAIT"
    if direction_verified(market):
        bid, ask = price(market, "ABOVE", "bid"), price(market, "ABOVE", "ask")
        if bid is not None and ask is not None and bid <= ask and ask - bid <= .15:
            midpoint = (bid + ask) / 2
            mid = "ABOVE" if midpoint >= .60 else "BELOW" if midpoint <= .40 else "WAIT"
    st.markdown("#### 👑 EARLY · MID · LOCK")
    x, y, z = st.columns(3)
    with x:
        card("EARLY · RECORDED", early_row.get("call") if early_row else "WAIT", call_time(early_row))
    with y:
        card("MID · INDICATIVE", mid, "Not a graded prediction")
    with z:
        card("LOCK · RECORDED", lock_row.get("call") if lock_row else "WAIT", call_time(lock_row))

    journal, reason = journal_from_secrets()
    entries, exits = [], []
    if journal:
        try:
            update_paper(journal, market, seconds)
            entries, exits = records(journal)
            # Only process official settlements; never invent missing cash-out quotes.
            settle_pending_paper(journal, entries, exits)
            entries, exits = records(journal)
        except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
            alerts.append("Paper engine delayed: " + str(exc)[:100])
    else:
        alerts.append(reason)

    st.markdown("#### ⚡ DUAL PROFIT ENGINE · PAPER ONLY")
    exit_map = {(e.get("strategy"), e.get("ticker")): e for e in exits}
    cols = st.columns(2)
    for strategy, column in (("early", cols[0]), ("lock", cols[1])):
        with column:
            title = "⚡ EARLY CASH-OUT" if strategy == "early" else "🔒 SETTLEMENT LOCK"
            entry = next((e for e in entries if e.get("strategy") == strategy and e.get("ticker") == ticker), None)
            end_event = exit_map.get((strategy, ticker))
            st.markdown("**" + title + "**")
            if end_event:
                value = num(end_event.get("modeled_pnl"))
                st.metric("Closed paper P&L", f"${value:+.2f}" if value is not None else "—")
                st.caption(str(end_event.get("reason") or "Closed"))
            elif entry:
                sell = price(market, entry["side"], "bid")
                if sell is None:
                    st.metric("OPEN · " + entry["side"], "No bid")
                else:
                    pnl, roi = pnl_at_bid(entry, sell)
                    st.metric("OPEN · " + entry["side"], f"${pnl:+.2f}")
                    st.caption(f"Observed net at bid: {roi:+.1%} · no sale yet")
                st.caption(f"Entry: {entry['ask'] * 100:.1f}¢ · {entry['qty']} contracts · $5 max")
            else:
                st.metric("Status", "WAIT")
                st.caption("45–75¢ + spread ≤4¢ + qualifying original signal")

    st.markdown("#### 📊 ONE RESULTS TRACKER")
    summary = []
    for stage, rows, ok in (("EARLY", early_rows, early_ok), ("LOCK", lock_rows, lock_ok)):
        w, l, rate = prediction_stats(rows)
        summary.append({"Record": stage + " prediction", "Wins": w if ok else "—",
                        "Losses": l if ok else "—", "Win rate": rate if ok else "—"})
    st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)
    if journal and not alerts:
        st.dataframe(pd.DataFrame([performance(entries, exits, "early"), performance(entries, exits, "lock")]),
                     use_container_width=True, hide_index=True)
    elif entries or exits:
        st.dataframe(pd.DataFrame([performance(entries, exits, "early"), performance(entries, exits, "lock")]),
                     use_container_width=True, hide_index=True)
    else:
        st.caption("Separate paper P&L will appear once the private journal is available and live entries are recorded.")

    recent = []
    for entry in entries:
        key = (entry.get("strategy"), entry.get("ticker"))
        end_event = exit_map.get(key)
        amount = num(end_event.get("modeled_pnl")) if end_event else None
        recent.append({"Time UTC": entry.get("observed_at"), "Strategy": entry.get("strategy"),
                       "Contract": entry.get("ticker"), "Side": entry.get("side"),
                       "Entry ask": f"{100 * float(entry['ask']):.1f}¢",
                       "Status": str(end_event.get("kind")) if end_event else "OPEN",
                       "Paper P&L": f"${amount:+.2f}" if amount is not None else "—"})
    if recent:
        recent.sort(key=lambda r: str(r["Time UTC"]), reverse=True)
        st.dataframe(pd.DataFrame(recent[:12]), hide_index=True, use_container_width=True)
    st.caption("Paper estimates use observed indicative quotes and a 2¢/contract illustrative round-trip fee. "
               "Not executable fills. The app monitors only while a Streamlit session is active; "
               "unseen targets are never counted as cash-outs. MID is ungraded. No real orders.")
    if alerts:
        st.warning(" · ".join(dict.fromkeys(alerts)))


dashboard()
