
"""
QUEEN'S EDGE | 254
LIVE BITCOIN COMMAND CENTER — PREVIEW UPGRADE

Preserves the existing dashboard, strategies, Supabase
tracking, historical results and paper-only safeguards.

Requires:
    queens_original.py
    queens_live_chart.py

NO REAL TRADING.
"""

from pathlib import Path

import streamlit as st

from queens_live_chart import render_live_chart


# ============================================================
# LOAD THE ORIGINAL QUEEN'S EDGE DASHBOARD
# ============================================================

BASE = Path(__file__).resolve().parent
ORIGINAL = BASE / "queens_original.py"

if not ORIGINAL.is_file():
    st.error(
        "QUEEN'S EDGE SETUP: queens_original.py is missing. "
        "Rename your previous app.py to queens_original.py "
        "without deleting its contents."
    )
    st.stop()

source = ORIGINAL.read_text(encoding="utf-8")


# ============================================================
# UPGRADE 1 — LIVE STREAMING BITCOIN CHART
# ============================================================

old_chart = """    st.markdown("#### 📈 Live Bitcoin price action")
    frame = btc_candles()
    qe_btc_chart(frame, strike)"""

new_chart = """    st.markdown(
        "#### 📈 LIVE BITCOIN · STREAMING CHART"
    )
    render_live_chart(strike)

    with st.expander(
        "📉 Original historical chart · Backup",
        expanded=False,
    ):
        frame = btc_candles()
        qe_btc_chart(frame, strike)
"""

if old_chart not in source:
    st.error(
        "Preview upgrade stopped safely: the original "
        "Bitcoin chart section did not match the expected "
        "version. Your original code has not been changed."
    )
    st.stop()

source = source.replace(old_chart, new_chart, 1)


# ============================================================
# UPGRADE 2 — CALCULATOR STARTS WITH NO SIDE SELECTED
# ============================================================

old_side = (
    'st.radio("Side", ["YES", "NO"], '
    'horizontal=True, key=f"qe_calc_side_{series}")'
)

new_side = (
    'st.radio("Side · manual paper calculator", '
    '["NONE", "YES", "NO"], '
    'horizontal=True, key=f"qe_calc_side_{series}")'
)

old_ask = 'ask = yes_ask if side == "YES" else no_ask'

new_ask = (
    'ask = (yes_ask if side == "YES" '
    'else no_ask if side == "NO" else None)'
)

if old_side not in source or old_ask not in source:
    st.error(
        "Preview upgrade stopped safely: the calculator "
        "section did not match the expected version. "
        "Your original code has not been changed."
    )
    st.stop()

source = source.replace(old_side, new_side, 1)
source = source.replace(old_ask, new_ask, 1)


# ============================================================
# UPGRADE 3 — PRESERVE ALL ORIGINAL FEATURES
# ============================================================

# Run the complete original application with only the
# chart and calculator changes above.
#
# This preserves:
# - Original Queen's Edge branding and layout
# - Kalshi market quotes
# - Early and Locked strategies
# - Strong LOCK
# - Dual Strong LOCK
# - V2 Shadow strategy
# - Supabase tracking and grading
# - Paper performance and historical records
# - Existing pressure radar
# - Existing safety console
#
# No real-money orders are added.

app_globals = {
    "__name__": "__main__",
    "__file__": str(ORIGINAL),
    "__package__": None,
}

exec(
    compile(
        source,
        str(ORIGINAL),
        "exec",
    ),
    app_globals,
)
