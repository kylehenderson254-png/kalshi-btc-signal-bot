
"""QUEEN'S EDGE V3 selective observation gate. Paper only; never places orders.

These rules are experimental filters, NOT calibrated probabilities or proven edge.
Coinbase spot/candles are not the Kalshi settlement reference.
"""
from datetime import datetime, timezone
import math
import pandas as pd
import streamlit as st


def _finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def assess(market, strike, spot, candles, seconds_left, verified, quote_fn, quote_stale=False):
    reasons = []
    if not market or not verified:
        reasons.append('Contract or YES/NO settlement mapping unverified')
    if quote_stale:
        reasons.append('Kalshi market request failed; cached quote')
    if strike is None or spot is None:
        reasons.append('Target or indicative spot price unavailable')
    if seconds_left is None or not 180 <= seconds_left <= 600:
        reasons.append('Outside conservative 3–10 minute observation window')
    if candles is None or len(candles) < 35:
        reasons.append('Not enough Bitcoin candle history')
    if reasons:
        return {'decision': 'SKIP / NO ENTRY', 'side': None, 'reasons': reasons}

    df = candles.copy().sort_values('time')
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['high'] = pd.to_numeric(df['high'], errors='coerce')
    df['low'] = pd.to_numeric(df['low'], errors='coerce')
    df = df.dropna(subset=['close', 'high', 'low'])
    if len(df) < 35:
        return {'decision': 'SKIP / NO ENTRY', 'side': None, 'reasons': ['Incomplete candle data']}
    last_time = pd.to_datetime(df['time'].iloc[-1], utc=True, errors='coerce')
    if pd.isna(last_time) or (datetime.now(timezone.utc) - last_time.to_pydatetime()).total_seconds() > 180:
        reasons.append('Underlying candle feed is stale')
    close = df['close']
    e9 = close.ewm(span=9, adjust=False).mean()
    e21 = close.ewm(span=21, adjust=False).mean()
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rsi = 100 if loss.iloc[-1] == 0 and gain.iloc[-1] > 0 else (
        50 if loss.iloc[-1] == 0 else 100 - 100/(1+gain.iloc[-1]/loss.iloc[-1]))
    direction = 'ABOVE' if spot > strike else 'BELOW' if spot < strike else None
    distance = abs(spot - strike)
    if direction is None or distance < max(50, spot * .0007):
        reasons.append('Price too close to Kalshi target')
    rising = e9.iloc[-1] > e21.iloc[-1] and e9.iloc[-1] > e9.iloc[-4] and close.iloc[-1] > close.iloc[-4]
    falling = e9.iloc[-1] < e21.iloc[-1] and e9.iloc[-1] < e9.iloc[-4] and close.iloc[-1] < close.iloc[-4]
    if direction == 'ABOVE' and (not rising or not 53 <= rsi <= 68):
        reasons.append('UP momentum/EMA/RSI confirmation missing')
    if direction == 'BELOW' and (not falling or not 32 <= rsi <= 47):
        reasons.append('DOWN momentum/EMA/RSI confirmation missing')
    # A large recent counter-move is a reversal warning.
    if direction == 'ABOVE' and close.iloc[-1] < close.iloc[-2] < close.iloc[-3]:
        reasons.append('Recent bearish reversal warning')
    if direction == 'BELOW' and close.iloc[-1] > close.iloc[-2] > close.iloc[-3]:
        reasons.append('Recent bullish reversal warning')
    side = 'yes' if direction == 'ABOVE' else 'no'
    bid = _finite(quote_fn(market, side, 'bid'))
    ask = _finite(quote_fn(market, side, 'ask'))
    if bid is None or ask is None or not 0 < bid <= ask < 1:
        reasons.append('Invalid executable-side Kalshi quote')
    else:
        if ask > .79:
            reasons.append('Contract too expensive (above 79¢)')
        if ask - bid > .03:
            reasons.append('Bid/ask spread too wide (above 3¢)')
        # This is a quote-price ceiling, NOT a proven expected-value test.
    return {
        'decision': 'SKIP / NO ENTRY' if reasons else 'PAPER CANDIDATE · REVIEW ONLY',
        'side': direction, 'reasons': reasons,
        'entry_ask': ask, 'distance': distance, 'rsi': rsi,
    }


def render_v3_guard(market, strike, spot, seconds_left, verified, quote_fn, candle_fn, quote_stale=False):
    st.markdown('#### 👑 V3 Selective Gate · Experimental / Paper Only')
    try:
        candles = candle_fn()
        result = assess(market, strike, spot, candles, seconds_left, verified, quote_fn, quote_stale)
    except Exception as exc:
        st.warning('SKIP / NO ENTRY · V3 data/assessment unavailable')
        st.caption(f'Diagnostic: {exc}')
        return
    if result['decision'].startswith('SKIP'):
        st.warning('🛑 SKIP / NO ENTRY')
        st.caption(' · '.join(result['reasons'][:5]))
    else:
        st.info(f"🔎 {result['decision']} · {result['side']} · quoted ask {result['entry_ask']*100:.1f}¢")
        st.caption('Technical conditions align, but the strategy is NOT backtested or probability-calibrated. No buy instruction, no order, no guaranteed edge.')
    st.caption('Uses Coinbase spot and 1-minute candles as indicative signals; Kalshi settlement may differ. Existing Early/Locked/V2 trackers are unchanged and can still log their own paper calls.')
