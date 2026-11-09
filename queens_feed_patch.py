
"""QUEEN'S EDGE | 254 — candle recovery patch.

PAPER ONLY. No orders, no fabricated candles.
Preserves the existing chart, markers and indicators.
"""

import queens_live_chart as chart


_ORIGINAL_RENDER = chart.render_live_chart
_ORIGINAL_HTML = chart.components.html


def repair_chart_html(html):
    """Add explicit-window historical candle recovery."""

    if "QE_FEED_RECOVERY_V1" in html:
        return html

    original_request = """  const data=await fetchJSON(
   'https://api.exchange.coinbase.com/'+
   'products/BTC-USD/candles?granularity=60'
  );"""

    replacement_request = """  const data=await qeFetchCandleHistory();"""

    if original_request not in html:
        raise RuntimeError(
            "Chart version does not match the feed patch. "
            "Original chart has not been changed."
        )

    helper = """
// QE_FEED_RECOVERY_V1
// Fetch actual Coinbase candles in bounded time windows.
// Missing minutes are NEVER filled with invented prices.
let qeLastHistoryAttempt = 0;

async function qeFetchCandleHistory(){
 const now = Math.floor(Date.now()/60000)*60;
 const windows = [];

 // Three 100-minute windows, with overlap.
 for(let i=2;i>=0;i--){
  const end = now-i*100*60;
  const start = end-105*60;
  windows.push({start,end});
 }

 const merged = new Map();
 let successful = 0;

 for(const w of windows){
  const start = new Date(w.start*1000).toISOString();
  const end = new Date(w.end*1000).toISOString();

  const url =
   'https://api.exchange.coinbase.com/' +
   'products/BTC-USD/candles?granularity=60' +
   '&start=' + encodeURIComponent(start) +
   '&end=' + encodeURIComponent(end);

  try{
   const data = await fetchJSON(url);

   if(!Array.isArray(data))continue;

   successful++;

   for(const candle of data){
    if(
     Array.isArray(candle) &&
     candle.length >= 6 &&
     Number.isFinite(Number(candle[0]))
    ){
     merged.set(Number(candle[0]),candle);
    }
   }
  }catch(e){
   // Another window may still succeed.
  }
 }

 if(successful===0){
  throw Error('Coinbase historical windows unavailable');
 }

 return [...merged.values()];
}

function qeRecoverHistory(){
 if(dead || loading)return;

 const now = Date.now();
 if(now-qeLastHistoryAttempt<20000)return;

 const latest = bars.at(-1);
 const oldCandle =
  !latest ||
  now/1000-latest.time>120;

 const missing = bars.length<60 || recentGap();

 if(missing || oldCandle){
  qeLastHistoryAttempt=now;
  load();
 }
}
"""

    anchor = "async function load(){"

    if anchor not in html:
        raise RuntimeError(
            "Could not locate the original chart history loader."
        )

    html = html.replace(
        anchor,
        helper + "\n" + anchor,
        1,
    )

    html = html.replace(
        original_request,
        replacement_request,
        1,
    )

    timer_anchor = (
        " pollTimer=setInterval(pollFallback,4000);"
    )

    if timer_anchor not in html:
        raise RuntimeError(
            "Could not locate the original chart polling timer."
        )

    html = html.replace(
        timer_anchor,
        timer_anchor
        + "\n qeRecoveryTimer=setInterval(qeRecoverHistory,5000);",
        1,
    )

    html = html.replace(
        "let healthTimer=null;",
        "let qeRecoveryTimer=null;\nlet healthTimer=null;",
        1,
    )

    cleanup = " clearInterval(healthTimer);"

    if cleanup not in html:
        raise RuntimeError(
            "Could not locate the original chart cleanup."
        )

    html = html.replace(
        cleanup,
        " clearInterval(qeRecoveryTimer);\n" + cleanup,
        1,
    )

    return html


def render_live_chart(*args, **kwargs):
    """Render the original chart with recovery added."""

    def patched_html(html, *html_args, **html_kwargs):
        repaired = repair_chart_html(html)
        return _ORIGINAL_HTML(
            repaired,
            *html_args,
            **html_kwargs,
        )

    chart.components.html = patched_html

    try:
        return _ORIGINAL_RENDER(*args, **kwargs)
    finally:
        chart.components.html = _ORIGINAL_HTML


def install(base):
    """Install the patch without changing original files."""

    base.render_live_chart = render_live_chart
