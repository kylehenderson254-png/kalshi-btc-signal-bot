
"""QUEEN'S EDGE | 254 — V3 preview launcher. NO REAL ORDERS.
Keep queens_original.py and queens_live_chart.py beside this file.
Deploy ONLY to queens-edge-preview, never production main.
"""
from pathlib import Path
import streamlit as st
from queens_live_chart import render_live_chart
from queens_v3_guard import render_v3_guard

BASE = Path(__file__).resolve().parent
ORIGINAL = BASE / 'queens_original.py'
if not ORIGINAL.is_file():
    st.error('Missing queens_original.py in the same folder as app.py')
    st.stop()
source = ORIGINAL.read_text(encoding='utf-8')


def replace_once(before, after, label):
    global source
    if source.count(before) != 1:
        st.error(f'V3 compatibility check failed: {label}. Original dashboard was not modified.')
        st.stop()
    source = source.replace(before, after, 1)


replace_once(
    'c.metric("Indicative distance", f"${abs(delta):,.2f}", "ABOVE" if delta > 0 else "BELOW" if delta < 0 else "AT TARGET")',
    'c.metric("Indicative distance", f"${abs(delta):,.2f}", "ABOVE" if delta > 0 else "BELOW" if delta < 0 else "AT TARGET", delta_color="off")',
    'distance-arrow correction',
)
replace_once(
    'side = st.radio("Side", ["YES", "NO"], horizontal=True, key=f"qe_calc_side_{series}")',
    'side = st.radio("Side · manual paper calculator", ["NONE", "YES", "NO"], horizontal=True, key=f"qe_calc_side_{series}")',
    'paper calculator default NONE',
)
replace_once(
    'ask = yes_ask if side == "YES" else no_ask',
    'ask = yes_ask if side == "YES" else no_ask if side == "NO" else None',
    'paper calculator no-side safety',
)
replace_once(
    '    st.markdown("#### 📈 Live Bitcoin price action")\n    frame = btc_candles()\n    qe_btc_chart(frame, strike)',
    '    render_v3_guard(market, strike, qe_indicative_btc(),\n'
    '        (parse_time(market.get("close_time") or market.get("latest_expiration_time")) - datetime.now(timezone.utc)).total_seconds()\n'
    '        if market and parse_time(market.get("close_time") or market.get("latest_expiration_time")) else None,\n'
    '        verified_direction(market) if market else False, quote, btc_candles, bool(errors.get(series)))\n'
    '    st.markdown("#### 📈 LIVE BITCOIN · STREAMING CHART")\n'
    '    render_live_chart(strike)\n'
    '    with st.expander("📉 Original historical chart · Backup", expanded=False):\n'
    '        frame = btc_candles()\n'
    '        qe_btc_chart(frame, strike)',
    'live chart and selective V3 panel',
)
# Preserve original code, trackers, and Supabase; execute in a namespace
# with the preview-only chart and assessment functions available.
app_globals = {
    '__name__': '__main__',
    '__file__': str(ORIGINAL),
    '__package__': None,
    'render_live_chart': render_live_chart,
    'render_v3_guard': render_v3_guard,
}
exec(compile(source, str(ORIGINAL), 'exec'), app_globals)
