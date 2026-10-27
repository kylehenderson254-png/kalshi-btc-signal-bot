"""Read-only Kalshi 15-minute market grid and dashboard-only Flip Radar."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import html
import re
import streamlit as st
import base64
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh
from call_tracker import store_from_secrets, process, candidate, early_store_from_secrets, process_early
from pressure_radar import show_pressure_radar

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
CENTRAL = ZoneInfo("America/Chicago")
SERIES = [
    ("Bitcoin", "KXBTC15M", "₿"),
    ("Gold", "KXGOLD15M", "🥇"),
    ("Silver", "KXSILVER15M", "🥈"),
    ("WTI Oil", "KXWTI15M", "🛢️"),
]
HISTORY_LIMIT = 60
RECENT_RESULTS = 10

st.set_page_config(page_title="QUEEN’S EDGE | 254", page_icon="👑", layout="wide")

st.markdown("""<style>
.yes-pill {color:#16a34a;font-weight:800;font-size:1.05rem;}
.no-pill {color:#dc2626;font-weight:800;font-size:1.05rem;}
.up-arrow {color:#16a34a;font-weight:800;}
.down-arrow {color:#dc2626;font-weight:800;}
.block-container {max-width: 1280px; padding-top: 1.5rem; padding-bottom: 2rem;}
[data-testid="stMetric"] {background: rgba(128,128,128,.06); border-radius: 10px; padding: 10px 12px;}
.quote-tile {border-radius: 14px; padding: 12px; border: 1px solid; min-height: 125px;}
.yes-tile {background: rgba(22,163,74,.12); border-color: rgba(22,163,74,.48);}
.no-tile {background: rgba(220,38,38,.12); border-color: rgba(220,38,38,.48);}
.quote-title {font-size: 1.12rem; font-weight: 850; margin-bottom: 10px;}
.yes-tile .quote-title, .yes-tile b {color: #22c55e;}
.no-tile .quote-title, .no-tile b {color: #ef4444;}
.quote-row {display:flex; justify-content:space-between; align-items:center; padding:3px 0; font-size:.93rem; gap:8px;}
.quote-row b {font-size:1.12rem;}
</style>""", unsafe_allow_html=True)


st.markdown("""<style>
.stApp{background:radial-gradient(ellipse at top,#1c0b37 0%,#070714 45%,#05050e 100%);color:#e9e6f7}
.block-container{max-width:1550px!important;padding-top:.55rem!important}
.qe-banner img{width:100%;max-height:250px;object-fit:cover;border-radius:12px;border:1px solid #6d28d9;box-shadow:0 0 24px #6b21a866}
.qe-kicker{text-align:center;color:#c4a5ff;letter-spacing:.13em;font-size:.78rem;margin:8px 0 20px}
[data-testid="stVerticalBlockBorderWrapper"]{background:linear-gradient(145deg,rgba(18,16,33,.95),rgba(8,8,20,.95));border-color:#5b32a6!important;border-radius:13px!important}
[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums}
[data-testid="stSidebar"]{background:#0d0920}
.stApp h1,.stApp h2,.stApp h3{color:#e8d9ff}
@media(max-width:700px){.qe-banner img{height:135px;object-fit:cover}.qe-kicker{font-size:.62rem;letter-spacing:0}}
</style>""",unsafe_allow_html=True)

st.markdown("""<style>
.qe-market-name{font-size:1.2rem;font-weight:900;letter-spacing:.08em;color:#e6d4ff;margin-bottom:4px}
.qe-compact-quote{border:1px solid;border-radius:12px;padding:10px 9px;min-height:112px;font-size:.78rem;line-height:1.8;font-variant-numeric:tabular-nums}
.qe-compact-quote div:not(.qe-quote-heading){display:flex;justify-content:space-between;gap:3px}
.qe-compact-quote b{font-size:.94rem}
.qe-quote-heading{font-weight:800;font-size:.75rem;min-height:32px;line-height:1.2;margin-bottom:4px}
.qe-green{background:rgba(15,140,84,.12);border-color:#178d61;color:#58e6aa}
.qe-red{background:rgba(180,35,70,.12);border-color:#ad3358;color:#ff8ca2}
[data-testid="stVerticalBlockBorderWrapper"]{border-color:#6d28d9!important}
@media(max-width:700px){.qe-market-name{font-size:1.05rem}.qe-compact-quote{padding:7px 5px;font-size:.7rem}.qe-compact-quote b{font-size:.78rem}}
</style>""",unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Radar settings")
    refresh_seconds = st.selectbox("Refresh every", [3, 5, 10, 15], index=0, format_func=lambda s: f"{s} seconds")
    sudden_cents = st.slider("Sudden move threshold (¢)", 2, 15, 4)
    st.caption("Alerts are shown only on this dashboard. Keep the page open to collect samples.")
    if st.button("Clear radar history", use_container_width=True):
        st.session_state["quote_history"] = {}
        st.session_state["radar_events"] = []
        st.rerun()

if "quote_history" not in st.session_state:
    st.session_state.quote_history = {}
if "radar_events" not in st.session_state:
    st.session_state.radar_events = []


def num(value):
    try:
        return None if value in (None, "") else float(value)
    except (ValueError, TypeError):
        return None


def price(market, dollar_field, cent_field):
    val = num(market.get(dollar_field))
    if val is not None:
        return val
    val = num(market.get(cent_field))
    return val / 100 if val is not None else None


def cents(val):
    return "—" if val is None else f"{val * 100:.1f}¢"


def when(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def target(m):
    for key in ("floor_strike", "functional_strike", "custom_strike", "cap_strike"):
        value = m.get(key)
        if value not in (None, ""):
            try:
                return f"${float(value):,.2f}"
            except (TypeError, ValueError):
                return str(value)
    return m.get("yes_sub_title") or m.get("subtitle") or "See contract rules"




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
    # Kalshi uses both fixed numeric targets and a starting Pyth candle reference.
    # Require explicit YES settlement language and the expected asset/series.
    fixed_target = r"\$?\d[\d,]*(?:\.\d+)?\b"
    pyth_symbol = {"KXGOLD15M": "gold", "KXSILVER15M": "silver", "KXWTI15M": "pythoil"}[series]
    reference_target = (
        rf"the close price of the 1-minute pyth {pyth_symbol} candlestick\b"
    )
    return bool(re.search(
        rf"\bif the close price of the 1-minute candlestick for {asset}\b"
        rf".{{0,240}}?\bis at least\s+(?:{fixed_target}|{reference_target})"
        rf".{{0,240}}?\bthen the market resolves to yes\b",
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


def directional_labels(m):
    """Only rename YES/NO if the contract explicitly describes YES as above the target.

    A generic title or numeric strike is insufficient to infer contract semantics.
    """
    yes_description = " ".join(str(m.get(key) or "") for key in (
        "yes_sub_title", "yes_subtitle", "rules_primary", "rules_secondary"
    )).lower()
    yes_description = " ".join(yes_description.split())
    if comparison_yes_is_higher_or_equal(m) or candlestick_yes_is_at_least_target(m):
        return "ABOVE OR EQUAL TARGET", "BELOW TARGET", True
    # Do not infer from generic "will price be above" question alone, since
    # a question does not establish the direction of YES in every contract.
    if re.search(r"\byes\s+(?:if|means|wins?\s+if|settles?\s+if)\s+.{0,100}\b(?:above|higher than|greater than|exceeds)\b", yes_description):
        return "ABOVE TARGET", "BELOW TARGET", True
    if re.search(r"\b(?:above|higher than|greater than|exceeds)\b.{0,70}\b(?:settles?\s+yes|results?\s+in\s+yes)\b", yes_description):
        return "ABOVE TARGET", "BELOW TARGET", True
    return "YES", "NO", False

@st.cache_data(ttl=2, show_spinner=False)
def fetch_markets(series_ticker):
    response = requests.get(
        f"{API_BASE}/markets",
        params={"series_ticker": series_ticker, "status": "open", "limit": 200},
        timeout=12,
    )
    response.raise_for_status()
    return response.json().get("markets", [])



@st.cache_data(ttl=60, show_spinner=False)
def fetch_recent_results(series_ticker):
    """Request recently settled contracts; do not infer outcomes from quotes."""
    response = requests.get(
        f"{API_BASE}/markets",
        params={"series_ticker": series_ticker, "status": "settled", "limit": 200},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json().get("markets", [])
    outcomes = []
    for m in data:
        result = str(m.get("result") or "").lower().strip()
        if result not in ("yes", "no"):
            continue
        outcomes.append(m)
    outcomes.sort(key=lambda m: when(m.get("close_time") or m.get("latest_expiration_time")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return outcomes[:RECENT_RESULTS]


def recent_arrows(series):
    try:
        results = fetch_recent_results(series)
    except (requests.RequestException, ValueError) as exc:
        st.caption(f"Recent results unavailable: {exc}")
        return
    if not results:
        st.caption("No settled YES/NO results returned for this series.")
        return
    arrows = []
    for m in reversed(results):  # oldest → newest
        yes = str(m.get("result")).lower() == "yes"
        arrow = "↑" if yes else "↓"
        cls = "up-arrow" if yes else "down-arrow"
        ticker = str(m.get("ticker", ""))
        arrows.append(f'<span class="{cls}" title="{ticker}: {"YES" if yes else "NO"}">{arrow}</span>')
    st.markdown("&nbsp; &nbsp; ".join(arrows), unsafe_allow_html=True)
    st.caption(f"Last {len(results)} settled results · oldest → newest · ↑ YES / ↓ NO")


def momentum_stats(m):
    ticker = m.get("ticker", "")
    hist = st.session_state.quote_history.get(ticker, [])
    if len(hist) < 2:
        return "Warming up", "Not enough samples", 0.0
    move = (hist[-1][1] - hist[-2][1]) * 100
    if abs(move) < 0.5:
        direction = "→ Flat"
    else:
        direction = "↑ YES rising" if move > 0 else "↓ YES falling"
    magnitude = abs(move)
    strength = "Strong" if magnitude >= 4 else "Moderate" if magnitude >= 2 else "Weak"
    return direction, f"{strength} · {move:+.1f}¢ / sample", magnitude

def current_market(markets):
    now = datetime.now(timezone.utc)
    valid = [m for m in markets if not when(m.get("close_time")) or when(m.get("close_time")) > now]
    if not valid:
        return None
    active = [m for m in valid if not when(m.get("open_time")) or when(m.get("open_time")) <= now]
    pool = active if active else valid
    return min(pool, key=lambda m: when(m.get("close_time")) or datetime.max.replace(tzinfo=timezone.utc))


def midpoint(m):
    bid = price(m, "yes_bid_dollars", "yes_bid")
    ask = price(m, "yes_ask_dollars", "yes_ask")
    if bid is not None and ask is not None and 0 <= bid <= ask <= 1:
        return (bid + ask) / 2, "YES bid/ask midpoint"
    last = price(m, "last_price_dollars", "last_price")
    if last is not None and 0 <= last <= 1:
        return last, "last traded YES price (bid/ask unavailable)"
    return None, "No usable quote"


def prediction_panel(m, verified_direction, remaining_seconds, ticker):
    """Experimental market-implied directional lean; not an independent forecast."""
    st.markdown("**🎯 Experimental settlement call**")
    if not verified_direction:
        st.info("No ABOVE/BELOW call: contract rules do not confirm that YES means ABOVE.")
        return
    bid = price(m, "yes_bid_dollars", "yes_bid")
    ask = price(m, "yes_ask_dollars", "yes_ask")
    if bid is None or ask is None or not (0 <= bid <= ask <= 1):
        st.info("No call: both current YES bid and ask are needed for a comparable market quote.")
        return
    spread = ask - bid
    if spread > 0.15 or (bid == 0 and ask == 1):
        st.info("No call: the quote spread is too wide to provide a useful directional reading.")
        return
    implied_above = (bid + ask) / 2
    implied_below = 1 - implied_above
    # A deliberately conservative no-call zone, not a validated prediction model.
    if implied_above >= 0.60:
        call = "🟢 ABOVE TARGET"
    elif implied_above <= 0.40:
        call = "🔴 BELOW TARGET"
    else:
        call = "⚪ NO CLEAR CALL"
    left, right = st.columns(2)
    left.metric("Market-implied ABOVE", f"{implied_above:.0%}")
    right.metric("Market-implied BELOW", f"{implied_below:.0%}")
    st.markdown(f"**Current market-based lean: {call}**")
    hist = st.session_state.quote_history.get(ticker, [])
    if len(hist) >= 2:
        change = (hist[-1][1] - hist[-2][1]) * 100
        st.caption(f"Last sampled YES quote move: {change:+.1f}¢ · Quote spread: {spread * 100:.1f}¢")
    else:
        st.caption(f"Quote spread: {spread * 100:.1f}¢ · Momentum still warming up")
    if remaining_seconds is not None:
        st.caption(f"Time until contract close: {remaining_seconds // 60:02d}:{remaining_seconds % 60:02d}")
    st.caption("Based on Kalshi's current YES bid/ask midpoint, not an independently trained prediction. "
               "Percentages are market-price proxies, not calibrated win probabilities; fees, spreads and settlement details matter. "
               "A call is not a recommendation to trade.")


def scan(m, name):
    ticker = m.get("ticker", name)
    val, source = midpoint(m)
    now = datetime.now(timezone.utc)
    hist = st.session_state.quote_history.setdefault(ticker, [])
    latest_label = "Scanning"
    detail = "Collecting price samples"
    new_sample = False
    if val is not None and (not hist or (now - hist[-1][0]).total_seconds() >= max(2, refresh_seconds - 1)):
        # Do not compare last trade prices with midpoints; those are different price sources.
        if hist and hist[-1][2] != source:
            hist.clear()
        hist.append((now, val, source))
        del hist[:-HISTORY_LIMIT]
        new_sample = True
    if val is None:
        return "No quote", "Waiting for Kalshi quote", 0, source
    if len(hist) < 3:
        return "Warming up", f"{len(hist)}/3 samples collected", len(hist), source
    if new_sample:
        prev_move = hist[-2][1] - hist[-3][1]
        move = hist[-1][1] - hist[-2][1]
        threshold = 0.005
        flip = None
        if prev_move <= -threshold and move >= threshold:
            flip = "🔺 Bullish flip"
        elif prev_move >= threshold and move <= -threshold:
            flip = "🔻 Bearish flip"
        sudden = abs(move) >= sudden_cents / 100
        if flip or sudden:
            latest_label = (flip + " + ⚡") if flip and sudden else (flip or "⚡ Sudden move")
            detail = f"YES quote changed {move * 100:+.1f}¢ since previous sample"
            st.session_state.radar_events.insert(0, {
                "ticker": ticker, "market": name, "label": latest_label,
                "detail": detail, "time": now,
            })
            del st.session_state.radar_events[50:]
            return latest_label, detail, len(hist), source
    # Show latest event for this market without repeating it as a new alert.
    for event in st.session_state.radar_events:
        if event["ticker"] == ticker:
            return "Scanning", "Last alert: " + event["label"], len(hist), source
    return "Scanning", "No flip detected in latest sample", len(hist), source


def render_card(name, series, emoji):
    """Compact, consistently ordered market card; detailed tools remain accessible."""
    with st.container(border=True):
        st.markdown(f'<div class="qe-market-name">{emoji} {html.escape(name.upper())}</div>', unsafe_allow_html=True)
        try:
            markets = fetch_markets(series)
        except (requests.RequestException, ValueError) as exc:
            st.error("Kalshi data unavailable")
            st.caption(f"{series}: {exc}")
            return {"label": "Unavailable", "magnitude": 0, "direction": "—"}
        m = current_market(markets)
        if m:
            st.session_state.setdefault("radar_active_tickers", {})[series] = m.get("ticker")
        else:
            st.session_state.setdefault("radar_active_tickers", {}).pop(series, None)
        if not m:
            st.warning("No active 15-minute contract")
            return {"label": "No market", "magnitude": 0, "direction": "—"}

        ticker = m.get("ticker", series)
        closing = when(m.get("close_time") or m.get("latest_expiration_time"))
        opening = when(m.get("open_time"))
        now = datetime.now(timezone.utc)
        remaining = max(0, int((closing - now).total_seconds())) if closing else None
        above_label, below_label, verified = directional_labels(m)
        label, detail, count, source = scan(m, name)
        direction, strength, magnitude = momentum_stats(m)
        bid = price(m, "yes_bid_dollars", "yes_bid")
        ask = price(m, "yes_ask_dollars", "yes_ask")
        midpoint_val = (bid + ask) / 2 if bid is not None and ask is not None and 0 <= bid <= ask <= 1 else None

        st.caption(f"Target / strike: **{target(m)}**")
        if closing:
            end_ms = int(closing.timestamp() * 1000)
            components.html(f"""<div style="height:48px;background:#120a25;border:1px solid #6d28d9;border-radius:10px;display:flex;align-items:center;justify-content:space-between;padding:0 12px;font-family:system-ui;color:#c4a5ff"><span style="font-size:12px">⏱️ CLOSES IN</span><strong id="clock" style="font-size:24px;color:#39e9bb;font-variant-numeric:tabular-nums">--:--</strong></div><script>const end={end_ms},el=document.getElementById("clock");function tick(){{let n=Math.max(0,Math.ceil((end-Date.now())/1000));el.textContent=String(Math.floor(n/60)).padStart(2,"0")+":"+String(n%60).padStart(2,"0");el.style.color=n<=60?"#ff5b75":"#39e9bb";}}tick();setInterval(tick,250);</script>""", height=52)
        else:
            st.caption("Close time unavailable")

        yes_bid = cents(bid)
        yes_ask = cents(ask)
        no_bid = cents(price(m, "no_bid_dollars", "no_bid"))
        no_ask = cents(price(m, "no_ask_dollars", "no_ask"))
        col_yes, col_no = st.columns(2, gap="small")
        with col_yes:
            st.markdown(
                f'<div class="qe-compact-quote qe-green"><div class="qe-quote-heading">{html.escape(above_label if verified else "YES")}</div>'
                f'<div>BID <b>{html.escape(yes_bid)}</b></div><div>ASK <b>{html.escape(yes_ask)}</b></div></div>',
                unsafe_allow_html=True)
        with col_no:
            st.markdown(
                f'<div class="qe-compact-quote qe-red"><div class="qe-quote-heading">{html.escape(below_label if verified else "NO")}</div>'
                f'<div>BID <b>{html.escape(no_bid)}</b></div><div>ASK <b>{html.escape(no_ask)}</b></div></div>',
                unsafe_allow_html=True)

        if not verified:
            st.caption("Direction unverified — YES/NO labels retained")
        if midpoint_val is not None and verified and ask - bid <= .15:
            if midpoint_val >= .60:
                lean = "🟢 ABOVE"
            elif midpoint_val <= .40:
                lean = "🔴 BELOW"
            else:
                lean = "⚪ NO CALL"
            st.markdown(f"**Market lean: {lean}** · YES midpoint {midpoint_val*100:.1f}¢")
        else:
            st.caption("Market lean unavailable · verify quotes/rules")

        early = st.session_state.get("early_call_map", {}).get(ticker)
        locked = st.session_state.get("locked_call_map", {}).get(ticker)
        st.markdown(f"**🌅 Early:** {html.escape(str(early['call'])) if early else 'Live preview only'}")
        st.markdown(f"**🔒 Locked:** {html.escape(str(locked['call'])) + ' · ' + html.escape(str(locked['status'])) if locked else 'Not recorded in preview'}")
        st.caption(f"📡 {label} · {direction} · {strength}")
        with st.expander("Radar, call details & history", expanded=False):
            st.caption(f"Contract: {ticker}")
            st.caption(f"Closes: {closing.astimezone(CENTRAL):%I:%M:%S %p %Z}" if closing else "Close time unknown")
            st.caption(f"Quotes poll every {refresh_seconds}s; countdown updates locally")
            st.caption("Quote display only. No trading actions.")
            st.markdown("**🐋 Buying / Selling Pressure & Whale Radar**")
            show_pressure_radar(name, ticker)
            st.markdown("**🌅 Early call — live preview (not locked)**")
            if closing and (not opening or opening <= now) and remaining > 300 and midpoint_val is not None and verified and ask - bid <= .15:
                st.write(f"Current early lean: {lean} · YES midpoint {midpoint_val*100:.1f}¢")
            else:
                st.caption("Early preview unavailable or early window has ended.")
            st.caption("Live lean can change; recorded early and locked calls are separate.")
            prediction_panel(m, verified, remaining, ticker)
            st.markdown("**🔒 Automatic 5-minute locked call**")
            if locked:
                st.write(f"{locked['call']} · {locked['status']} · {locked['locked_at']}")
            elif st.session_state.get("tracker_enabled"):
                row, reason = candidate(m, name, series)
                st.caption("Eligible to lock" if row else (reason or "Waiting"))
            else:
                st.caption("Supabase secrets not configured in preview.")
            st.markdown("**📡 Flip Radar**")
            st.caption(f"{label} · {detail} · {count} samples · {source}")
            st.markdown("**Last 10 settled markets**")
            recent_arrows(series)
            with st.expander("Official contract rules"):
                st.write(m.get("rules_primary") or "Check Kalshi for contract rules.")
                st.write(m.get("rules_secondary") or "")
        return {"label": label, "magnitude": magnitude, "direction": direction}


# The database is required for genuine persistent win/loss statistics.
try:
    call_store = store_from_secrets(st.secrets)
except Exception:
    call_store = None
st.session_state.tracker_enabled = call_store is not None
if call_store:
    try:
        # Reuse cached Kalshi market responses; avoid duplicate requests on each refresh.
        active_markets = {}
        for _, series, _ in SERIES:
            try:
                active_markets[series] = current_market(fetch_markets(series))
            except (requests.RequestException, ValueError):
                active_markets[series] = None
        st.session_state["tracker_activity"] = process(call_store, current_by_series=active_markets)
        st.session_state["locked_call_rows"] = call_store.all()
        st.session_state["locked_call_map"] = {r["ticker"]: r for r in st.session_state["locked_call_rows"]}
    except (requests.RequestException, ValueError) as exc:
        st.session_state["tracker_activity"] = [f"Call tracker storage error: {exc}"]
        st.session_state["locked_call_rows"] = []
        st.session_state["locked_call_map"] = {}
else:
    st.session_state["tracker_activity"] = []
    st.session_state["locked_call_rows"] = []
    st.session_state["locked_call_map"] = {}

# Early history has its own Supabase table; failures never block locked-call tracking.
early_store = early_store_from_secrets(st.secrets)
st.session_state["early_call_rows"] = []
st.session_state["early_call_map"] = {}
st.session_state["early_tracker_activity"] = []
st.session_state["early_tracker_error"] = None
if early_store:
    try:
        early_markets = {}
        for _, series, _ in SERIES:
            try:
                early_markets[series] = current_market(fetch_markets(series))
            except (requests.RequestException, ValueError):
                early_markets[series] = None
        st.session_state["early_tracker_activity"] = process_early(early_store, current_by_series=early_markets)
        st.session_state["early_call_rows"] = early_store.all()
        st.session_state["early_call_map"] = {r["ticker"]: r for r in st.session_state["early_call_rows"]}
    except (requests.RequestException, ValueError) as exc:
        st.session_state["early_tracker_error"] = str(exc)

# QUEEN'S EDGE header - image based on the user-approved design reference.
from pathlib import Path as _Path
_banner = _Path(__file__).with_name("queens_banner.jpg")
if _banner.exists():
    _encoded = base64.b64encode(_banner.read_bytes()).decode("ascii")
    st.markdown(f'<div class="qe-banner"><img src="data:image/jpeg;base64,{_encoded}" alt="QUEEN’S EDGE — purple bull and bear banner" /></div>', unsafe_allow_html=True)
else:
    st.title("👑 QUEEN’S EDGE")
st.markdown('<div class="qe-kicker">⚡ LIVE KALSHI MARKETS &nbsp; • &nbsp; BUILT BY K HENDO | 254 &nbsp; • &nbsp; READ ONLY</div>', unsafe_allow_html=True)

st.caption("15-minute markets · Kalshi contract-aligned countdowns · Dashboard-only Flip Radar · Read-only")
st.caption(f"⟳ Refresh interval: {refresh_seconds}s · API quotes may lag or be rate-limited · not a streaming connection")
st.info("Radar detects quote reversals. The experimental settlement call reflects Kalshi market-implied pricing, not a validated independent forecast. No trades are executed.")

# Two matched rows. Bitcoin and Oil always appear side by side.
st.markdown("### ⚡ LIVE MARKET GRID")
statuses = []
top_left, top_right = st.columns(2, gap="small")
with top_left:
    statuses.append(("Bitcoin", render_card("Bitcoin", "KXBTC15M", "₿")))
with top_right:
    statuses.append(("WTI Oil", render_card("WTI Oil", "KXWTI15M", "🛢️")))
bottom_left, bottom_right = st.columns(2, gap="small")
with bottom_left:
    statuses.append(("Gold", render_card("Gold", "KXGOLD15M", "🥇")))
with bottom_right:
    statuses.append(("Silver", render_card("Silver", "KXSILVER15M", "🥈")))

st.subheader("🔒 Locked Call Scoreboard")
if not call_store:
    st.warning("Persistent Call Tracker is not active. Set up Supabase and Streamlit Secrets (instructions in README). The dashboard and radar still work.")
else:
    rows = st.session_state.get("locked_call_rows", [])
    graded = [r for r in rows if r.get("status") in ("win", "loss")]
    wins = sum(r["status"] == "win" for r in graded)
    losses = len(graded) - wins
    a,b,c,d = st.columns(4)
    a.metric("Wins", wins)
    b.metric("Losses", losses)
    c.metric("Win rate", f"{wins / len(graded):.1%}" if graded else "—")
    d.metric("Pending", sum(r.get("status") == "pending" for r in rows))
    st.caption("Win rate excludes pending contracts and contracts where the system abstained. Only official Kalshi settled results are graded.")
    for name, series, emoji in SERIES:
        market_rows = [r for r in graded if r["series_ticker"] == series]
        market_wins = sum(r["status"] == "win" for r in market_rows)
        st.write(f"{emoji} **{name}:** {market_wins} W / {len(market_rows)-market_wins} L · " + (f"{market_wins / len(market_rows):.1%}" if market_rows else "Not graded yet"))
    with st.expander("📜 Prediction history", expanded=False):
        if rows:
            st.dataframe([{
                "Market": r["market_name"], "Contract": r["ticker"],
                "Call": r["call"], "Locked (UTC)": r["locked_at"],
                "YES midpoint (¢)": round(r["yes_midpoint"]*100,1),
                "Status": r["status"].upper(), "Settlement": r.get("settlement_result") or "Pending"
            } for r in rows], hide_index=True, use_container_width=True)
        else:
            st.info("No calls locked yet. The app must be running in the last five minutes of an eligible market to record one.")
    for message in st.session_state.get("tracker_activity", []):
        st.info(message)
    st.caption("Calls are locked once per contract ticker; no later price changes overwrite them. Dashboard scans only while open unless an external scheduler runs worker.py.")

st.subheader("🌅 Early Call Scoreboard")
if not early_store:
    st.warning("Early history requires Supabase secrets (same credentials as Locked Calls).")
elif st.session_state.get("early_tracker_error"):
    st.warning("Early history is not ready. Create the early_calls table using the included SQL setup file, then refresh.")
    st.caption(st.session_state["early_tracker_error"])
else:
    early_rows = st.session_state.get("early_call_rows", [])
    early_graded = [r for r in early_rows if r.get("status") in ("win", "loss")]
    early_wins = sum(r["status"] == "win" for r in early_graded)
    ea, eb, ec, ed = st.columns(4)
    ea.metric("Early wins", early_wins)
    eb.metric("Early losses", len(early_graded) - early_wins)
    ec.metric("Early win rate", f"{early_wins / len(early_graded):.1%}" if early_graded else "—")
    ed.metric("Early pending", sum(r.get("status") == "pending" for r in early_rows))
    st.caption("Separate from Locked Calls. First qualifying early quote per contract (15–5 minutes before close); "
               "only official Kalshi results are graded. Market-implied calls are not independent forecasts.")
    for name, series, emoji in SERIES:
        subset = [r for r in early_graded if r.get("series_ticker") == series]
        w = sum(r["status"] == "win" for r in subset)
        st.write(f"{emoji} **{name}:** {w} W / {len(subset)-w} L · " +
                 (f"{w / len(subset):.1%}" if subset else "Not graded yet"))
    with st.expander("📜 Early prediction history", expanded=False):
        if early_rows:
            st.dataframe([{
                "Market": r["market_name"], "Contract": r["ticker"],
                "Early call": r["call"], "Recorded (UTC)": r["locked_at"],
                "YES midpoint (¢)": round(r["yes_midpoint"] * 100, 1),
                "Status": r["status"].upper(),
                "Settlement": r.get("settlement_result") or "Pending",
            } for r in early_rows], hide_index=True, use_container_width=True)
        else:
            st.info("No early calls recorded yet. Leave the dashboard open during an active contract's first 10 minutes.")
    for msg in st.session_state.get("early_tracker_activity", []):
        st.info(msg)

# Phase 4 compact Flip Radar: presentation only. Existing alert detection and trackers unchanged.
st.markdown('''<style>
.qe-radar-card{background:linear-gradient(135deg,#1c1533,#0c0c1d);border:1px solid #51327f;border-radius:14px;padding:12px 14px;min-height:142px;margin-bottom:8px}
.qe-radar-head{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:15px;font-weight:750;color:#f2eaff}
.qe-radar-chip{font-size:11px;padding:3px 8px;border-radius:20px;font-weight:800}
.qe-radar-body{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:7px}
.qe-radar-value{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.qe-radar-sub{font-size:11px;color:#bdb0d5;margin-top:3px}
.qe-alert{display:flex;justify-content:space-between;gap:12px;padding:8px 11px;border:1px solid #392d56;background:#131025;border-radius:9px;margin:5px 0;font-size:13px}
.qe-alert small{color:#aa9bbf}
</style>''', unsafe_allow_html=True)
st.markdown("**🚨 Flip Radar — All Markets**")
st.caption("Live YES quote momentum · latest 30 samples per market")
radar_cols = st.columns(4, gap="small")
for col, (name, stat) in zip(radar_cols, statuses):
    series_code = next(code for market_name, code, _ in SERIES if market_name == name)
    current = st.session_state.get("radar_active_tickers", {}).get(series_code)
    samples = st.session_state.quote_history.get(current, []) if current else []
    vals = [float(sample[1]) * 100 for sample in samples[-30:]]
    if len(vals) >= 2:
        lo, hi = min(vals), max(vals)
        span = max(hi - lo, 1.0)
        coords = " ".join(f"{4 + i * 108 / (len(vals)-1):.1f},{37 - (v-lo)/span*29:.1f}" for i,v in enumerate(vals))
        spark = f'<polyline points="{coords}" fill="none" stroke="CURRENT" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
    else:
        spark = '<path d="M4 23H112" stroke="CURRENT" stroke-width="1.5" stroke-dasharray="4 5"/>'
    direction = stat["direction"]
    rising = "↑" in direction
    falling = "↓" in direction
    tone = "#34d399" if rising else ("#fb7185" if falling else "#b7a3ed")
    tag = "BUY ↑" if rising else ("SELL ↓" if falling else "FLAT →")
    change = f'{stat["magnitude"]:.1f}¢'
    with col:
        st.markdown(f'''<div class="qe-radar-card">
          <div class="qe-radar-head"><span>{html.escape(name)}</span><span class="qe-radar-chip" style="background:{tone}20;color:{tone}">{tag}</span></div>
          <div class="qe-radar-body"><div><div class="qe-radar-value" style="color:{tone}">{change}</div><div class="qe-radar-sub">Latest YES move</div></div>
          <svg viewBox="0 0 116 44" style="width:49%;max-width:116px;height:52px;color:{tone}" aria-label="Recent YES quote trend">{spark.replace('CURRENT',tone)}</svg></div>
          <div class="qe-radar-sub">{len(vals)} quote samples · live session</div>
        </div>''', unsafe_allow_html=True)

events = st.session_state.radar_events
st.markdown(f"**⚡ Recent alerts** · {len(events)} stored in this session")
if events:
    for event in events[:3]:
        when_local = event["time"].astimezone(CENTRAL).strftime("%I:%M:%S %p")
        st.markdown(f'<div class="qe-alert"><span><b>{html.escape(event["market"])}</b> · {html.escape(event["detail"])}</span><small>{when_local}</small></div>', unsafe_allow_html=True)
    with st.expander(f"📜 View full alert history ({len(events)})", expanded=False):
        for event in events:
            st.markdown(f"**{event['market']}** · {event['label']} · {event['detail']}")
            st.caption(f"{event['time'].astimezone(CENTRAL):%I:%M:%S %p %Z} · {event['ticker']}")
else:
    st.caption("No alerts yet. Keep the dashboard open for several refreshes.")

with st.expander("ℹ️ Radar details & thresholds", expanded=False):
    st.caption("Charts show sampled Kalshi YES quote movement relative to the first displayed sample for each active contract, not underlying asset price. Chart histories reset on contract changes or session restart.")
    st.caption("Recent-market arrows show official Kalshi settlement results: YES ↑ / NO ↓. These do not necessarily indicate underlying asset direction.")
    st.caption(f"Direction flip threshold: 0.5¢ each way · Sudden move: {sudden_cents}¢ per sample. Alerts are price observations, not trading recommendations.")
st.caption(f"Last refresh: {datetime.now(CENTRAL):%b %d, %Y · %I:%M:%S %p %Z} · Quotes polled every {refresh_seconds}s.")
st_autorefresh(interval=refresh_seconds * 1000, key="kalshi_grid_refresh")
