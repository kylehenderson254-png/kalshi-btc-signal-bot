
"""
QUEEN'S EDGE | 254
LIVE BITCOIN COMMAND CENTER — PREVIEW

Preserves the original dashboard, strategy engines,
Supabase history, paper tracking, and safety controls.

Runs on queens-edge-preview.
NO REAL TRADING.
"""

from pathlib import Path

import streamlit as st
from queens_live_chart import render_live_chart


# ============================================================
# ORIGINAL DASHBOARD
# ============================================================

BASE = Path(__file__).resolve().parent
ORIGINAL = BASE / "queens_original.py"

if not ORIGINAL.is_file():
    st.error(
        "Missing queens_original.py. "
        "Keep the original dashboard file in the "
        "same GitHub folder as app.py."
    )
    st.stop()

source = ORIGINAL.read_text(encoding="utf-8")


# ============================================================
# UPGRADE 1 — LIVE BITCOIN STREAMING CHART
# ============================================================

old_chart = (
    '    st.markdown("#### 📈 Live Bitcoin price action")\n'
    '    frame = btc_candles()\n'
    '    qe_btc_chart(frame, strike)'
)

new_chart = (
    '    st.markdown('
    '"#### 📈 LIVE BITCOIN · STREAMING CHART")\n'
    '    render_live_chart(strike)\n\n'
    '    with st.expander('
    '"📉 Original historical chart · Backup", '
    'expanded=False):\n'
    '        frame = btc_candles()\n'
    '        qe_btc_chart(frame, strike)'
)

if old_chart not in source:
    st.error(
        "Live chart upgrade could not locate the "
        "original chart section. No original files "
        "were changed."
    )
    st.stop()

source = source.replace(old_chart, new_chart, 1)


# ============================================================
# UPGRADE 2 — PAPER CALCULATOR DEFAULTS TO NONE
# ============================================================

old_side = (
    'st.radio("Side", ["YES", "NO"], '
    'horizontal=True, key=f"qe_calc_side_{series}")'
)

new_side = (
    'st.radio('
    '"Side · manual paper calculator", '
    '["NONE", "YES", "NO"], '
    'horizontal=True, '
    'key=f"qe_calc_side_{series}")'
)

old_ask = 'ask = yes_ask if side == "YES" else no_ask'

new_ask = (
    'ask = ('
    'yes_ask if side == "YES" '
    'else no_ask if side == "NO" '
    'else None)'
)

if old_side not in source or old_ask not in source:
    st.error(
        "Calculator upgrade could not locate the "
        "original calculator section. No original "
        "files were changed."
    )
    st.stop()

source = source.replace(old_side, new_side, 1)
source = source.replace(old_ask, new_ask, 1)


# ============================================================
# UPGRADE 3 — FIX LIVE CHART FUNCTION CONNECTION
# ============================================================

# The original app is executed with its own namespace.
# The chart function MUST be included in that namespace.
# Otherwise the live chart crashes with NameError.

app_globals = {
    "__name__": "__main__",
    "__file__": str(ORIGINAL),
    "__package__": None,
    "render_live_chart": render_live_chart,
}


# ============================================================
# START COMPLETE QUEEN'S EDGE DASHBOARD
# ============================================================

# Everything else remains in the original application:
#
# - Queen's Edge branding
# - Kalshi 15-minute Bitcoin contracts
# - Early Call and Locked Call
# - Strong LOCK and Dual Strong LOCK
# - V2 Shadow tracking
# - Supabase history and grading
# - Paper performance
# - Pressure radar
# - Existing safety console
#
# The chart streams separately in the browser.
# No real trades are executed.

exec(
    compile(
        source,
        str(ORIGINAL),
        "exec",
    ),
    app_globals,
)
