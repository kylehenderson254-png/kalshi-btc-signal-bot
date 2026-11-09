
"""QUEEN'S EDGE | 254
Compact Bull & Bear Dashboard

PAPER ONLY — NO REAL ORDERS.

Required existing files:
    queens_preview_base.py
    queens_original.py
    queens_live_chart.py
    queens_v3_guard.py

Preserves the V4 scanner, filters, saved paper history,
and official-versus-estimated P&L tracking.
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
    "queens_preview_base",
    BASE,
)

base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


# ============================================================
# PRESERVE COMPLETE V4 PAPER HISTORY
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
# PRESERVE V4 PAPER P&L
# ============================================================

def clearer_paper_metrics(rows):
    """Keep official results separate from estimates."""

    finals = [
        row
        for row in rows
        if row.get("final")
    ]

    exited_pending = [
        row
        for row in rows
        if not row.get("final")
        and row.get("exit")
    ]

    open_rows = [
        row
        for row in rows
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

    a.metric(
        "Open positions",
        len(open_rows),
    )

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
# PRESERVE CORRECT KALSHI QUOTE MAPPING
# ============================================================

def fix_mid_quote_mapping():
    """Correct ABOVE/BELOW mapping without editing base."""

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
# SIGNAL INTERPRETATION — DISPLAY ONLY
# ============================================================

def normalized_direction(value):
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
    """Never interpret EARLY as a confirmed V4 entry."""

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
            "Waiting for fresh Kalshi market data",
            "#FF8295",
        )

    if "setup detected" in reason_lower:

        if "ABOVE" in reason_text.upper():
            return (
                "EARLY UP",
                "Bullish candidate · V4 entry not confirmed",
                "#53E7AB",
            )

        if "BELOW" in reason_text.upper():
            return (
                "EARLY DOWN",
                "Bearish candidate · V4 entry not confirmed",
                "#FF8295",
            )

    early_side = normalized_direction(
        early.get("call")
        if isinstance(early, dict)
        else None
    )

    if early_side == "UP":
        return (
            "EARLY UP",
            "Recorded bullish call · not a new BUY",
            "#53E7AB",
        )

    if early_side == "DOWN":
        return (
            "EARLY DOWN",
            "Recorded bearish call · not a new BUY",
            "#FF8295",
        )

    return (
        "WAIT",
        reason_text or "No qualifying setup yet",
        "#F5C96B",
    )


# ============================================================
# COMPACT HEADER
# NO SECOND BANNER
# ============================================================

def compact_header(
    market,
    errors,
    quote_fn,
    candle_fn,
    verified,
    early=None,
    locked=None,
):
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

    close = base.utc(
        (market or {}).get("close_time")
    )

    seconds = (
        max(
            0,
            int(
                (close - base.now_utc()).total_seconds()
            ),
        )
        if close
        else None
    )

    above = (
        base.quote(fresh, "ABOVE", "ask")
        if fresh
        else None
    )

    below = (
        base.quote(fresh, "BELOW", "ask")
        if fresh
        else None
    )

    target = base.number(
        (market or {}).get("floor_strike")
    )

    def price(p):
        return (
            f"{p * 100:.1f}¢"
            if p is not None
            else "—"
        )

    def cell(title, value, tone="#e8d6ff"):
        return (
            '<div class="qcell">'
            '<small>'
            + escape(str(title))
            + '</small>'
            '<b style="color:'
            + tone
            + '">'
            + escape(str(value))
            + '</b></div>'
        )

    tiles = (
        cell(
            "KALSHI TARGET",
            f"${target:,.2f}"
            if target is not None
            else "—",
        )
        + cell(
            "SETTLEMENT",
            (
                f"{seconds // 60:02d}:"
                f"{seconds % 60:02d}"
                if seconds is not None
                else "—"
            ),
        )
        + cell(
            "UP · ASK",
            price(above),
            "#63edaf",
        )
        + cell(
            "DOWN · ASK",
            price(below),
            "#ff8295",
        )
    )

    style = """
<style>
.qgrid{
 display:grid;
 grid-template-columns:repeat(4,minmax(0,1fr));
 gap:7px;
 margin:6px 0;
}
.qcell{
 background:#1c102e;
 border:1px solid #563678;
 border-radius:10px;
 padding:9px 5px;
 text-align:center;
 min-width:0;
}
.qcell small{
 display:block;
 font-size:10px;
 color:#bca5d6;
 font-weight:700;
}
.qcell b{
 display:block;
 font-size:clamp(13px,2vw,20px);
 margin-top:5px;
 overflow-wrap:anywhere;
}
.qsignal{
 background:#1c102e;
 border:1px solid #78469c;
 border-radius:12px;
 padding:11px;
 text-align:center;
 margin:8px 0;
}
.qsignal b{
 display:block;
 font-size:clamp(22px,3vw,30px);
 line-height:1.3;
}
.qsignal small{
 color:#c9b5dc;
}
@media(max-width:550px){
 .qgrid{
  grid-template-columns:repeat(2,minmax(0,1fr));
 }
}
</style>
"""

    html = (
        style
        + '<div class="qgrid">'
        + tiles
        + '</div>'
        '<div class="qsignal">'
        '<small>👑 CURRENT KALSHI SIGNAL</small>'
        '<b style="color:'
        + color
        + '">'
        + escape(signal)
        + '</b>'
        '<small>'
        + escape(explanation)
        + '</small></div>'
    )

    st.markdown(
        html,
        unsafe_allow_html=True,
    )

    # Preserve records, but hide the extra row by default.
    with st.expander(
        "EARLY · MID · LOCK details",
        expanded=False,
    ):
        base.qe_stage_row(
            market,
            errors,
            verified,
            early,
            locked,
        )

        st.caption(
            "EARLY recorded calls are not V4 BUY entries. "
            "Settlement LOCK is tracked separately."
        )

    st.caption(
        "Coinbase chart LIVE does not mean Kalshi "
        "quotes are verified. PAPER ONLY."
    )


# ============================================================
# COMPACT LIVE CHART
# ============================================================

original_compact_html = base.compact_chart_html


def compact_chart_html(html):
    """Remove duplicate summary and fix iframe layout."""

    html = original_compact_html(html)

    # The outer dashboard already provides the summary.
    html = html.replace(
        '<div id="summary">',
        '<div id="summary" style="display:none">',
        1,
    )

    css = """
<style>
html,body{
 height:auto!important;
 min-height:0!important;
 overflow-x:hidden;
}
body{
 display:block!important;
}
#summary{
 display:none!important;
}
#toolbar{
 position:relative!important;
 top:auto!important;
 z-index:1!important;
}
.mainPanel{
 height:350px!important;
 min-height:350px!important;
 flex:none!important;
}
#main{
 height:350px!important;
 min-height:350px!important;
}
#notice{
 margin:6px 0!important;
}
.qe-notes{
 font-size:10px;
}
@media(max-width:600px){
 .mainPanel,#main{
  height:300px!important;
  min-height:300px!important;
 }
}
</style>
"""

    html = html.replace(
        "</head>",
        css + "</head>",
        1,
    )

    return html


# ============================================================
# APPLY DISPLAY PATCHES
# V4 SCANNER AND STRATEGY REMAIN UNCHANGED
# ============================================================

base.PaperJournal.rows = all_paper_rows

base.paper_metrics = clearer_paper_metrics

try:
    fix_mid_quote_mapping()

except (OSError, TypeError, RuntimeError) as exc:

    st.error(
        "Quote compatibility check failed. "
        "Original trading logic was not changed: "
        + str(exc)
    )

    st.stop()


base.qe_simple_header = compact_header

base.compact_chart_html = compact_chart_html


# ============================================================
# LAUNCH QUEEN'S EDGE
# ============================================================

if __name__ == "__main__":
    base.launch()
