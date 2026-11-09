
"""QUEEN'S EDGE | 254 — clear signal dashboard + V4 paper tracking.

PAPER ONLY. NO REAL ORDERS.

Requires queens_preview_base.py, queens_original.py,
queens_live_chart.py, and queens_v3_guard.py.

Preserves existing paper entry rules, storage, history, and P&L.
"""
import importlib.util
import inspect
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path

import streamlit as st

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


# ------------------------------------------------------------
# PRESERVE COMPLETE V4 PAPER HISTORY
# ------------------------------------------------------------

def all_paper_rows(self):
    """Read every stored V4 entry, not just the first 200."""
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


# ------------------------------------------------------------
# PRESERVE IMPROVED PAPER P&L
# ------------------------------------------------------------

def clearer_paper_metrics(rows):
    """Separate estimated exits from officially matched P&L."""
    finals = [
        r for r in rows if r.get("final")
    ]
    exited_pending = [
        r for r in rows
        if not r.get("final") and r.get("exit")
    ]
    open_rows = [
        r for r in rows
        if not r.get("final") and not r.get("exit")
    ]

    matched_managed = sum(
        float(r["final"]["managed_pnl"])
        for r in finals
    )
    matched_hold = sum(
        float(r["final"]["hold_pnl"])
        for r in finals
    )
    pending_exit = sum(
        float(r["exit"]["pnl"])
        for r in exited_pending
    )

    a, b = st.columns(2)

    a.metric(
        "Matched managed P&L · official",
        f"${matched_managed:+.2f}",
    )
    b.metric(
        "Matched settlement P&L · official",
        f"${matched_hold:+.2f}",
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
    c.metric("Officially graded", len(finals))

    st.metric(
        "Estimated exited P&L · pending official result",
        f"${pending_exit:+.2f}",
    )

    st.caption(
        "Pending exit P&L uses estimated fills and fees. "
        "It is NOT included in officially matched totals. "
        "No real orders are placed."
    )

    if finals:
        wins = sum(
            float(r["final"]["managed_pnl"]) > 0
            for r in finals
        )
        st.caption(
            f"Officially matched managed wins: "
            f"{wins}/{len(finals)} "
            f"({wins / len(finals):.1%}). "
            "Paper estimates only."
        )


# ------------------------------------------------------------
# PRESERVE FIXED KALSHI QUOTE MAPPING
# ------------------------------------------------------------

def fix_mid_quote_mapping():
    """Correct ABOVE/BELOW lookups in the existing stage cards."""
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


# ------------------------------------------------------------
# NEW: SIMPLE QUEEN'S SIGNAL DISPLAY
# ------------------------------------------------------------

def normalized_direction(value):
    """Interpret recorded ABOVE/BELOW calls conservatively."""
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
    direction,
    reason,
    early,
    locked,
):
    """Presentation only. Never approves or records a trade."""

    ticker = str((market or {}).get("ticker") or "")
    reason_text = str(reason or "")
    lower_reason = reason_text.lower()

    if not ticker.startswith("KXBTC15M-"):
        return (
            "WAIT",
            "No active BTC 15-minute market",
            "#F5C96B",
        )

    if errors or not verified:
        return (
            "DATA STALE",
            "Market quotes are not fully verified",
            "#FF8295",
        )

    if any(
        phrase in lower_reason
        for phrase in (
            "needs refreshing",
            "unavailable",
            "stale",
            "data issue",
            "candle or quote",
        )
    ):
        return (
            "DATA STALE",
            "Waiting for fresh candle and quote data",
            "#FF8295",
        )

    if "no active settlement window" in lower_reason:
        return (
            "WAIT",
            "Market closing or changing rounds",
            "#F5C96B",
        )

    # The base analysis has already verified freshness here.
    # A candidate is still NOT an approved V4 paper entry.
    if "setup detected" in lower_reason:
        if "ABOVE" in reason_text.upper():
            return (
                "EARLY UP",
                "Potential UP setup · entry filters pending",
                "#53E7AB",
            )

        if "BELOW" in reason_text.upper():
            return (
                "EARLY DOWN",
                "Potential DOWN setup · entry filters pending",
                "#FF8295",
            )

    # Display an existing recorded EARLY call.
    # Do not convert it into a confirmed paper entry.
    early_side = normalized_direction(
        early.get("call") if isinstance(early, dict) else None
    )

    if early_side:
        color = (
            "#53E7AB"
            if early_side == "UP"
            else "#FF8295"
        )
        return (
            "EARLY " + early_side,
            "Recorded early call · V4 entry not confirmed",
            color,
        )

    # Existing LOCK records remain visible in the original
    # EARLY / MID / LOCK row. They do not automatically
    # become CONFIRMED paper trades here.

    return (
        "WAIT",
        reason_text or "No qualifying setup yet",
        "#F5C96B",
    )


def queen_signal_card(signal, explanation, color):
    """Large, readable primary signal card."""

    signal = escape(str(signal))
    explanation = escape(str(explanation))

    st.markdown(
        f"""
        <div style="
            background:linear-gradient(
                135deg,#251638,#150d25
            );
            border:1px solid #69438d;
            border-radius:17px;
            padding:19px 12px;
            text-align:center;
            margin:8px 0 12px 0;
        ">
            <div style="
                color:#d8baf3;
                font-size:12px;
                font-weight:800;
                letter-spacing:2px;
            ">
                👑 QUEEN'S SIGNAL
            </div>

            <div style="
                color:{color};
                font-size:clamp(28px,7vw,46px);
                font-weight:900;
                margin:8px 0;
                line-height:1.15;
            ">
                {signal}
            </div>

            <div style="
                color:#e1d2ed;
                font-size:13px;
                line-height:1.5;
            ">
                {explanation}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------
# NEW: CLEAN, READABLE HEADER
# ------------------------------------------------------------

def clear_queen_header(
    market,
    errors,
    quote_fn,
    candle_fn,
    verified,
    early=None,
    locked=None,
):
    """Replace only the visible header, not trading logic."""

    # Reuse the original market analysis and freshness checks.
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
        direction,
        reason,
        early,
        locked,
    )

    # Primary signal first.
    queen_signal_card(signal, explanation, color)

    # Keep existing EARLY / MID / LOCK tracking visible.
    base.qe_stage_row(
        market,
        errors,
        verified,
        early,
        locked,
    )

    close = base.utc(
        (market or {}).get("close_time")
    )

    now = base.now_utc()

    remaining = (
        max(0, int((close - now).total_seconds()))
        if close
        else None
    )

    now_ct = (
        now.astimezone(base.CT)
        .strftime("%I:%M:%S %p")
        .lstrip("0")
    )

    a, b = st.columns(2)

    a.metric(
        "🕒 CENTRAL TIME",
        now_ct,
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

    a, b = st.columns(2)

    a.metric(
        "📈 MARKET DIRECTION",
        direction,
    )

    if signal == "DATA STALE":
        next_move = "REFRESH DATA"
    elif signal.startswith("EARLY"):
        next_move = "MONITOR"
    else:
        next_move = "WAIT"

    b.metric(
        "👑 NEXT MOVE",
        next_move,
    )

    # Display prices without implying they are approved entries.
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

    with st.expander(
        "📊 Market prices & signal details",
        expanded=False,
    ):
        a, b = st.columns(2)

        a.metric(
            "ABOVE · ASK",
            f"{yes * 100:.1f}¢"
            if yes is not None
            else "—",
        )

        b.metric(
            "BELOW · ASK",
            f"{no * 100:.1f}¢"
            if no is not None
            else "—",
        )

        st.write("Market direction:", direction)
        st.write("Queen's signal:", signal)
        st.write("Analysis detail:", reason)

        st.caption(
            "EARLY signals are observations, not approved "
            "paper entries. Recorded LOCK calls are shown "
            "separately. V4 entry approval and paper P&L "
            "remain in Advanced Tools."
        )

    st.caption(
        "👑 Paper observation only · "
        "No real orders · "
        "Signals are not guarantees."
    )


# ------------------------------------------------------------
# APPLY PRESERVED FIXES + NEW PRESENTATION
# ------------------------------------------------------------

base.PaperJournal.rows = all_paper_rows
base.paper_metrics = clearer_paper_metrics

try:
    fix_mid_quote_mapping()
except (OSError, TypeError, RuntimeError) as exc:
    st.error(
        "Compatibility check failed. "
        "No trading logic was changed: " + str(exc)
    )
    st.stop()

# The preview base injects this function into the original
# dashboard. Replace only that display function.
base.qe_simple_header = clear_queen_header


# ------------------------------------------------------------
# LAUNCH EXISTING DASHBOARD
# ------------------------------------------------------------

if __name__ == "__main__":
    base.launch()
