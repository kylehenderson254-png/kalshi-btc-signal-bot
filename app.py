
"""QUEEN'S EDGE | 254 — Bull & Bear paper-only dashboard.

Requires queens_preview_base.py, queens_original.py,
queens_live_chart.py and queens_v3_guard.py.

PAPER ONLY. Never sends real orders.
"""

import importlib.util
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
BASE = ROOT / "queens_preview_base.py"

if not BASE.is_file():
    st.error("Missing queens_preview_base.py. No files were changed.")
    st.stop()

spec = importlib.util.spec_from_file_location(
    "queens_preview_base", BASE
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


# ============================================================
# PRESERVE ALL V4 PAPER HISTORY
# ============================================================

def paper_rows(self):
    """Read all V4 entries, not just the first 200."""
    names = []
    offset = 0

    while True:
        page = self.request(
            "POST",
            "/object/list/" + base.BUCKET,
            {
                "prefix": "entries",
                "limit": 200,
                "offset": offset,
                "sortBy": {
                    "column": "name",
                    "order": "desc",
                },
            },
        )

        if not isinstance(page, list):
            raise RuntimeError("Paper history listing unavailable")

        names += [
            x["name"]
            for x in page
            if isinstance(x, dict)
            and str(x.get("name", "")).endswith(".json")
        ]

        if len(page) < 200:
            break

        offset += 200

        if offset >= 10000:
            raise RuntimeError(
                "Paper history exceeds pagination safety limit"
            )

    def fetch(name):
        entry = self.get("entries", name)

        if not isinstance(entry, dict):
            raise RuntimeError(
                "Saved paper entry could not be read"
            )

        final = self.get("finals", name)

        return {
            "key": name,
            "entry": entry,
            "final": final,
            "exit": (
                None
                if final
                else self.get("exits", name)
            ),
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(fetch, names))

    st.session_state["qe_saved_paper_rows"] = rows
    return rows


# ============================================================
# PRESERVE IMPROVED V4 PAPER P&L
# ============================================================

def paper_metrics(rows):
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
        "Officially matched · managed P&L",
        f"${managed:+.2f}",
    )

    b.metric(
        "Officially matched · hold P&L",
        f"${hold:+.2f}",
    )

    a.metric(
        "V4 entries / official outcomes",
        f"{len(rows)} / {len(finals)}",
    )

    b.metric(
        "Today · managed realized",
        f"${base.daily_pnl(rows):+.2f}",
    )

    a, b, c = st.columns(3)

    a.metric("Open", len(open_rows))
    b.metric("Exit pending settlement", len(pending))
    c.metric("Officially graded", len(finals))

    st.caption(
        f"Estimated exited P&L pending official result: "
        f"${estimated:+.2f}. "
        "Not included in matched totals. "
        "Estimated fills, not live trades."
    )

    if finals:
        wins = sum(
            float(r["final"]["managed_pnl"]) > 0
            for r in finals
        )

        st.caption(
            f"Officially matched managed wins: "
            f"{wins}/{len(finals)} "
            f"({wins / len(finals):.1%}). Paper only."
        )


# ============================================================
# CORRECT KALSHI QUOTE MAPPING
# ============================================================

def fix_quote_mapping():
    source = inspect.getsource(base.qe_stage_row)

    for old, new in [
        ("yes", "ABOVE"),
        ("no", "BELOW"),
    ]:
        for kind in ("bid", "ask"):
            before = f"quote(market, '{old}', '{kind}')"
            after = f"quote(market, '{new}', '{kind}')"

            if source.count(before) != 1:
                raise RuntimeError(
                    "MID quote compatibility check failed"
                )

            source = source.replace(before, after)

    exec(
        compile(source, str(BASE), "exec"),
        base.__dict__,
    )


def side_of(value):
    v = str(value or "").upper()

    if v in ("UP", "ABOVE", "YES"):
        return "UP"

    if v in ("DOWN", "BELOW", "NO"):
        return "DOWN"

    return None


# ============================================================
# QUEEN'S SIGNAL — DISPLAY ONLY
# ============================================================

def signal_state(market, errors, verified, reason, early):
    ticker = str((market or {}).get("ticker") or "")

    if not ticker.startswith("KXBTC15M-"):
        return (
            "WAIT",
            "Waiting for BTC 15-minute contract",
            "#f6cc78",
        )

    close = base.utc((market or {}).get("close_time"))

    if not close:
        return (
            "DATA STALE",
            "Contract time unavailable",
            "#ff718e",
        )

    if (close - base.now_utc()).total_seconds() <= 0:
        return (
            "WAIT",
            "Contract closing or rolling over",
            "#f6cc78",
        )

    if errors or not verified:
        return (
            "DATA STALE",
            "Market quote verification unavailable",
            "#ff718e",
        )

    lower = str(reason or "").lower()

    if any(
        x in lower
        for x in (
            "stale",
            "unavailable",
            "needs refreshing",
            "data issue",
            "candle or quote",
        )
    ):
        return (
            "DATA STALE",
            "Waiting for fresh market data",
            "#ff718e",
        )

    if "setup detected" in lower:
        if "ABOVE" in str(reason).upper():
            return (
                "EARLY UP",
                "Live candidate · V4 entry NOT confirmed",
                "#53e7ab",
            )

        if "BELOW" in str(reason).upper():
            return (
                "EARLY DOWN",
                "Live candidate · V4 entry NOT confirmed",
                "#ff718e",
            )

    recorded = (
        side_of(early.get("call"))
        if isinstance(early, dict)
        else None
    )

    if recorded:
        return (
            "EARLY " + recorded,
            "Previously recorded call · not a new BUY",
            (
                "#53e7ab"
                if recorded == "UP"
                else "#ff718e"
            ),
        )

    return (
        "WAIT",
        str(reason or "No confirmed setup"),
        "#f6cc78",
    )


# ============================================================
# PURPLE / BLACK BULL & BEAR DESIGN
# ============================================================

def card(label, value, note="", color="#eee5ff"):
    return (
        '<div class="qe-tile">'
        '<div class="qe-label">'
        + escape(str(label))
        + '</div><div class="qe-value" style="color:'
        + color
        + '">'
        + escape(str(value))
        + '</div><div class="qe-note">'
        + escape(str(note))
        + "</div></div>"
    )


STYLE = """
<style>
.qe-brand{
 background:linear-gradient(
  110deg,#1a0c2d,#10091d 52%,#241039
 );
 border:1px solid #9253d5;
 border-radius:18px;
 padding:12px 18px;
 margin:4px 0 12px;
 box-shadow:0 0 22px #7d35b62c;
 text-align:center;
}
.qe-brand h1{
 font-size:clamp(20px,3vw,36px);
 color:#eac9ff;
 margin:0;
 text-shadow:0 0 16px #a958ff;
 font-weight:900;
 letter-spacing:1px;
}
.qe-brand small{
 color:#bd9ddd;
 letter-spacing:2px;
 font-weight:700;
}
.qe-tilegrid{
 display:grid;
 grid-template-columns:repeat(6,minmax(0,1fr));
 gap:8px;
 margin:8px 0;
}
.qe-tile{
 background:linear-gradient(150deg,#211337,#130c23);
 border:1px solid #624081;
 border-radius:12px;
 padding:12px 8px;
 min-width:0;
 text-align:center;
}
.qe-label{
 font-size:11px;
 font-weight:800;
 color:#bfa6da;
 letter-spacing:.4px;
}
.qe-value{
 font-size:clamp(15px,2vw,23px);
 font-weight:900;
 margin:5px 0;
 overflow-wrap:anywhere;
}
.qe-note{
 font-size:10px;
 color:#a994c3;
 min-height:12px;
}
.qe-signal{
 border:1px solid #8b4ac1;
 border-radius:14px;
 background:linear-gradient(110deg,#27143d,#130b24);
 padding:14px;
 text-align:center;
 margin:8px 0;
}
.qe-signal strong{
 font-size:clamp(22px,3vw,32px);
 display:block;
 margin:3px 0;
}
@media(max-width:850px){
 .qe-tilegrid{
  grid-template-columns:repeat(3,minmax(0,1fr));
 }
}
@media(max-width:480px){
 .qe-tilegrid{
  grid-template-columns:repeat(2,minmax(0,1fr));
 }
}
</style>
"""


def header(
    market,
    errors,
    quote_fn,
    candle_fn,
    verified,
    early=None,
    locked=None,
):
    trend, _, reason, fresh_market = (
        base.qe_simple_status(
            market,
            errors,
            quote_fn,
            candle_fn,
            verified,
        )
    )

    signal, explanation, color = signal_state(
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

    target = base.number(
        (market or {}).get("floor_strike")
    )

    yes = (
        base.quote(fresh_market, "ABOVE", "ask")
        if fresh_market
        else None
    )

    no = (
        base.quote(fresh_market, "BELOW", "ask")
        if fresh_market
        else None
    )

    spot = None

    if signal != "DATA STALE":
        try:
            spot, _ = base.trade_snapshot()
        except Exception:
            pass

    lock_call = (
        side_of(locked.get("call"))
        if isinstance(locked, dict)
        else None
    )

    lock_status = (
        str(locked.get("status", ""))
        if isinstance(locked, dict)
        else ""
    )

    lock_label = (
        "RECORDED " + lock_call
        if lock_call
        else "WAIT"
    )

    now_ct = (
        base.now_utc()
        .astimezone(base.CT)
        .strftime("%I:%M:%S %p")
        .lstrip("0")
    )

    tiles = [
        card(
            "BTC/USD · COINBASE",
            f"${spot:,.2f}" if spot is not None else "—",
            "Indicative spot",
        ),
        card(
            "KALSHI TARGET",
            f"${target:,.2f}" if target is not None else "—",
            "Price to beat",
        ),
        card(
            "SETTLEMENT IN",
            (
                f"{seconds//60:02d}:{seconds%60:02d}"
                if seconds is not None
                else "—"
            ),
            now_ct,
        ),
        card(
            "UP · ASK",
            f"{yes*100:.1f}¢" if yes is not None else "—",
            "Kalshi contract",
            "#53e7ab",
        ),
        card(
            "DOWN · ASK",
            f"{no*100:.1f}¢" if no is not None else "—",
            "Kalshi contract",
            "#ff718e",
        ),
        card(
            "MARKET BIAS",
            trend,
            "EMA/momentum analysis",
            "#d6b0ff",
        ),
    ]

    st.markdown(
        STYLE
        + '<div class="qe-brand">'
        '<h1>👑 🐂 QUEEN’S EDGE | 254 🐻 👑</h1>'
        '<small>BTC · KALSHI 15-MINUTE '
        'COMMAND CENTER · PAPER ONLY</small>'
        '</div><div class="qe-tilegrid">'
        + "".join(tiles)
        + '</div><div class="qe-signal">'
        '<div class="qe-label">'
        '👑 QUEEN’S CURRENT SIGNAL</div>'
        f'<strong style="color:{color}">'
        f'{escape(signal)}</strong>'
        '<span style="color:#d9c4ed">'
        f'{escape(explanation)}</span></div>',
        unsafe_allow_html=True,
    )

    # Keep existing EARLY / MID / LOCK records.
    base.qe_stage_row(
        market,
        errors,
        verified,
        early,
        locked,
    )

    st.session_state["qe_chart_sidebar"] = {
        "signal": signal,
        "reason": explanation,
        "trend": trend,
        "lock": lock_label,
        "lock_status": lock_status or "Not recorded",
        "target": target,
        "ticker": str(
            (market or {}).get("ticker") or ""
        ),
        "verified": (
            not errors
            and verified
            and signal != "DATA STALE"
        ),
    }

    st.caption(
        "BUY markers = recorded V4 paper entries only. "
        "WATCH arrows = retrospective chart patterns, "
        "NOT buys. Kalshi contract prices differ "
        "from BTC spot. No real orders."
    )


# ============================================================
# BUILD REAL PAPER-EVENT MARKERS
# ============================================================

def events_from_rows(rows, ticker):
    """Use saved V4 events only. Never invent trades."""
    events = []

    for row in rows:
        entry = row.get("entry") or {}

        if entry.get("ticker") != ticker:
            continue

        side = side_of(entry.get("side"))
        observed = base.utc(
            entry.get("observed_at")
        )

        if not side or not observed:
            continue

        price = base.number(
            entry.get("price")
        )

        events.append({
            "kind": "BUY",
            "side": side,
            "time": int(observed.timestamp()),
            "label": (
                f"PAPER BUY {side}"
                + (
                    f" {price*100:.0f}¢"
                    if price is not None
                    else ""
                )
            ),
        })

        exit_event = row.get("exit") or {}
        final = row.get("final") or {}

        reason = str(
            exit_event.get("reason")
            or final.get("exit_reason")
            or ""
        )

        when = base.utc(
            exit_event.get("observed_at")
            or final.get("managed_closed_at")
        )

        # Official settlement is not an executable exit.
        if (
            when
            and reason
            and reason != "Held to official settlement"
        ):
            kind = (
                "TP"
                if "profit review" in reason.lower()
                else "EXIT"
            )

            events.append({
                "kind": kind,
                "side": side,
                "time": int(when.timestamp()),
                "label": (
                    "TP REVIEW"
                    if kind == "TP"
                    else "EXIT REVIEW"
                ),
            })

    return sorted(
        events,
        key=lambda e: (e["time"], e["kind"]),
    )[-100:]


# ============================================================
# ENHANCE EXISTING LIVE CHART
# ============================================================

def chart_html(original_html):
    """Add design and recorded markers to existing chart."""
    html = ORIGINAL_CHART_HTML(original_html)

    state = (
        st.session_state.get("qe_chart_sidebar")
        or {}
    )

    ticker = state.get("ticker", "")

    rows = (
        st.session_state.get("qe_saved_paper_rows")
        or []
    )

    events = events_from_rows(rows, ticker)

    data = (
        json.dumps(events, separators=(",", ":"))
        .replace("<", "\\u003c")
    )

    html = html.replace(
        "const CONFIG=__CONFIG__;",
        "const CONFIG=__CONFIG__;\n"
        "const QE_PAPER_EVENTS=" + data + ";",
        1,
    )

    js = """
let qePaperMarkers=[];

function qeBuildPaperMarkers(){
 const available=new Set(bars.map(b=>b.time));
 const result=[];

 for(const e of QE_PAPER_EVENTS){
  const t=Math.floor(e.time/60)*60;
  if(!available.has(t))continue;

  const up=e.side==='UP';

  result.push({
   time:t,
   position:e.kind==='BUY'
    ?(up?'belowBar':'aboveBar')
    :'aboveBar',
   shape:e.kind==='BUY'
    ?(up?'arrowUp':'arrowDown')
    :'circle',
   color:e.kind==='BUY'
    ?(up?'#50edab':'#ff6f8c')
    :(e.kind==='TP'?'#ffe169':'#d6a6ff'),
   text:e.label
  });
 }

 return result.sort((a,b)=>a.time-b.time);
}

function qeVisibleMarkers(){
 return [
  ...qePaperMarkers,
  ...(flags.markers?markers:[])
 ].sort((a,b)=>a.time-b.time);
}
"""

    if html.count("function detectReversals(") != 1:
        raise RuntimeError(
            "Chart markers compatibility check failed"
        )

    html = html.replace(
        "function detectReversals(",
        js + "\nfunction detectReversals(",
        1,
    )

    needle = (
        " series.candles.setMarkers(\n"
        "  flags.markers?markers:[]\n"
        " );"
    )

    if html.count(needle) != 1:
        raise RuntimeError(
            "Chart marker rendering compatibility check failed"
        )

    html = html.replace(
        needle,
        " qePaperMarkers=qeBuildPaperMarkers();\n"
        " series.candles.setMarkers(qeVisibleMarkers());",
        1,
    )

    # Update marker toggle without altering WATCH detection.
    html = html.replace(
        "flags.markers?markers:[]",
        "qeVisibleMarkers()",
        1,
    )

    # Preserve saved historical markers if the live feed is stale.
    html = html.replace(
        "series.candles?.setMarkers([])",
        "series.candles?.setMarkers(qePaperMarkers)",
    )

    side_html = (
        '<aside class="qe-side">'
        '<div class="qe-side-title">👑 CURRENT SIGNAL</div>'
        '<div class="qe-side-big">'
        + escape(str(state.get("signal", "WAIT")))
        + "</div><p>"
        + escape(str(
            state.get(
                "reason",
                "Waiting for verification",
            )
        ))
        + '</p><div class="qe-side-title">'
        '📈 MARKET BIAS</div><p>'
        + escape(str(
            state.get("trend", "UNCERTAIN")
        ))
        + '</p><div class="qe-side-title">'
        '🔒 SETTLEMENT LOCK</div><p>'
        + escape(str(
            state.get("lock", "WAIT")
        ))
        + "</p><small>"
        + escape(str(
            state.get(
                "lock_status",
                "Not recorded",
            )
        ))
        + '</small><p class="qe-disclaimer">'
        'Paper observations, not live orders '
        'or guarantees.</p></aside>'
    )

    main_start = '<div class="panel mainPanel">'
    main_end = '<div id="main"></div>\n</div>'

    if (
        html.count(main_start) != 1
        or html.count(main_end) != 1
    ):
        raise RuntimeError(
            "Chart layout compatibility check failed"
        )

    html = html.replace(
        main_start,
        '<div class="qe-chart-layout">'
        '<div class="qe-chart-main">'
        + main_start,
        1,
    )

    html = html.replace(
        main_end,
        main_end + "</div>" + side_html + "</div>",
        1,
    )

    recent = [
        e for e in events
        if e["kind"] in ("BUY", "TP", "EXIT")
    ][-3:]

    finals = [
        r for r in rows
        if r.get("final")
    ]

    official = sum(
        float(r["final"]["managed_pnl"])
        for r in finals
    )

    open_positions = [
        r for r in rows
        if not r.get("final")
        and not r.get("exit")
        and (r.get("entry") or {}).get("ticker") == ticker
    ]

    if open_positions:
        entry = open_positions[0]["entry"]

        position = (
            escape(str(entry.get("side", "—")))
            + " · "
            + f"{float(entry.get('price', 0))*100:.0f}"
            + "¢ contract entry"
        )
    else:
        position = "No open V4 paper position"

    history = "".join(
        "<div>"
        + escape(e["label"])
        + " · "
        + datetime.fromtimestamp(
            e["time"],
            base.CT,
        ).strftime("%H:%M:%S CT")
        + "</div>"
        for e in reversed(recent)
    ) or "No recorded V4 entries yet"

    bottom = (
        '<div class="qe-bottom">'
        '<div class="qe-bottom-box">'
        "<b>🎯 CURRENT PAPER POSITION</b><p>"
        + position
        + '</p></div><div class="qe-bottom-box">'
        "<b>📊 OFFICIAL V4 P&L</b><p>"
        + f"${official:+.2f} · {len(finals)} settled"
        + '</p></div><div class="qe-bottom-box">'
        "<b>📝 RECENT RECORDED EVENTS</b>"
        + history
        + "</div></div>"
    )

    html = html.replace(
        '<div id="legend">',
        bottom + '\n<div id="legend">',
        1,
    )

    css = """
<style>
.qe-chart-layout{
 display:grid;
 grid-template-columns:minmax(0,3.6fr) minmax(190px,1fr);
 gap:10px;
 margin:8px 0;
}
.qe-chart-main{min-width:0}
.qe-chart-main .mainPanel{
 height:420px;
 margin:0;
}
.qe-chart-main #main{
 height:100%!important;
 min-height:410px;
}
.qe-side{
 background:linear-gradient(150deg,#241039,#110a20);
 border:1px solid #8a4fc3;
 border-radius:12px;
 padding:16px 12px;
 box-shadow:0 0 15px #8c39cb44;
}
.qe-side-title{
 font-weight:900;
 color:#d4aaff;
 font-size:12px;
 letter-spacing:.7px;
 margin-top:10px;
}
.qe-side-big{
 font-size:25px;
 font-weight:900;
 color:#63e9ad;
 margin:12px 0;
}
.qe-side p{
 line-height:1.5;
 color:#e7d9f5;
}
.qe-side small,.qe-disclaimer{
 color:#b99dcf;
 font-size:11px;
}
.qe-bottom{
 display:grid;
 grid-template-columns:repeat(3,minmax(0,1fr));
 gap:8px;
 margin:10px 0;
}
.qe-bottom-box{
 border:1px solid #714293;
 background:#211435;
 border-radius:10px;
 padding:12px;
 min-width:0;
}
.qe-bottom-box b{
 color:#d8b8f3;
 font-size:11px;
}
.qe-bottom-box p{
 font-size:15px;
 font-weight:800;
}
.qe-bottom-box div{
 font-size:11px;
 margin:6px 0;
 color:#ddd0ee;
}
@media(max-width:800px){
 .qe-chart-layout{
  grid-template-columns:1fr;
 }
 .qe-side{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:4px;
 }
 .qe-bottom{
  grid-template-columns:1fr;
 }
 .qe-chart-main .mainPanel{
  height:350px;
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
# APPLY DISPLAY PATCHES — PRESERVE BASE STRATEGY
# ============================================================

base.PaperJournal.rows = paper_rows
base.paper_metrics = paper_metrics

try:
    fix_quote_mapping()
except (RuntimeError, OSError, TypeError) as exc:
    st.error(
        "Compatibility check failed. "
        "Existing dashboard was not changed: "
        + str(exc)
    )
    st.stop()

base.qe_simple_header = header

ORIGINAL_CHART_HTML = base.compact_chart_html
base.compact_chart_html = chart_html


# ============================================================
# LAUNCH
# ============================================================

if __name__ == "__main__":
    base.launch()
