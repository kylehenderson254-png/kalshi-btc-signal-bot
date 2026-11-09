
"""QUEEN'S EDGE | 254
COMPACT V5 — DIRECT CHART FEED RECOVERY

PAPER ONLY. NO REAL ORDERS.

Preserves:
- Bull / bear dashboard branding
- Compact layout and signal protection
- V4 paper scanner and entry filters
- Official paper P&L and history
- Saved BUY / TP / EXIT chart markers
- EMA 9 / EMA 21 and WATCH reversal arrows

Coinbase spot is indicative, not Kalshi settlement data.
"""

import importlib.util
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# LOAD ORIGINAL QUEEN'S EDGE
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

VERSION = "COMPACT V5 · SIGNAL FIX V3 · FEED RECOVERY"


# ============================================================
# COMPLETE PAPER JOURNAL
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
                "Paper journal listing unavailable"
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
                "Paper journal pagination limit"
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
# PAPER PERFORMANCE
# ============================================================

def clearer_paper_metrics(rows):
    finals = [
        r for r in rows if r.get("final")
    ]

    pending = [
        r for r in rows
        if r.get("exit") and not r.get("final")
    ]

    open_rows = [
        r for r in rows
        if not r.get("final") and not r.get("exit")
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
        f"Estimated pending exit P&L: "
        f"${estimated:+.2f}. "
        "Excluded from official totals. "
        "Paper estimates only."
    )


# ============================================================
# CORRECT ABOVE / BELOW QUOTE MAPPING
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
                "Quote mapping compatibility failed: "
                + before
            )

        source = source.replace(before, after)

    exec(
        compile(source, str(BASE), "exec"),
        base.__dict__,
    )


# ============================================================
# SIGNAL FIX V3
# NEVER PROMOTE HISTORICAL EARLY CALLS
# ============================================================

def normalized_direction(value):
    value = str(value or "").strip().upper()

    if value in ("UP", "ABOVE", "YES"):
        return "UP"

    if value in ("DOWN", "BELOW", "NO"):
        return "DOWN"

    return None


def queen_signal_state(
    market, errors, verified, reason, early
):
    ticker = str(
        (market or {}).get("ticker") or ""
    )

    reason_text = str(reason or "").strip()
    reason_lower = reason_text.lower()
    reason_upper = reason_text.upper()

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
            "Settlement time unavailable",
            "#FF8295",
        )

    if (close - base.now_utc()).total_seconds() <= 0:
        return (
            "WAIT",
            "Contract closing or rolling over",
            "#F5C96B",
        )

    if errors or not verified:
        return (
            "DATA STALE",
            "Kalshi verification unavailable",
            "#FF8295",
        )

    stale_phrases = (
        "stale",
        "unavailable",
        "needs refreshing",
        "data issue",
        "candle or quote",
    )

    if any(
        phrase in reason_lower
        for phrase in stale_phrases
    ):
        return (
            "DATA STALE",
            "Waiting for fresh market analysis",
            "#FF8295",
        )

    if "setup detected" in reason_lower:
        above = "ABOVE" in reason_upper
        below = "BELOW" in reason_upper

        if above and not below:
            return (
                "EARLY UP",
                "Current candidate · no V4 BUY",
                "#53E7AB",
            )

        if below and not above:
            return (
                "EARLY DOWN",
                "Current candidate · no V4 BUY",
                "#FF8295",
            )

    return (
        "WAIT",
        "No fresh qualifying candidate",
        "#F5C96B",
    )


# ============================================================
# SAVED PAPER BUY / TP / EXIT MARKERS
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
            exit_stamp is None
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
            profit = "profit review" in reason.lower()

            events.append({
                "time": int(exit_stamp.timestamp()),
                "ticker": ticker,
                "kind": "TP" if profit else "EXIT",
                "side": side,
                "text": (
                    "TP REVIEW"
                    if profit
                    else "EXIT REVIEW"
                ),
            })

    return sorted(
        events,
        key=lambda e: e["time"],
    )[-80:]


# ============================================================
# COMPACT PAPER STRIP
# ============================================================

def paper_strip(rows, ticker):
    official = [
        r for r in rows if r.get("final")
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

    a, b, c = st.columns(3, gap="small")

    a.metric(
        "PAPER POSITION",
        "OPEN" if open_rows else "NONE",
    )

    b.metric(
        "OFFICIAL P&L",
        f"${managed:+.2f}",
    )

    c.metric(
        "SETTLED / PENDING",
        f"{len(official)} / {len(pending)}",
    )


# ============================================================
# COMPACT STYLING
# ============================================================

COMPACT_CSS = """
<style>
.block-container{
 padding-top:.65rem!important;
 padding-bottom:1rem!important;
}
[data-testid="stVerticalBlock"]{
 gap:.35rem!important;
}
[data-testid="stHorizontalBlock"]{
 gap:.4rem!important;
}
[data-testid="stMarkdownContainer"] p{
 margin-bottom:.15rem!important;
}
[data-testid="stMetric"]{
 background:#1c102e;
 border:1px solid #563678;
 border-radius:10px;
 padding:8px 10px;
}
[data-testid="stMetricLabel"]{
 font-size:11px!important;
}
[data-testid="stMetricValue"]{
 font-size:clamp(15px,2vw,23px)!important;
}
[data-testid="stExpander"]{
 margin-top:2px!important;
}
.qgrid{
 display:grid;
 grid-template-columns:repeat(4,minmax(0,1fr));
 gap:6px;
 margin:0 0 6px;
}
.qcell{
 background:#1c102e;
 border:1px solid #563678;
 border-radius:10px;
 padding:8px 4px;
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
 margin-top:3px;
 overflow-wrap:anywhere;
}
.qsignal{
 background:#1c102e;
 border:1px solid #78469c;
 border-radius:12px;
 padding:9px 8px;
 text-align:center;
 margin:2px 0 5px;
}
.qsignal b{
 display:block;
 font-size:clamp(21px,3vw,29px);
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


# ============================================================
# CURRENT SIGNAL HEADER
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

    st.markdown(
        COMPACT_CSS
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

    ticker = str(
        (market or {}).get("ticker") or ""
    )

    rows = st.session_state.get(
        "qe_paper_rows", []
    )

    paper_strip(rows, ticker)

    st.caption(
        "👑 " + VERSION
        + " · PAPER ONLY · "
        "Coinbase and Kalshi verification are separate."
    )

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
            "Recorded EARLY calls are historical, "
            "not current BUY signals."
        )


# ============================================================
# DIRECT CANDLE RECOVERY JAVASCRIPT
# ============================================================

RECOVERY_JS = r"""
// QUEENS_EDGE_DIRECT_RECOVERY_V5
// Actual Coinbase candles only. No synthetic candles.

let qeRecoveryTimer = null;
let qeLastRecovery = 0;

async function qeFetchCandleHistory(){
 const now = Math.floor(Date.now()/60000)*60;
 const merged = new Map();
 let successful = 0;

 // Coinbase supports bounded historical windows.
 // Overlap windows to reduce boundary omissions.
 for(let i=2;i>=0;i--){
  const end = now-i*100*60;
  const start = end-105*60;

  const url =
   'https://api.exchange.coinbase.com/' +
   'products/BTC-USD/candles?granularity=60' +
   '&start=' +
   encodeURIComponent(
    new Date(start*1000).toISOString()
   ) +
   '&end=' +
   encodeURIComponent(
    new Date(end*1000).toISOString()
   );

  try{
   const data=await fetchJSON(url);

   if(!Array.isArray(data))continue;

   successful++;

   for(const x of data){
    if(
     Array.isArray(x) &&
     x.length>=6 &&
     Number.isFinite(Number(x[0]))
    ){
     merged.set(Number(x[0]),x);
    }
   }
  }catch(e){
   // Other windows may still succeed.
  }
 }

 if(successful===0){
  throw Error('Coinbase history recovery unavailable');
 }

 return [...merged.values()];
}

function qeRecoverHistory(){
 if(dead || loading)return;

 const now=Date.now();

 if(now-qeLastRecovery<20000)return;

 const latest=bars.at(-1);

 const stale=
  !latest ||
  now/1000-latest.time>120;

 const missing=
  bars.length<60 ||
  recentGap();

 if(stale || missing){
  qeLastRecovery=now;
  load();
 }
}
"""


# ============================================================
# INJECT RECOVERY INTO ACTUAL COMPACT CHART
# ============================================================

def inject_candle_recovery(html):
    if "QUEENS_EDGE_DIRECT_RECOVERY_V5" in html:
        return html

    request_start = (
        "  const data=await fetchJSON(\n"
        "   'https://api.exchange.coinbase.com/'+\n"
        "   'products/BTC-USD/candles?granularity=60'\n"
        "  );"
    )

    if html.count(request_start) != 1:
        raise RuntimeError(
            "Feed compatibility: original candle "
            "request not found. No unsafe replacement."
        )

    load_anchor = "async function load(){"

    if html.count(load_anchor) != 1:
        raise RuntimeError(
            "Feed compatibility: history loader missing"
        )

    html = html.replace(
        load_anchor,
        RECOVERY_JS + "\n" + load_anchor,
        1,
    )

    html = html.replace(
        request_start,
        "  const data=await qeFetchCandleHistory();",
        1,
    )

    timer_anchor = (
        " pollTimer=setInterval(pollFallback,4000);"
    )

    if html.count(timer_anchor) != 1:
        raise RuntimeError(
            "Feed compatibility: polling timer missing"
        )

    html = html.replace(
        timer_anchor,
        timer_anchor
        + "\n qeRecoveryTimer=setInterval("
        "qeRecoverHistory,5000);",
        1,
    )

    cleanup_anchor = " clearInterval(healthTimer);"

    if html.count(cleanup_anchor) != 1:
        raise RuntimeError(
            "Feed compatibility: cleanup missing"
        )

    html = html.replace(
        cleanup_anchor,
        " clearInterval(qeRecoveryTimer);\n"
        + cleanup_anchor,
        1,
    )

    return html


# ============================================================
# LIVE CHART + PAPER MARKERS
# ============================================================

original_compact_html = base.compact_chart_html


def compact_chart_html(html):
    html = original_compact_html(html)

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

    before, separator, after = html.partition(
        "function qeAllMarkers(){"
    )

    helper, separator2, remainder = after.partition(
        "function detectReversals("
    )

    marker_path = "flags.markers?markers:[]"

    if (
        not separator
        or not separator2
        or remainder.count(marker_path) != 1
    ):
        raise RuntimeError(
            "Chart compatibility: marker path changed"
        )

    remainder = remainder.replace(
        marker_path,
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
 margin:0!important;
 padding:0!important;
}
#summary{
 display:none!important;
}
#toolbar{
 position:relative!important;
 top:auto!important;
 z-index:1!important;
 margin-bottom:3px!important;
}
.mainPanel{
 height:340px!important;
 min-height:340px!important;
 flex:none!important;
 margin-bottom:0!important;
}
#main{
 height:340px!important;
 min-height:340px!important;
}
#notice{
 margin:4px 0!important;
}
@media(max-width:600px){
 .mainPanel,#main{
  height:315px!important;
  min-height:315px!important;
 }
}
</style>
"""

    html = html.replace(
        "</head>",
        css + "</head>",
        1,
    )

    # Patch the actual compact renderer's HTML.
    # No changes to V4 scanner or strategy.
    return inject_candle_recovery(html)


# ============================================================
# COMPACT CHART IFRAME
# ============================================================

original_components_html = components.html


def compact_components_html(
    html, *args, **kwargs
):
    is_queen_chart = (
        isinstance(html, str)
        and "function detectReversals(" in html
        and "QE_PAPER_EVENTS" in html
    )

    if is_queen_chart:
        kwargs["height"] = 490
        kwargs["scrolling"] = False

    return original_components_html(
        html, *args, **kwargs
    )


components.html = compact_components_html


# ============================================================
# APPLY DISPLAY PATCHES
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
# LAUNCH QUEEN'S EDGE
# ============================================================

if __name__ == "__main__":
    base.launch()
