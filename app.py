import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

TZ = ZoneInfo("America/Chicago")
BINANCE_BASE = "https://data-api.binance.vision"

st.set_page_config(page_title="BTC 15-Minute Signal", page_icon="₿", layout="centered")

st.title("₿ BTC Target Signal")
st.caption("Experimental probability estimate • Signals only • No orders placed")

st.info(
    "This first version uses a simple statistical baseline, not a trained or validated AI model. "
    "Its probability estimates are assumptions and may be poorly calibrated. Use it for observation "
    "and paper testing only."
)

with st.sidebar:
    st.header("Contract settings")
    target = st.number_input(
        "Settlement target (USD)",
        min_value=1.0,
        value=85000.0,
        step=50.0,
        format="%.2f",
        help="Enter the exact dollar target shown in your Kalshi contract."
    )
    now_local = datetime.now(TZ).replace(tzinfo=None, second=0, microsecond=0)
    settlement_local = st.datetime_input(
        "Contract settlement time (Central)",
        value=now_local + pd.Timedelta(minutes=15),
        help="Choose the actual settlement time shown by the contract. Central Time is used."
    )
    threshold = st.slider(
        "Minimum probability for a directional signal",
        min_value=50, max_value=80, value=60, step=1
    ) / 100
    refresh_seconds = st.selectbox("Refresh interval", [10, 15, 30, 60], index=1)

@st.cache_data(ttl=8, show_spinner=False)
def get_market_data():
    ticker_resp = requests.get(
        f"{BINANCE_BASE}/api/v3/ticker/price",
        params={"symbol": "BTCUSDT"},
        timeout=8
    )
    ticker_resp.raise_for_status()
    spot = float(ticker_resp.json()["price"])

    candles_resp = requests.get(
        f"{BINANCE_BASE}/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "1m", "limit": 120},
        timeout=8
    )
    candles_resp.raise_for_status()
    raw = candles_resp.json()
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base",
        "taker_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    # Ignore the currently forming candle for the volatility/momentum estimate.
    df = df.iloc[:-1].copy()
    return spot, df

def normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

try:
    spot, candles = get_market_data()
    now = datetime.now(TZ)
    settle = settlement_local.replace(tzinfo=TZ)
    seconds_left = (settle - now).total_seconds()
    minutes_left = max(seconds_left / 60.0, 0.0)

    returns = candles["close"].pct_change().dropna()
    recent = returns.tail(15)
    if len(recent) < 10:
        st.error("Not enough recent BTC candles to estimate volatility. Try again shortly.")
        st.stop()

    # A deliberately simple baseline: recent mean return is drift; recent standard
    # deviation is one-minute volatility. This is not a validated forecast.
    drift_per_min = float(recent.mean())
    sigma_per_min = float(recent.std(ddof=1))

    st.subheader("Live market snapshot")
    c1, c2 = st.columns(2)
    c1.metric("BTC spot (Binance BTC/USDT)", f"${spot:,.2f}")
    c2.metric("Target", f"${target:,.2f}", delta=f"${spot-target:,.2f} vs target")

    if seconds_left <= 0:
        st.warning("The selected settlement time has passed. Choose the next contract's actual settlement time.")
        st.stop()

    # Approximate future log-price distribution; floor volatility to avoid division by zero.
    log_distance = math.log(target / spot)
    horizon = max(minutes_left, 1e-6)
    sigma_horizon = max(sigma_per_min, 1e-8) * math.sqrt(horizon)
    expected_log_return = drift_per_min * horizon
    z = (log_distance - expected_log_return) / sigma_horizon
    p_above = 1.0 - normal_cdf(z)
    p_below = 1.0 - p_above

    st.subheader("Estimated finish probability")
    a, b = st.columns(2)
    a.metric("Finish ABOVE target", f"{p_above*100:.1f}%")
    b.metric("Finish BELOW target", f"{p_below*100:.1f}%")
    st.progress(min(max(p_above, 0.0), 1.0), text="Estimated probability of finishing above target")

    if p_above >= threshold:
        signal = "UP / ABOVE"
        st.success(f"Model signal: {signal} — {p_above:.1%} estimated probability")
    elif p_below >= threshold:
        signal = "DOWN / BELOW"
        st.warning(f"Model signal: {signal} — {p_below:.1%} estimated probability")
    else:
        signal = "NO TRADE"
        st.info("Model signal: NO TRADE — neither side meets your probability threshold.")

    st.caption(
        f"Time remaining: {int(seconds_left // 60)}m {int(seconds_left % 60)}s • "
        f"Settlement time: {settle.strftime('%Y-%m-%d %I:%M:%S %p %Z')}"
    )

    with st.expander("Model details and limitations"):
        st.write(f"Recent 15-minute average return per minute: {drift_per_min*100:.5f}%")
        st.write(f"Recent one-minute return volatility: {sigma_per_min*100:.5f}%")
        st.write(f"Estimated probability threshold: {threshold:.0%}")
        st.write(
            "The estimate assumes recent returns and volatility remain representative until settlement. "
            "It does not yet use Kalshi prices, official settlement feeds, order-book imbalance, "
            "or a trained model. Binance BTC/USDT may not match the contract's settlement reference. "
            "Do not treat this as a reliable trading edge."
        )

    st.subheader("Safe testing checklist")
    st.markdown(
        "- Record each forecast and the exact time it was made.\n"
        "- After settlement, record the official contract outcome.\n"
        "- Track calibration and Brier score over many non-overlapping windows.\n"
        "- Compare against a simple baseline before considering any live use.\n"
        "- Do not add automatic orders or put API secrets in this dashboard."
    )

except requests.RequestException as exc:
    st.error(f"Could not load public BTC market data: {exc}")
except (ValueError, KeyError, IndexError) as exc:
    st.error(f"Market data response was unexpected: {exc}")

st.caption("Public market data source: Binance. Refreshing this page does not place trades.")

# Streamlit's built-in auto-refresh component can be added later if desired.
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh_seconds * 1000, key="btc_refresh")
except ImportError:
    st.caption("For automatic refresh, install all packages from requirements.txt.")
