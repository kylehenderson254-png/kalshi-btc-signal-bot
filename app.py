"""Read-only Kalshi 15-minute market grid and dashboard-only Flip Radar."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import html
import re
import streamlit as st
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

st.set_page_config(page_title="Kalshi Market Command Center", page_icon="📡", layout="wide")

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
    with st.container(border=True):
        st.subheader(f"{emoji} {name}")
        try:
            markets = fetch_markets(series)
        except (requests.RequestException, ValueError) as exc:
            st.error("Kalshi data unavailable")
            st.caption(f"{series}: {exc}")
            return {"label": "Unavailable", "magnitude": 0, "direction": "—"}
        m = current_market(markets)
        if not m:
            st.warning("No open 15-minute contract returned")
            st.caption(f"Series: {series}. Confirm the series ticker and availability on Kalshi.")
            return {"label": "No market", "magnitude": 0, "direction": "—"}
        ticker = m.get("ticker", series)
        st.caption(m.get("title") or ticker)
        st.caption(f"Contract: {ticker}")
        closing = when(m.get("close_time") or m.get("latest_expiration_time"))
        opening = when(m.get("open_time"))
        now = datetime.now(timezone.utc)
        if opening and opening > now:
            st.info("Upcoming contract — not yet open")
        if closing:
            remaining = max(0, int((closing - now).total_seconds()))
            st.metric("⏱️ Kalshi contract countdown", f"{remaining // 60:02d}:{remaining % 60:02d}")
            st.caption(f"Closes at {closing.astimezone(CENTRAL):%I:%M:%S %p %Z} · refreshed every {refresh_seconds}s")
        else:
            st.metric("⏱️ Time remaining", "—")
        if closing:
            remaining_seconds = max(0, int((closing - now).total_seconds()))
            if remaining_seconds <= 60:
                st.error("⏳ FINAL MINUTE — signals can change quickly")
            elif remaining_seconds <= 180:
                st.warning("⏳ Less than 3 minutes remain")
        above_label, below_label, verified_direction = directional_labels(m)
        st.markdown("**Above / Below target — Kalshi bid and ask**" if verified_direction else "**Kalshi YES / NO bid and ask**")
        if not verified_direction:
            st.caption("Direction not confirmed by available contract-rule text. Showing official YES / NO labels for accuracy.")
        yes_bid = cents(price(m, "yes_bid_dollars", "yes_bid"))
        yes_ask = cents(price(m, "yes_ask_dollars", "yes_ask"))
        no_bid = cents(price(m, "no_bid_dollars", "no_bid"))
        no_ask = cents(price(m, "no_ask_dollars", "no_ask"))
        # Visual quote tiles only. Not buttons: no click action and no trading API credentials.
        yes_col, no_col = st.columns(2, gap="small")
        with yes_col:
            st.markdown(
                f'<div class="quote-tile yes-tile"><div class="quote-title">🟢 {above_label}</div>'
                f'<div class="quote-row"><span>BID</span><b>{html.escape(yes_bid)}</b></div>'
                f'<div class="quote-row"><span>ASK</span><b>{html.escape(yes_ask)}</b></div>'
                f'</div>', unsafe_allow_html=True,
            )
        with no_col:
            st.markdown(
                f'<div class="quote-tile no-tile"><div class="quote-title">🔴 {below_label}</div>'
                f'<div class="quote-row"><span>BID</span><b>{html.escape(no_bid)}</b></div>'
                f'<div class="quote-row"><span>ASK</span><b>{html.escape(no_ask)}</b></div>'
                f'</div>', unsafe_allow_html=True,
            )
        st.caption("Quote display only · No trading actions · ABOVE maps to YES only when verified from contract rules")
        show_pressure_radar(name, ticker)
        e, f = st.columns(2)
        e.metric("Target / strike", target(m))
        f.metric("Last YES", cents(price(m, "last_price_dollars", "last_price")))
        label, detail, count, source = scan(m, name)
        st.markdown("**🌅 Early call — live preview (not locked)**")
        if not closing or (opening and opening > now):
            st.caption("Waiting for an active contract with a closing time.")
        elif remaining > 300:
            early_bid = price(m, "yes_bid_dollars", "yes_bid")
            early_ask = price(m, "yes_ask_dollars", "yes_ask")
            if not verified_direction:
                st.caption("No early call: ABOVE/BELOW meaning is not verified from the contract rules.")
            elif early_bid is None or early_ask is None or not (0 <= early_bid <= early_ask <= 1) or early_ask - early_bid > 0.15 or (early_bid == 0 and early_ask == 1):
                st.caption("No early call: reliable YES bid/ask quotes are unavailable or spread is too wide.")
            else:
                early_mid = (early_bid + early_ask) / 2
                if early_mid >= 0.60:
                    st.success(f"EARLY LEAN: ABOVE TARGET ↑ · YES quote midpoint {early_mid * 100:.1f}¢")
                elif early_mid <= 0.40:
                    st.error(f"EARLY LEAN: BELOW TARGET ↓ · YES quote midpoint {early_mid * 100:.1f}¢")
                else:
                    st.info(f"EARLY LEAN: NO CLEAR CALL · YES quote midpoint {early_mid * 100:.1f}¢")
                st.caption("Live preview can change. The first eligible early signal is recorded separately and graded after official settlement; the 5-minute locked call remains independent.")
        else:
            st.caption("Early preview window has ended. Check the 5-minute locked call below.")
        early_saved = st.session_state.get("early_call_map", {}).get(ticker)
        if early_saved:
            st.caption(f"📜 Recorded early call: {early_saved['call']} · {early_saved['status'].upper()} · "
                       f"YES midpoint {early_saved['yes_midpoint'] * 100:.1f}¢ · {early_saved['locked_at']} UTC")
        prediction_panel(m, verified_direction, remaining if closing else None, ticker)
        st.markdown("**🔒 Automatic 5-minute locked call**")
        stored = st.session_state.get("locked_call_map", {}).get(ticker)
        if stored:
            label = "🟢 ABOVE" if stored["call"] == "ABOVE" else "🔴 BELOW"
            st.markdown(f"**LOCKED: {label}** · {stored['status'].upper()}")
            st.caption(f"Locked at {stored['locked_at']} · quote midpoint {stored['yes_midpoint'] * 100:.1f}¢")
        elif st.session_state.get("tracker_enabled"):
            row, reason = candidate(m, name, series)
            st.caption("Eligible for automatic lock now" if row else (reason or "Waiting for lock"))
        else:
            st.caption("Persistent call tracking not configured; see README setup instructions.")
        st.divider()
        st.markdown("**📡 Flip Radar**")
        if label.startswith("🔺") or label.startswith("🔻") or label.startswith("⚡"):
            st.warning(f"{label} — {detail}")
        else:
            st.caption(f"{label} · {detail}")
        st.caption(f"Samples: {count} · Source: {source}")
        direction, strength, magnitude = momentum_stats(m)
        st.markdown(f"**Momentum:** {direction} · {strength}")
        st.markdown("**📈 YES price trend & radar movements**")
        quote_samples = st.session_state.quote_history.get(ticker, [])
        if len(quote_samples) >= 2:
            # Each point is a sampled Kalshi quote, not a continuous trade-price feed.
            chart_rows = [
                {"Time": sample_time.astimezone(CENTRAL), "YES price (¢)": round(sample_price * 100, 2)}
                for sample_time, sample_price, _ in quote_samples
            ]
            st.line_chart(chart_rows, x="Time", y="YES price (¢)", height=190, use_container_width=True)
            st.caption("Rolling YES quote samples · newest at right · a rising line means YES became more expensive")
        else:
            st.caption("Chart warming up — keep this page open for at least two refreshes.")
        st.markdown("**Last 10 settled markets**")
        recent_arrows(series)
        with st.expander("Contract details and rules"):
            st.write(f"Market ticker: {ticker}")
            st.write(f"Closes (Central): {closing.astimezone(CENTRAL):%b %d, %I:%M:%S %p %Z}" if closing else "Close time not provided")
            st.write(m.get("rules_primary") or "Primary rules not included in response; check Kalshi.")
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

st.title("📡 Kalshi Market Command Center")
st.caption("15-minute markets · Kalshi contract-aligned countdowns · Dashboard-only Flip Radar · Read-only")
st.caption(f"⟳ Refresh interval: {refresh_seconds}s · API quotes may lag or be rate-limited · not a streaming connection")
st.info("Radar detects quote reversals. The experimental settlement call reflects Kalshi market-implied pricing, not a validated independent forecast. No trades are executed.")

# Streamlit columns collapse vertically on narrower screens.
left, right = st.columns(2, gap="medium")
statuses = []
with left:
    statuses.append(("Bitcoin", render_card(*SERIES[0])))
    statuses.append(("Silver", render_card(*SERIES[2])))
with right:
    statuses.append(("Gold", render_card(*SERIES[1])))
    statuses.append(("WTI Oil", render_card(*SERIES[3])))

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

st.subheader("🚨 Flip Radar — All Markets")
summary_cols = st.columns(4)
for col, (name, status) in zip(summary_cols, sorted(statuses, key=lambda s: [x[0] for x in SERIES].index(s[0]))):
    col.metric(name, status["label"])

st.subheader("📊 All-market momentum comparison")
st.caption("Latest absolute change in YES quote price, in cents per sample. Taller bars mean larger recent movement, not better trade odds.")
momentum_chart = [
    {"Market": name, "Movement (¢)": round(status["magnitude"], 2)}
    for name, status in sorted(statuses, key=lambda x: [item[0] for item in SERIES].index(x[0]))
]
st.bar_chart(momentum_chart, x="Market", y="Movement (¢)", height=250, use_container_width=True)

st.subheader("📉 Recent settled results comparison")
st.caption("Shows counts of YES and NO outcomes among up to 10 recent settled contracts for each series. These are contract outcomes, not asset up/down moves.")
result_counts = []
for series_name, series_ticker, _ in SERIES:
    try:
        recent = fetch_recent_results(series_ticker)
    except (requests.RequestException, ValueError):
        recent = []
    result_counts.append({
        "Market": series_name,
        "YES settled": sum(str(m.get("result", "")).lower() == "yes" for m in recent),
        "NO settled": sum(str(m.get("result", "")).lower() == "no" for m in recent),
    })
if any(row["YES settled"] or row["NO settled"] for row in result_counts):
    st.bar_chart(result_counts, x="Market", y=["YES settled", "NO settled"], height=250, use_container_width=True)
else:
    st.caption("Waiting for settled result data from Kalshi.")

st.markdown("**Momentum ranking**")
ranked = sorted(statuses, key=lambda item: item[1]["magnitude"], reverse=True)
for index, (name, stat) in enumerate(ranked, 1):
    st.write(f"{index}. **{name}** — {stat['direction']} · {stat['magnitude']:.1f}¢ latest change")
st.caption("Rankings measure absolute YES quote movement between samples, not expected profitability or win probability.")

st.markdown("**Recent dashboard alerts**")
if st.session_state.radar_events:
    for event in st.session_state.radar_events[:12]:
        st.markdown(f"**{event['label']} · {event['market']}** — {event['detail']}")
        st.caption(f"{event['time'].astimezone(CENTRAL):%I:%M:%S %p %Z} · {event['ticker']}")
else:
    st.caption("No alerts yet. Keep the dashboard open for several refreshes.")

st.caption("Recent-market arrows show official Kalshi settlement results: YES ↑ / NO ↓. These are not automatically ABOVE / BELOW unless contract rules establish that mapping.")
st.caption("Radar thresholds: direction change ≥0.5¢ each way; sudden move ≥" + str(sudden_cents) + "¢ per sample. Quote history lasts only for this Streamlit session. These are price-movement observations, not trading recommendations.")
st.caption(f"Last refresh: {datetime.now(CENTRAL):%b %d, %Y · %I:%M:%S %p %Z} · Contract countdowns align to Kalshi close timestamps; updates are not guaranteed every {refresh_seconds}s.")
st_autorefresh(interval=refresh_seconds * 1000, key="kalshi_grid_refresh")
