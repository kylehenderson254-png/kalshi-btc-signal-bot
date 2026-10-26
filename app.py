from datetime import datetime
from zoneinfo import ZoneInfo
from collections import deque

import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

TZ = ZoneInfo("America/Chicago")
API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
HISTORY_LIMIT = 12
SUDDEN_MOVE_THRESHOLD = 0.04  # 4 probability points = 4¢ on a $1 contract

SERIES = [
    ("Bitcoin", "KXBTC15M", "₿"),
    ("Gold", "KXGOLD15M", "🥇"),
    ("Silver", "KXSILVER15M", "🥈"),
    ("WTI Oil", "KXWTI15M", "🛢️"),
]

st.set_page_config(page_title="Kalshi 15-Minute Market Monitor", page_icon="📈", layout="centered")
st.title("📈 Kalshi 15-Minute Market Monitor")
st.caption("Read-only market monitor • Dashboard-only Flip Radar • No orders placed")

st.info(
    "Tracks Kalshi's available 15-minute contracts and their YES/NO quotes. Flip Radar watches changes in the "
    "YES quote midpoint across dashboard refreshes and flags possible reversals or sudden moves. These are "
    "price-movement alerts, not predictions of the final outcome."
)

with st.sidebar:
    st.header("Dashboard")
    refresh_seconds = st.selectbox("Auto-refresh interval", [10, 15, 30, 60], index=1)
    st.caption("Flip Radar compares quotes between refreshes. Keep this page open to build its short-term history.")
    st.caption("Sudden-move threshold: 4¢ change in the YES quote midpoint between refreshes.")

# Session-only history: it resets if the browser session/app restarts.
if "flip_history" not in st.session_state:
    st.session_state.flip_history = {}
if "flip_alerts" not in st.session_state:
    st.session_state.flip_alerts = {}

@st.cache_data(ttl=8, show_spinner=False)
def get_active_markets(series_ticker):
    response = requests.get(
        f"{API_BASE}/markets",
        params={"series_ticker": series_ticker, "status": "open", "limit": 1000},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    markets = data.get("markets", [])
    now = datetime.now().astimezone()

    def close_time(m):
        value = m.get("close_time") or m.get("latest_expiration_time") or ""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    live = []
    for market in markets:
        ct = close_time(market)
        if ct is None or ct > now:
            live.append(market)
    live.sort(key=lambda m: close_time(m) or now)
    return live


def as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_dollars(value):
    if value is None or value == "":
        return "Not provided"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_contract_price(value):
    number = as_float(value)
    if number is None:
        return "—"
    return f"{number * 100:.1f}¢"


def fmt_market_price(market, dollars_key, cents_key):
    """Prefer Kalshi's dollar-denominated field; otherwise display integer cents correctly."""
    dollars = as_float(market.get(dollars_key))
    if dollars is not None:
        return fmt_contract_price(dollars)
    cents = as_float(market.get(cents_key))
    if cents is not None:
        return f"{cents:.1f}¢"
    return "—"


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(TZ)
    except (ValueError, TypeError):
        return None


def market_target(m):
    for key in ("floor_strike", "functional_strike", "custom_strike", "cap_strike"):
        if m.get(key) not in (None, ""):
            return fmt_dollars(m[key])
    return m.get("yes_sub_title") or m.get("subtitle") or "Check contract rules"


def quote_midpoint(m):
    """Return YES midpoint as a 0..1 probability-like contract price, when available."""
    bid = as_float(m.get("yes_bid_dollars"))
    ask = as_float(m.get("yes_ask_dollars"))
    last = as_float(m.get("last_price_dollars"))
    if bid is not None and ask is not None and 0 <= bid <= 1 and 0 <= ask <= 1 and ask >= bid:
        return (bid + ask) / 2, "YES bid/ask midpoint"
    if last is not None and 0 <= last <= 1:
        return last, "last YES price (midpoint unavailable)"
    # Some API responses may only expose integer cent fields.
    bid_cents = as_float(m.get("yes_bid"))
    ask_cents = as_float(m.get("yes_ask"))
    if bid_cents is not None and ask_cents is not None and 0 <= bid_cents <= 100 and 0 <= ask_cents <= 100 and ask_cents >= bid_cents:
        return ((bid_cents + ask_cents) / 2) / 100, "YES bid/ask midpoint"
    last_cents = as_float(m.get("last_price"))
    if last_cents is not None and 0 <= last_cents <= 100:
        return last_cents / 100, "last YES price (midpoint unavailable)"
    return None, "No usable YES quote yet"


def record_and_detect_flip(series_name, market):
    ticker = market.get("ticker") or f"{series_name}:unknown"
    price, source = quote_midpoint(market)
    now = datetime.now(TZ)
    history = st.session_state.flip_history.setdefault(ticker, [])
    if price is not None:
        # Avoid duplicating identical samples when reruns occur before the API quote changes.
        if not history or (now - history[-1][0]).total_seconds() >= max(5, refresh_seconds - 2):
            history.append((now, price))
            del history[:-HISTORY_LIMIT]

    alert = None
    if len(history) >= 3:
        older = history[-2][1] - history[-3][1]
        latest = history[-1][1] - history[-2][1]
        if older > 0.005 and latest < -0.005:
            alert = ("🔻 Bearish flip", f"YES midpoint turned down after rising ({latest * 100:+.1f}¢ on latest sample).")
        elif older < -0.005 and latest > 0.005:
            alert = ("🔺 Bullish flip", f"YES midpoint turned up after falling ({latest * 100:+.1f}¢ on latest sample).")

    if len(history) >= 2:
        move = history[-1][1] - history[-2][1]
        if abs(move) >= SUDDEN_MOVE_THRESHOLD:
            direction = "jumped up" if move > 0 else "dropped"
            sudden = ("⚡ Sudden move", f"YES midpoint {direction} {abs(move) * 100:.1f}¢ between samples.")
            # Prefer showing a sudden move if it occurred on this latest sample.
            if alert:
                alert = (f"{alert[0]} + ⚡", f"{alert[1]} Also, midpoint {direction} {abs(move) * 100:.1f}¢.")
            else:
                alert = sudden

    if alert:
        st.session_state.flip_alerts[ticker] = {
            "name": series_name,
            "ticker": ticker,
            "label": alert[0],
            "detail": alert[1],
            "time": now,
            "price": price,
        }
    return price, source, history, alert


def render_market(name, ticker, emoji):
    st.subheader(f"{emoji} {name}")
    try:
        markets = get_active_markets(ticker)
    except requests.RequestException as exc:
        st.error(f"Could not load {name} from Kalshi: {exc}")
        return

    if not markets:
        st.warning(f"No open market was returned for {ticker}. The series ticker may differ, no contract may be open, or the API may be temporarily unavailable.")
        st.caption(f"Series requested: {ticker}")
        return

    for idx, m in enumerate(markets[:3]):
        close_dt = parse_time(m.get("close_time") or m.get("latest_expiration_time"))
        open_dt = parse_time(m.get("open_time"))
        label = "Nearest closing contract" if idx == 0 else f"Upcoming contract {idx + 1}"
        with st.container(border=True):
            st.markdown(f"**{label}**")
            st.write(m.get("title") or m.get("subtitle") or ticker)
            market_ticker = m.get("ticker", ticker)
            st.caption(f"Market ticker: {market_ticker}")
            a, b = st.columns(2)
            a.metric("YES bid", fmt_market_price(m, "yes_bid_dollars", "yes_bid"))
            b.metric("YES ask", fmt_market_price(m, "yes_ask_dollars", "yes_ask"))
            c, d = st.columns(2)
            c.metric("NO bid", fmt_market_price(m, "no_bid_dollars", "no_bid"))
            d.metric("NO ask", fmt_market_price(m, "no_ask_dollars", "no_ask"))
            e, f = st.columns(2)
            e.metric("Target / strike", market_target(m))
            f.metric("Last price", fmt_market_price(m, "last_price_dollars", "last_price"))

            if idx == 0:
                price, source, history, alert = record_and_detect_flip(name, m)
                g, h = st.columns(2)
                g.metric("YES midpoint", fmt_contract_price(price), help=source)
                h.metric("Radar samples", len(history))
                if alert:
                    st.warning(f"{alert[0]} — {alert[1]}")
                elif len(history) < 3:
                    st.caption("Flip Radar warming up: collecting quote samples.")
                else:
                    st.caption("No new flip or sudden-move alert on the latest sample.")

            if open_dt:
                st.caption(f"Opens: {open_dt.strftime('%b %d, %I:%M:%S %p %Z')}")
            if close_dt:
                seconds = int((close_dt - datetime.now(TZ)).total_seconds())
                if seconds > 0:
                    st.caption(f"Closes: {close_dt.strftime('%b %d, %I:%M:%S %p %Z')} • {seconds//60}m {seconds%60}s remaining")
                else:
                    st.caption(f"Close time: {close_dt.strftime('%b %d, %I:%M:%S %p %Z')}")
            with st.expander("Contract rules / settlement details"):
                st.write(m.get("rules_primary") or "Primary rules not included in this API response.")
                st.write(m.get("rules_secondary") or "Secondary rules not included in this API response.")
                st.write(f"Status: {m.get('status', 'not provided')}")
                st.write(f"Result: {m.get('result') or 'Not settled'}")

    st.caption("Quotes shown are Kalshi contract prices (YES/NO), not the underlying asset's dollar price.")


st.subheader("📡 Flip Radar")
st.caption("Dashboard-only alerts • Uses live Kalshi YES quotes • No trades or external notifications")
radar_cols = st.columns(2)
for i, (name, ticker, emoji) in enumerate(SERIES):
    with radar_cols[i % 2]:
        st.metric(f"{emoji} {name}", "Scanning" if ticker else "Unavailable")

for name, ticker, emoji in SERIES:
    render_market(name, ticker, emoji)

st.divider()
st.subheader("🚨 Recent Flip Radar Alerts")
alerts = sorted(st.session_state.flip_alerts.values(), key=lambda a: a["time"], reverse=True)
if alerts:
    for alert in alerts[:12]:
        with st.container(border=True):
            st.markdown(f"**{alert['label']} · {alert['name']}**")
            st.write(alert["detail"])
            st.caption(f"{alert['ticker']} • {alert['time'].strftime('%I:%M:%S %p %Z')}")
else:
    st.info("No flips or sudden moves flagged yet. The radar needs a few refreshes with usable quotes before it can compare direction.")

st.divider()
st.subheader("How to read Flip Radar")
st.markdown(
    "- **Bullish flip:** YES midpoint moved down on a prior sample and then up on the latest sample.\n"
    "- **Bearish flip:** YES midpoint moved up on a prior sample and then down on the latest sample.\n"
    "- **Sudden move:** YES midpoint changed by at least 4¢ between two dashboard samples.\n\n"
    "These labels describe recent contract-price movement only. They do not mean the contract will settle YES or NO. "
    "History is kept in this browser session and resets when the session restarts."
)
st.warning(
    "The radar is a simple monitoring aid, not a validated forecasting model. Market tickers, fields, availability, "
    "and settlement rules can change. Confirm contract rules before using quotes to make decisions."
)
st.caption(f"Last page render: {datetime.now(TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z')}")

st_autorefresh(interval=refresh_seconds * 1000, key="kalshi_refresh")
