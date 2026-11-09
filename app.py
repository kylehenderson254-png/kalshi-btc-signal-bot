
"""QUEEN'S EDGE | 254 — Clear Signals + V4 Paper Tracking.

PAPER ONLY. NO REAL ORDERS.

Requires:
    queens_preview_base.py
    queens_original.py
    queens_live_chart.py
    queens_v3_guard.py

Preserves existing strategy, storage, paper history and P&L.
"""

import importlib.util
import inspect
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path

import streamlit as st


# ============================================================
# LOAD ORIGINAL QUEEN'S EDGE
# ============================================================

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "queens_preview_base.py"

if not BASE.is_file():
    st.error(
        "Missing queens_preview_base.py. "
        "Restore the original preview base file."
    )
    st.stop()

spec = importlib.util.spec_from_file_location(
    "queens_preview_base", BASE
)

base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


# ============================================================
# V4 PAPER HISTORY — PRESERVE ALL ENTRIES
# ============================================================

def all_paper_rows(self):
    """Read all saved V4 entries using pagination."""

    names = []
    offset = 0
    page_size = 200

    while True:
        objects = self.request(
            "POST",
            "/object/list/" + base.BUCKET,
            {
                "prefix": "entries",
                "limit": page_size,
                "offset": offset,
                "sortBy": {
                    "column": "name",
                    "order": "desc",
                },
            },
        )

        if not isinstance(objects, list):
            raise RuntimeError(
                "Unexpected paper journal listing response"
            )

        names.extend(
            item["name"]
            for item in objects
            if isinstance(item, dict)
            and str(item.get("name", "")).endswith(".json")
        )

        if len(objects) < page_size:
            break

        offset += page_size

        if offset >= 10000:
            raise RuntimeError(
                "Paper journal exceeds safe pagination limit"
            )

    def load(key):
        entry = self.get("entries", key)

        if not isinstance(entry, dict):
            raise RuntimeError(
                "Stored paper entry could not be read: " + key
            )

        final = self.get("finals", key)

        return {
            "key": key,
            "entry": entry,
            "final": final,
            "exit": (
                None
                if final
                else self.get("exits", key)
            ),
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(load, names))


# ============================================================
# V4 PAPER P&L — PRESERVE IMPROVED TRACKING
# ============================================================

def clearer_paper_metrics(rows):
    """Separate official results from estimated paper exits."""

    finals = [
        row for row in rows
        if row.get("final")
    ]

    exited_pending = [
        row for row in rows
        if not row.get("final")
        and row.get("exit")
    ]

    open_rows = [
        row for row in rows
        if not row.get("final")
        and not row.get("exit")
    ]

    managed = sum(
        float(row["final"]["managed_pnl"])
        for row in finals
    )

    settlement = sum(
        float(row["final"]["hold_pnl"])
        for row in finals
    )

    pending_exit = sum(
        float(row["exit"]["pnl"])
        for row in exited_pending
    )

    a, b = st.columns(2)

    a.metric(
        "Matched managed P&L · official",
        f"${managed:+.2f}",
    )

    b.metric(
        "Matched settlement P&L · official",
        f"${settlement:+.2f}",
    )

    a.metric(
        "Paper entries / official outcomes",
        f"{len(rows)} / {len(finals)}",
    )

    b.metric(
        "Today · managed realized",
        f"${base.daily_pnl(rows):+.2f}",
    )

    a, b, c = st.columns(3)

    a.metric("Open positions", len(open_rows))

    b.metric(
        "Exited · awaiting settlement",
        len(exited_pending),
    )

    c.metric(
        "Officially graded",
        len(finals),
    )

    st.metric(
        "Estimated exited P&L · pending official result",
        f"${pending_exit:+.2f}",
    )

    st.caption(
        "Pending exit P&L uses estimated fills and fees. "
        "It is not included in officially matched totals. "
        "No real orders are placed."
    )

    if finals:
        wins = sum(
            float(row["final"]["managed_pnl"]) > 0
            for row in finals
        )

        st.caption(
            f"Officially matched managed wins: "
            f"{wins}/{len(finals)} "
            f"({wins / len(finals):.1%}). "
            "Paper estimates only."
        )


# ============================================================
# PRESERVE CORRECT KALSHI ABOVE / BELOW QUOTES
# ============================================================

def fix_mid_quote_mapping():
    """Fix quote mapping without modifying the base file."""

    source = inspect.getsource(base.qe_stage_row)

    replacements = {
        "quote(market, 'yes', 'bid')":
            "quote(market, 'ABOVE', 'bid')",
        "quote(market, 'yes', 'ask')":
            "quote(market, 'ABOVE', 'ask')",
        "quote(market, 'no', 'bid')":
            "quote(market, 'BELOW', 'bid')",
        "quote(market, 'no', 'ask')":
            "quote(market, 'BELOW', 'ask')",
    }

    for before, after in replacements.items():

        if source.count(before) != 1:
            raise RuntimeError(
                "MID quote compatibility check failed: "
                + before
            )

        source = source.replace(before, after)

    exec(
        compile(source, str(BASE), "exec"),
        base.__dict__,
    )


# ============================================================
# QUEEN'S SIMPLE SIGNAL ENGINE — DISPLAY ONLY
# ============================================================

def normalized_direction(value):
    """Translate saved directional calls into UP / DOWN."""

    value = str(value or "").strip().upper()

    if value in ("ABOVE", "UP", "YES"):
        return "UP"

    if value in ("BELOW", "DOWN", "NO"):
        return "DOWN"

    return None


def queen_signal_state(
    market,
    errors,
    verified,
    reason,
    early,
):
    """Interpret existing analysis without approving new trades."""

    ticker = str(
        (market or {}).get("ticker") or ""
    )

    reason_text = str(reason or "")
    reason_lower = reason_text.lower()

    if not ticker.startswith("KXBTC15M-"):
        return (
            "WAIT",
            "Waiting for the next BTC market",
            "#F5C96B",
        )

    close = base.utc(
        (market or {}).get("close_time")
    )

    if close is None:
        return (
            "DATA STALE",
            "Market settlement time unavailable",
            "#FF8295",
        )

    seconds = (
        close - base.now_utc()
    ).total_seconds()

    if seconds <= 0:
        return (
            "WAIT",
            "Market closing or changing rounds",
            "#F5C96B",
        )

    if errors or not verified:
        return (
            "DATA STALE",
            "Market quote verification unavailable",
            "#FF8295",
        )

    stale_phrases = (
        "needs refreshing",
        "unavailable",
        "stale",
        "data issue",
        "candle or quote",
    )

    if any(
        phrase in reason_lower
        for phrase in stale_phrases
    ):
        return (
            "DATA STALE",
            "Waiting for fresh market data",
            "#FF8295",
        )

    # Existing verified V3 candidate detection.
    # This is not V4 paper entry approval.

    if "setup detected" in reason_lower:

        if "ABOVE" in reason_text.upper():
            return (
                "EARLY UP",
                "Bullish setup detected · confirmation pending",
                "#53E7AB",
            )

        if "BELOW" in reason_text.upper():
            return (
                "EARLY DOWN",
                "Bearish setup detected · confirmation pending",
                "#FF8295",
            )

    # Existing saved early calls may be displayed,
    # but are not treated as new confirmed trades.

    early_side = normalized_direction(
        early.get("call")
        if isinstance(early, dict)
        else None
    )

    if early_side == "UP":
        return (
            "EARLY UP",
            "Recorded bullish call · V4 entry not confirmed",
            "#53E7AB",
        )

    if early_side == "DOWN":
        return (
            "EARLY DOWN",
            "Recorded bearish call · V4 entry not confirmed",
            "#FF8295",
        )

    return (
        "WAIT",
        reason_text or "No qualifying setup yet",
        "#F5C96B",
    )


# ============================================================
# FIXED SIGNAL CARD — NO RAW HTML DISPLAY
# ============================================================

def queen_signal_card(signal, explanation, color):
    """Compact Streamlit-compatible signal display."""

    safe_signal = escape(str(signal))
    safe_explanation = escape(str(explanation))

    # One-line HTML avoids Streamlit Markdown interpreting
    # indented multiline HTML as a code block.

    html = (
        '<div style="'
        'background:linear-gradient(135deg,#29183f,#140c24);'
        'border:1px solid #70459a;'
        'border-radius:16px;'
        'padding:20px 12px;'
        'text-align:center;'
        'margin:8px 0 12px 0;'
        '">'
        '<div style="'
        'color:#d8baf3;'
        'font-size:12px;'
        'font-weight:800;'
        'letter-spacing:2px;'
        '">👑 QUEEN&#39;S SIGNAL</div>'
        '<div style="'
        f'color:{color};'
        'font-size:36px;'
        'font-weight:900;'
        'line-height:1.2;'
        'margin:10px 0;'
        f'">{safe_signal}</div>'
        '<div style="'
        'color:#e1d2ed;'
        'font-size:13px;'
        'line-height:1.5;'
        f'">{safe_explanation}</div>'
        '</div>'
    )

    st.markdown(
        html,
        unsafe_allow_html=True,
    )


# ============================================================
# QUEEN'S NEXT MOVE
# ============================================================

def next_move_for(signal):
    """Simple informational guidance, never an order."""

    if signal == "DATA STALE":
        return "CHECK DATA"

    if signal == "EARLY UP":
        return "WATCH UP"

    if signal == "EARLY DOWN":
        return "WATCH DOWN"

    return "WAIT"


# ============================================================
# CLEAN QUEEN'S DASHBOARD HEADER
# ============================================================

def clear_queen_header(
    market,
    errors,
    quote_fn,
    candle_fn,
    verified,
    early=None,
    locked=None,
):
    """Show the answer first, details second."""

    direction, old_signal, reason, fresh = (
        base.qe_simple_status(
            market,
            errors,
            quote_fn,
            candle_fn,
            verified,
        )
    )

    signal, explanation, color = queen_signal_state(
        market,
        errors,
        verified,
        reason,
        early,
    )

    # Main Queen's Signal — large and readable.

    queen_signal_card(
        signal,
        explanation,
        color,
    )

    # Preserve existing EARLY / MID / LOCK records.

    base.qe_stage_row(
        market,
        errors,
        verified,
        early,
        locked,
    )

    # Clock and settlement countdown.

    now = base.now_utc()

    close = base.utc(
        (market or {}).get("close_time")
    )

    remaining = (
        max(
            0,
            int((close - now).total_seconds())
        )
        if close
        else None
    )

    central_time = (
        now.astimezone(base.CT)
        .strftime("%I:%M:%S %p")
        .lstrip("0")
    )

    a, b = st.columns(2)

    a.metric(
        "🕒 CENTRAL TIME",
        central_time,
    )

    b.metric(
        "⏳ SETTLEMENT IN",
        (
            f"{remaining // 60:02d}:"
            f"{remaining % 60:02d}"
            if remaining is not None
            else "—"
        ),
    )

    # Market direction and next move.

    a, b = st.columns(2)

    a.metric(
        "📈 MARKET DIRECTION",
        direction,
    )

    b.metric(
        "👑 NEXT MOVE",
        next_move_for(signal),
    )

    # Technical details stay in the background.

    with st.expander(
        "📊 Market prices & signal details",
        expanded=False,
    ):

        yes = (
            base.quote(fresh, "ABOVE", "ask")
            if fresh
            else None
        )

        no = (
            base.quote(fresh, "BELOW", "ask")
            if fresh
            else None
        )

        a, b = st.columns(2)

        a.metric(
            "ABOVE · ASK",
            (
                f"{yes * 100:.1f}¢"
                if yes is not None
                else "—"
            ),
        )

        b.metric(
            "BELOW · ASK",
            (
                f"{no * 100:.1f}¢"
                if no is not None
                else "—"
            ),
        )

        st.write(
            "Market direction:",
            direction,
        )

        st.write(
            "Queen's signal:",
            signal,
        )

        st.write(
            "Analysis detail:",
            reason,
        )

        st.caption(
            "EARLY calls are observations, not approved "
            "V4 paper entries. Existing LOCK records "
            "remain separate. No real orders."
        )

    st.caption(
        "👑 QUEEN'S EDGE | 254 · "
        "Paper observation only · "
        "Signals are not guarantees."
    )


# ============================================================
# APPLY PATCHES WITHOUT CHANGING BASE STRATEGY
# ============================================================

base.PaperJournal.rows = all_paper_rows

base.paper_metrics = clearer_paper_metrics

try:
    fix_mid_quote_mapping()

except (OSError, TypeError, RuntimeError) as exc:

    st.error(
        "Compatibility check failed. "
        "Original trading logic was not changed: "
        + str(exc)
    )

    st.stop()


# Replace only the original visible header function.
# The base scanner, filters and P&L continue unchanged.

base.qe_simple_header = clear_queen_header


# ============================================================
# LAUNCH QUEEN'S EDGE
# ============================================================

if __name__ == "__main__":
    base.launch()
