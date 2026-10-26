"""Read-only market-flow radar using public market-data endpoints."""
import statistics
import requests
import streamlit as st

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com"
HEADERS = {"User-Agent": "ReadOnlyMarketPressureDashboard/1.0"}

def get_json(url, params=None):
    response = requests.get(url, params=params, headers=HEADERS, timeout=8)
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl=15, show_spinner=False)
def kalshi_trades(ticker):
    result = get_json(f"{KALSHI}/markets/trades", {"ticker": ticker, "limit": 100})
    return [t for t in result.get("trades", []) if t.get("ticker") == ticker]

@st.cache_data(ttl=15, show_spinner=False)
def kalshi_orderbook(ticker):
    result = get_json(f"{KALSHI}/markets/{ticker}/orderbook", {"depth": 20})
    return result.get("orderbook", {})

@st.cache_data(ttl=15, show_spinner=False)
def coinbase_trades(product):
    result = get_json(f"{COINBASE}/products/{product}/trades", {"limit": 100})
    return result if isinstance(result, list) else []

def size_of(trade):
    try:
        return float(trade.get("count_fp") or trade.get("count") or 0)
    except (ValueError, TypeError):
        return 0.0

def large_print(sizes, units):
    if len(sizes) < 10:
        st.caption("Large-trade watch: fewer than 10 usable trades.")
        return
    median = statistics.median(sizes)
    biggest = max(sizes)
    if median > 0 and biggest >= median * 5:
        st.warning(f"🐋 Large trade flag: {biggest:,.3f} {units} ({biggest / median:.1f}× median size). Identity unknown.")
    else:
        st.caption("🐋 No 5×-median trade found in the recent sample.")

def pressure(buy, sell, units):
    total = buy + sell
    if total <= 0:
        st.caption("No usable trade-side data in the recent sample.")
        return
    share = buy / total
    st.progress(share, text=f"Buy-initiated {share:.0%} · Sell-initiated {1-share:.0%}")
    st.caption(f"Sampled volume: {buy:,.3f} {units} buy-initiated / {sell:,.3f} {units} sell-initiated.")

def level_volume(levels):
    volume = 0.0
    biggest = 0.0
    for level in levels:
        try:
            qty = float(level[1] if isinstance(level, (list, tuple)) else level.get("count", 0))
        except (ValueError, TypeError, IndexError, AttributeError):
            continue
        if qty > 0:
            volume += qty
            biggest = max(biggest, qty)
    return volume, biggest

def show_pressure_radar(name, ticker):
    with st.expander("🐋 Buying / Selling Pressure & Whale Radar"):
        st.caption("Public market-data samples; not a forecast. Large trades do not identify whales.")
        st.markdown("**Kalshi contract trading pressure**")
        try:
            trades = kalshi_trades(ticker)
            yes = no = 0.0
            sizes = []
            for t in trades:
                side = str(t.get("taker_side") or "").lower()
                qty = size_of(t)
                if qty <= 0:
                    continue
                if side == "yes":
                    yes += qty
                elif side == "no":
                    no += qty
                else:
                    continue
                sizes.append(qty)
            pressure(yes, no, "contracts")
            large_print(sizes, "contracts")
            st.caption("YES/NO taker-side activity is contract demand, not underlying-asset buying or selling.")
        except (requests.RequestException, ValueError, TypeError) as exc:
            st.caption(f"Kalshi trade data unavailable ({type(exc).__name__}).")
        try:
            book = kalshi_orderbook(ticker)
            y, ybig = level_volume(book.get("yes_dollars") or book.get("yes") or [])
            n, nbig = level_volume(book.get("no_dollars") or book.get("no") or [])
            if y + n > 0:
                st.caption(f"Visible resting bids (up to 20 levels): YES {y:,.0f} contracts / NO {n:,.0f} contracts.")
                st.caption(f"Largest visible bid level: {max(ybig, nbig):,.0f} contracts. Orders can be cancelled without trading.")
            else:
                st.caption("No usable Kalshi order-book levels returned.")
        except (requests.RequestException, ValueError, TypeError) as exc:
            st.caption(f"Kalshi order book unavailable ({type(exc).__name__}).")

        st.markdown("**Underlying market trading pressure**")
        if name == "Bitcoin":
            product, units = "BTC-USD", "BTC"
            st.caption("Coinbase BTC-USD spot, recent trades only; not the entire BTC market.")
        elif name == "Gold":
            product, units = "PAXG-USD", "PAXG"
            st.caption("Coinbase PAXG-USD token trades, a proxy only — NOT spot gold futures or bullion flow.")
        else:
            st.info(f"No verified underlying trade-side feed configured for {name}. Showing Kalshi pressure only, not fabricated underlying data.")
            return
        try:
            trades = coinbase_trades(product)
            buy = sell = 0.0
            sizes = []
            for t in trades:
                try:
                    qty = float(t.get("size") or 0)
                except (ValueError, TypeError):
                    continue
                if qty <= 0:
                    continue
                # Coinbase 'side' denotes maker side, opposite the aggressor.
                maker = str(t.get("side") or "").lower()
                if maker == "sell":
                    buy += qty
                elif maker == "buy":
                    sell += qty
                else:
                    continue
                sizes.append(qty)
            pressure(buy, sell, units)
            large_print(sizes, units)
        except (requests.RequestException, ValueError, TypeError) as exc:
            st.info(f"Underlying trade data unavailable ({type(exc).__name__}); no pressure inferred.")
