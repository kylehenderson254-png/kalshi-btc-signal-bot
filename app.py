"""QUEEN'S EDGE | 254 — read-only compact Kalshi market monitor.

No orders are submitted. Underlying prices are explicitly labeled as indicative
unless a settlement-matched feed is configured. No fabricated oil prices.
"""
from datetime import datetime, timezone
import os
import re
import time
from urllib.parse import urlparse
import requests
import streamlit as st

st.set_page_config(page_title="QUEEN'S EDGE | 254", page_icon="👑", layout="wide", initial_sidebar_state="collapsed")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"🛢️ WTI Oil": "KXWTI15M", "₿ Bitcoin": "KXBTC15M"}
SESSION = requests.Session()

st.markdown("""<style>
.stApp {background:#0c0916;color:#f5f1ff}
.block-container {max-width:1150px;padding-top:1.1rem}
[data-testid="stMetric"] {background:#1b142b;border:1px solid #5c3985;border-radius:14px;padding:14px}
[data-testid="stMetricLabel"] {color:#cbb5ef}
h1,h2,h3 {color:#e8d7ff!important}
div.stAlert {border-radius:12px}
</style>""", unsafe_allow_html=True)


def secret(name, default=""):
    try:
        return str(st.secrets.get(name, os.getenv(name, default)))
    except Exception:
        return os.getenv(name, default)


def num(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


@st.cache_data(ttl=8, show_spinner=False)
def markets_for(series):
    r = requests.get(f"{KALSHI}/markets", params={"series_ticker": series, "status": "open", "limit": 200}, timeout=9)
    r.raise_for_status()
    return r.json().get("markets", [])


def choose_current(markets):
    now = datetime.now(timezone.utc)
    eligible = [m for m in markets if dt(m.get("close_time")) and dt(m["close_time"]) > now and (not dt(m.get("open_time")) or dt(m["open_time"]) <= now)]
    return min(eligible, key=lambda m: dt(m["close_time"])) if eligible else None


def strike(m):
    for k in ("floor_strike", "functional_strike", "custom_strike", "cap_strike"):
        v = num(m.get(k))
        if v is not None:
            return v
    return None


def contract_cents(m, side, bid_or_ask):
    # Prefer fixed-point decimal dollar fields; legacy integer fields are cents.
    prefix = "yes" if side == "ABOVE / YES" else "no"
    fp = num(m.get(f"{prefix}_{bid_or_ask}_dollars"))
    if fp is not None:
        return fp * 100
    return num(m.get(f"{prefix}_{bid_or_ask}"))


def verified_above_yes(m):
    rule = " ".join(str(m.get(k) or "") for k in ("rules_primary", "rules_secondary", "yes_sub_title")).lower()
    return bool(re.search(r"(?:yes.{0,100}(?:above|higher|greater|at least)|(?:above|higher|greater|at least).{0,100}(?:yes|resolves))", rule))


@st.cache_data(ttl=10, show_spinner=False)
def btc_spot():
    r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=7)
    r.raise_for_status()
    return float(r.json()["data"]["amount"]), datetime.now(timezone.utc), "Coinbase BTC-USD spot (indicative; not verified settlement feed)"


@st.cache_data(ttl=10, show_spinner=False)
def configured_feed(url, token):
    """Optional licensed JSON price endpoint returning {price, timestamp, source}.
    URL and bearer token are provided by the user in Streamlit secrets.
    """
    if not url.startswith("https://"):
        raise ValueError("Underlying feed URL must use HTTPS")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(url, headers=headers, timeout=8)
    r.raise_for_status()
    body = r.json()
    price = num(body.get("price"))
    stamp = dt(body.get("timestamp"))
    if price is None or price <= 0 or stamp is None:
        raise ValueError("Feed must provide positive numeric price and ISO timestamp")
    return price, stamp, str(body.get("source") or urlparse(url).netloc)


def remaining(m):
    close = dt(m.get("close_time"))
    if not close:
        return "—"
    seconds = max(0, int((close - datetime.now(timezone.utc)).total_seconds()))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


st.title("👑 QUEEN'S EDGE | 254")
st.caption("MARKET INTELLIGENCE · PAPER-ONLY · NO LIVE ORDERS")
left, right = st.columns([2, 1])
with left:
    label = st.radio("Market", list(SERIES), horizontal=True)
with right:
    refresh = st.selectbox("Refresh interval", [10, 15, 30, 60], index=1, format_func=lambda x: f"{x} seconds")
series = SERIES[label]

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh * 1000, key="qe_auto_refresh")
except ImportError:
    st.caption("Install streamlit-autorefresh to enable unattended contract rollover.")

try:
    market = choose_current(markets_for(series))
    fetch_error = None
except Exception as exc:
    market, fetch_error = None, str(exc)

if fetch_error:
    st.error(f"Kalshi market request failed: {fetch_error}")
if market is None:
    st.warning("NO ACTIVE VERIFIED CONTRACT / NO ENTRY. The app will check again at the next refresh. No market target is invented.")
    st.stop()

ticker = market.get("ticker", "—")
tgt = strike(market)
close = dt(market.get("close_time"))
st.markdown(f"**ACTIVE KALSHI CONTRACT:** `{ticker}`")
st.caption(f"Closes: {close.strftime('%b %d, %Y %I:%M:%S %p UTC') if close else 'Unknown'} · Automatically selects the earliest currently open, unexpired contract in {series}.")

underlying, observed_at, source, feed_error = None, None, None, None
url_key = "QE_OIL_FEED_URL" if series == "KXWTI15M" else "QE_BTC_FEED_URL"
token_key = "QE_OIL_FEED_TOKEN" if series == "KXWTI15M" else "QE_BTC_FEED_TOKEN"
try:
    if secret(url_key):
        underlying, observed_at, source = configured_feed(secret(url_key), secret(token_key))
    elif series == "KXBTC15M":
        underlying, observed_at, source = btc_spot()
    else:
        feed_error = "No verified WTI Oil underlying-price provider configured. Set QE_OIL_FEED_URL in Streamlit secrets to enable the comparison."
except Exception as exc:
    feed_error = f"Underlying feed unavailable: {exc}"

age = (datetime.now(timezone.utc) - observed_at).total_seconds() if observed_at else None
fresh = age is not None and -10 <= age <= 45
comparison_ready = underlying is not None and tgt is not None and fresh
fmt = lambda v: f"${v:,.3f}" if series == "KXWTI15M" else f"${v:,.2f}"

c1, c2, c3, c4 = st.columns(4)
c1.metric("UNDERLYING PRICE", fmt(underlying) if underlying is not None else "UNAVAILABLE")
c2.metric("KALSHI TARGET", fmt(tgt) if tgt is not None else "UNAVAILABLE")
c3.metric("DISTANCE TO TARGET", (f"{fmt(abs(underlying - tgt))} {'ABOVE' if underlying > tgt else 'BELOW' if underlying < tgt else 'AT TARGET'}") if comparison_ready else "UNAVAILABLE")
c4.metric("TIME REMAINING", remaining(market))

if feed_error:
    st.warning(feed_error)
if underlying is not None:
    st.caption(f"Underlying source: {source} · observed {observed_at.strftime('%H:%M:%S UTC')} · age {age:.0f}s. Source is not automatically equivalent to Kalshi's settlement reference.")
if not fresh:
    st.error("DATA UNAVAILABLE / NO ENTRY — missing or stale underlying feed.")

st.subheader("ABOVE / BELOW · Kalshi contract quotes")
if not verified_above_yes(market):
    st.warning("YES/ABOVE mapping has not been confirmed from the returned contract rules. Verify the Kalshi market's settlement details before interpreting sides.")
for col, side in zip(st.columns(2), ("ABOVE / YES", "BELOW / NO")):
    bid = contract_cents(market, side, "bid")
    ask = contract_cents(market, side, "ask")
    with col:
        st.markdown(f"**{side}**")
        st.metric("Buy ask", f"{ask:.1f}¢" if ask is not None else "—")
        st.caption(f"Bid: {bid:.1f}¢" if bid is not None else "Bid unavailable")

with st.expander("Paper-trade cost and payout (not a trade)", expanded=False):
    budget = st.number_input("Maximum hypothetical stake ($)", min_value=1.0, max_value=10000.0, value=10.0, step=1.0)
    side = st.selectbox("Paper side", ("ABOVE / YES", "BELOW / NO"))
    ask = contract_cents(market, side, "ask")
    if ask is not None and 0 < ask < 100:
        count = int(budget // (ask / 100))
        cost = count * ask / 100
        payout = count * 1.0
        st.write(f"{count} contracts · Cost ${cost:.2f} · Gross winning payout ${payout:.2f} · Gross winning profit ${payout-cost:.2f} · Maximum stake loss ${cost:.2f}. Fees excluded.")
    else:
        st.warning("Valid live ask unavailable; cannot size hypothetical contracts.")

st.markdown("### ENTRY ASSESSMENT: **WAIT / NO ENTRY**")
st.caption("This build intentionally does not invent a prediction, STRONG LOCK, settlement-matched price, or profitable entry. Contract prices are not the underlying chart. No automated orders.")
with st.expander("View live price history (optional)", expanded=False):
    if comparison_ready:
        key = f"qe_underlying_history_{series}_{ticker}"
        history = st.session_state.setdefault(key, [])
        history.append({"time": datetime.now(timezone.utc), "underlying": underlying, "target": tgt})
        st.session_state[key] = history[-500:]
        if len(history) > 1:
            import pandas as pd
            st.line_chart(pd.DataFrame(history).set_index("time")[["underlying", "target"]], use_container_width=True)
        else:
            st.info("History will populate while this browser session remains open. No fabricated historical prices.")
    else:
        st.info("Chart unavailable until a fresh underlying price and a numeric target are available.")

st.caption("Historical Supabase trackers are not deleted or modified by this standalone dashboard. To show your old strategy history here, the existing tracker modules must be integrated in a follow-up revision.")
