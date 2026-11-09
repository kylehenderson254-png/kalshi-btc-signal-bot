
"""QUEEN'S EDGE | 254
Bull & Bear Dashboard — Signal Fix V3

PAPER ONLY. NO REAL ORDERS.

Required existing files:
    queens_preview_base.py
    queens_original.py
    queens_live_chart.py
    queens_v3_guard.py

This wrapper preserves the existing V4 strategy.
"""

import importlib.util
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path

import streamlit as st


# ============================================================
# LOAD EXISTING QUEEN'S EDGE
# ============================================================

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "queens_preview_base.py"

if not BASE.is_file():
    st.error("Missing queens_preview_base.py")
    st.stop()

spec = importlib.util.spec_from_file_location(
    "queens_preview_base", BASE
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


# ============================================================
# COMPLETE SAVED V4 PAPER HISTORY
# ============================================================

def all_paper_rows(self):
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
                "Paper history listing unavailable"
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
                "Paper history pagination limit reached"
            )

    def load(key):
        entry = self.get("entries", key)

        if not isinstance(entry, dict):
            raise RuntimeError(
                "Saved paper entry unavailable: " + key
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
        rows = list(pool.map(load, names))

    st.session_state["qe_paper_rows"] = rows
    return rows


# ============================================================
# OFFICIAL AND ESTIMATED PAPER P&L
# ============================================================

def clearer_paper_metrics(rows):
    finals = [
        r for r in rows
        if r.get("final")
    ]

    pending = [
        r for r in rows
        if r.get("exit") and not r.get("final")
    ]

    open_rows = [
        r for r in rows
        if not r.get("final")
        and not r.get("exit")
    ]

    managed = sum(
        float(r["final"]["managed_pnl"])
        for r in finals
    )

    hold = sum(
        float(r["final"]["hold_pnl"])
        for r in finals
    )

    estimated = sum(
        float(r["exit"]["pnl"])
        for r in pending
    )

    a, b = st.columns(2)

    a.metric(
        "Official managed P&L",
        f"${managed:+.2f}",
    )

    b.metric(
        "Official hold P&L",
        f"${hold:+.2f}",
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

    a.metric("Open", len(open_rows))
    b.metric("Exit pending", len(pending))
    c.metric("Officially graded", len(finals))

    st.caption(
        f"Estimated exit P&L pending settlement: "
        f"${estimated:+.2f}. "
        "Excluded from official totals. "
        "Paper estimates only."
    )

    if finals:
        wins = sum(
            float(r["final"]["managed_pnl"]) > 0
            for r in finals
        )

        st.caption(
            f"Official managed wins: "
            f"{wins}/{len(finals)} "
            f"({wins / len(finals):.1%})"
        )


# ============================================================
# QUOTE MAPPING
# ============================================================

def fix_mid_quote_mapping():
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
# DIRECTION HELPERS
# ============================================================

def normalized_direction(value):
    value = str(value or "").strip().upper()

    if value in ("UP", "ABOVE", "YES"):
        return "UP"

    if value in ("DOWN", "BELOW", "NO"):
        return "DOWN"

    return None


# ============================================================
# SIGNAL FIX V3
# HISTORICAL CALLS ARE NEVER LIVE SIGNALS
# ============================================================

SIGNAL_VERSION = "SIGNAL FIX V3"


def queen_signal_state(
    market, errors, verified, reason, early
):
    """
    Current verified candidate display only.

    Saved EARLY calls remain historical.
    They cannot trigger a live BUY label.
    """

    ticker = str(
        (market or {}).get("ticker") or ""
    )

    reason_text = str(reason or "").strip()
    reason_upper = reason_text.upper()
    reason_lower = reason_text.lower()

    if not ticker.startswith("KXBTC15M-"):
        return (
            "WAIT",
            "Waiting for BTC 15-minute contract",
            "#F5C96B",
        )

    close = base.utc(
        (market or {}).get("close_time")
    )

    if close is None:
        return (
            "DATA STALE",
            "Kalshi settlement time unavailable",
            "#FF8295",
        )

    remaining = (
        close - base.now_utc()
    ).total_seconds()

    if remaining <= 0:
        return (
            "WAIT",
            "Contract closing or rolling over",
            "#F5C96B",
        )

    if errors or not verified:
        return (
            "DATA STALE",
            "Kalshi market verification unavailable",
            "#FF8295",
        )

    if any(
        phrase in reason_lower
        for phrase in (
            "stale",
            "unavailable",
            "needs refreshing",
            "data issue",
            "candle or quote",
        )
    ):
        return (
            "DATA STALE",
            "Waiting for fresh market analysis",
            "#FF8295",
        )

    # Only a fresh setup from the current analysis
    # may appear as an EARLY directional candidate.

    if "setup detected" in reason_lower:
        above = "ABOVE" in reason_upper
        below = "BELOW" in reason_upper

        if above and not below:
            return (
                "EARLY UP",
                "Current bullish candidate · no V4 BUY",
                "#53E7AB",
            )

        if below and not above:
            return (
                "EARLY DOWN",
                "Current bearish candidate · no V4 BUY",
                "#FF8295",
            )

    # No historical early.get("call") fallback.

    return (
        "WAIT",
        "No fresh qualifying candidate",
        "#F5C96B",
    )


# ============================================================
# REAL SAVED V4 PAPER EVENTS
# ============================================================

def recorded_events(rows, ticker):
    events = []

    for row in rows:
        entry = row.get("entry") or {}

        if entry.get("ticker") != ticker:
            continue

        side = normalized_direction(
            entry.get("side")
        )

        stamp = base.utc(
            entry.get("observed_at")
        )

        if not side or stamp is None:
            continue

        events.append({
            "time": int(stamp.timestamp()),
            "ticker": ticker,
            "kind": "BUY",
            "side": side,
            "text": "PAPER BUY " + side,
        })

        exit_record = row.get("exit") or {}
        final = row.get("final") or {}

        reason = str(
            exit_record.get("reason")
            or final.get("exit_reason")
            or ""
        )

        exit_stamp = base.utc(
            exit_record.get("observed_at")
        )

        if (
            not exit_stamp
            and reason != "Held to official settlement"
        ):
            exit_stamp = base.utc(
                final.get("managed_closed_at")
            )

        if (
            exit_stamp
            and reason
            and reason != "Held to official settlement"
        ):
            is_profit = (
                "profit review" in reason.lower()
            )

            events.append({
                "time": int(exit_stamp.timestamp()),
                "ticker": ticker,
                "kind": (
                    "TP" if is_profit else "EXIT"
                ),
                "side": side,
                "text": (
                    "TP REVIEW"
                    if is_profit
                    else "EXIT REVIEW"
                ),
            })

    return sorted(
        events,
        key=lambda e: e["time"],
    )[-80:]


# ============================================================
# COMPACT PAPER POSITION / P&L
# ============================================================

def paper_strip(rows, ticker):
    official = [
        r for r in rows
        if r.get("final")
    ]

    managed = sum(
        float(r["final"]["managed_pnl"])
        for r in official
    )

    open_rows = [
        r for r in rows
        if not r.get("final")
        and not r.get("exit")
        and (r.get("entry") or {}).get("ticker") == ticker
    ]

    pending = [
        r for r in rows
        if r.get("exit") and not r.get("final")
    ]

    a, b, c = st.columns(3)

    a.metric(
        "V4 PAPER POSITION",
        "OPEN" if open_rows else "NONE",
    )

    b.metric(
        "OFFICIAL MANAGED P&L",
        f"${managed:+.2f}",
    )

    c.metric(
        "SETTLED / PENDING",
        f"{len(official)} / {len(pending)}",
    )

    st.caption(
        "BUY / TP / EXIT chart markers represent "
        "saved V4 paper events, not live orders."
    )


# ============================================================
# COMPACT DASHBOARD
# ORIGINAL BULL & BEAR BANNER REMAINS
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
    direction, _, reason, fresh = (
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

    def price(value):
        return (
            f"{value * 100:.1f}¢"
            if value is not None
            else "—"
        )

    def cell(title, value, tone="#e8d6ff"):
        return (
            '<div class="qcell"><small>'
            + escape(str(title))
            + '</small><b style="color:'
            + tone
            + '">'
            + escape(str(value))
            + "</b></div>"
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

    css = """
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

    st.markdown(
        css
        + '<div class="qgrid">'
        + tiles
        + '</div><div class="qsignal">'
        '<small>👑 CURRENT KALSHI SIGNAL</small>'
        '<b style="color:'
        + color
        + '">'
        + escape(signal)
        + '</b><small>'
        + escape(explanation)
        + '</small></div>',
        unsafe_allow_html=True,
    )

    # Version marker makes deployment verification easy.
    st.caption(
        "👑 QUEEN'S EDGE | 254 · " + SIGNAL_VERSION
    )

    ticker = str(
        (market or {}).get("ticker") or ""
    )

    rows = st.session_state.get(
        "qe_paper_rows", []
    )

    paper_strip(rows, ticker)

    with st.expander(
        "EARLY · MID · LOCK history",
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
            "EARLY calls are historical records. "
            "They are not current BUY signals."
        )

    st.caption(
        "Coinbase chart LIVE and Kalshi signal "
        "verification are separate. PAPER ONLY."
    )


# ============================================================
# CHART: RECORDED PAPER BUY / TP / EXIT
# ============================================================

original_compact_html = base.compact_chart_html


def compact_chart_html(html):
    html = original_compact_html(html)

    # Hide duplicate chart summary.
    html = html.replace(
        '<div id="summary">',
        '<div id="summary" style="display:none">',
        1,
    )

    rows = st.session_state.get(
        "qe_paper_rows", []
    )

    events = []

    for row in rows:
        ticker = str(
            (row.get("entry") or {}).get("ticker")
            or ""
        )

        events.extend(
            recorded_events([row], ticker)
        )

    payload = json.dumps(
        events,
        separators=(",", ":"),
    ).replace("<", "\\u003c")

    anchor = "let bars=[];"

    if html.count(anchor) != 1:
        raise RuntimeError(
            "Chart compatibility: candle array missing"
        )

    html = html.replace(
        anchor,
        "const QE_PAPER_EVENTS="
        + payload
        + ";\n"
        + anchor,
        1,
    )

    marker_js = r"""
function qeRecordedMarkers(){
 if(!CONFIG.ticker || !bars.length)return [];

 const candleTimes=new Set(
  bars.map(b=>b.time)
 );

 const result=[];

 for(const e of QE_PAPER_EVENTS){
  if(!e || typeof e.time!=='number')continue;

  if(e.ticker!==CONFIG.ticker)continue;

  const time=Math.floor(e.time/60)*60;

  if(!candleTimes.has(time))continue;

  const up=e.side==='UP';
  const buy=e.kind==='BUY';

  result.push({
   time:time,
   position:buy
    ?(up?'belowBar':'aboveBar')
    :'aboveBar',
   shape:buy
    ?(up?'arrowUp':'arrowDown')
    :'circle',
   color:buy
    ?(up?'#51f0aa':'#ff7896')
    :(e.kind==='TP'
      ?'#ffe16b'
      :'#d6a5ff'),
   text:e.text
  });
 }

 return result.sort(
  (a,b)=>a.time-b.time
 );
}

function qeAllMarkers(){
 return [
  ...qeRecordedMarkers(),
  ...(flags.markers?markers:[])
 ].sort((a,b)=>a.time-b.time);
}
"""

    anchor = (
        "function detectReversals("
        "close,e9,e21,rv,mc){"
    )

    if html.count(anchor) != 1:
        raise RuntimeError(
            "Chart compatibility: reversal function missing"
        )

    html = html.replace(
        anchor,
        marker_js + "\n" + anchor,
        1,
    )

    # Replace only the chart's original marker path.
    before, separator, after = html.partition(
        "function qeAllMarkers(){"
    )

    helper, separator2, remainder = after.partition(
        "function detectReversals("
    )

    original_marker_path = "flags.markers?markers:[]"

    if (
        not separator
        or not separator2
        or remainder.count(original_marker_path) != 1
    ):
        raise RuntimeError(
            "Chart compatibility: marker rendering changed"
        )

    remainder = remainder.replace(
        original_marker_path,
        "qeAllMarkers()",
        1,
    )

    html = (
        before
        + separator
        + helper
        + separator2
        + remainder
    )

    html = html.replace(
        "series.candles?.setMarkers([]);",
        "series.candles?.setMarkers("
        "qeRecordedMarkers());",
    )

    html = html.replace(
        "<span>🟡 Target snapshot</span>",
        '<span>🟡 Target snapshot</span>'
        '<span style="color:#51f0aa">'
        '● PAPER BUY</span>'
        '<span style="color:#ffe16b">'
        '● TP REVIEW</span>'
        '<span style="color:#d6a5ff">'
        '● EXIT REVIEW</span>',
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
 height:365px!important;
 min-height:365px!important;
 flex:none!important;
}
#main{
 height:365px!important;
 min-height:365px!important;
}
#notice{
 margin:6px 0!important;
}
@media(max-width:600px){
 .mainPanel,#main{
  height:330px!important;
  min-height:330px!important;
 }
}
</style>
"""

    return html.replace(
        "</head>",
        css + "</head>",
        1,
    )


# ============================================================
# APPLY WRAPPER PATCHES
# NO V4 STRATEGY CHANGES
# ============================================================

base.PaperJournal.rows = all_paper_rows
base.paper_metrics = clearer_paper_metrics

try:
    fix_mid_quote_mapping()

except (OSError, TypeError, RuntimeError) as exc:
    st.error(
        "Quote compatibility check failed: "
        + str(exc)
    )
    st.stop()

base.qe_simple_header = compact_header
base.compact_chart_html = compact_chart_html


# ============================================================
# START QUEEN'S EDGE
# ============================================================

if __name__ == "__main__":
    base.launch()
