
"""
QUEEN'S EDGE | 254
LIVE CHART RECOVERY — CLEAN ENTRY POINT

PAPER ONLY — NO REAL ORDERS

This file launches the existing Queen's Edge
dashboard without monkey-patching the chart,
Streamlit HTML components, or layout functions.

The existing base module remains responsible for:
- Purple/black dashboard
- Coinbase BTC streaming chart
- EMA, RSI and WATCH indicators
- Kalshi market information
- EARLY, MID and LOCK displays
- V4 paper scanner and history
- Paper P&L and settlement tracking

No real-money orders are submitted.
"""

from pathlib import Path
import traceback

import streamlit as st


ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = (
    "queens_preview_base.py",
    "queens_original.py",
    "queens_live_chart.py",
    "queens_v3_guard.py",
)

missing = [
    filename
    for filename in REQUIRED_FILES
    if not (ROOT / filename).is_file()
]

if missing:
    st.error(
        "Missing Queen's Edge files: "
        + ", ".join(missing)
    )
    st.stop()


def main():
    try:
        # Import the original dashboard engine.
        # Do not override components.html.
        # Do not override compact_original.
        # Do not override clean_chart_renderer.
        from queens_preview_base import launch

        launch()

    except Exception as exc:
        st.error(
            "Queen's Edge encountered an error."
        )

        st.write(
            "Error type:",
            type(exc).__name__,
        )

        st.write(
            "Error message:",
            str(exc),
        )

        with st.expander(
            "Full Python diagnostics",
            expanded=False,
        ):
            st.code(
                traceback.format_exc(),
                language="text",
            )

        st.caption(
            "Paper-only dashboard. "
            "No real orders are sent."
        )


if __name__ == "__main__":
    main()
