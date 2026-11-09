
"""
QUEEN'S EDGE | 254
LIVE CHART RECOVERY

PAPER ONLY — NO REAL ORDERS.

This entry point uses the existing Queen's Edge
dashboard and chart modules.

Fixes:
- Removes the recursive AST chart override.
- Renders the chart in a local Streamlit container.
- Shows chart errors instead of silently hiding them.
- Keeps the original V4 paper engine and safety rules.

This is a chart recovery build.
It does not add new automatic STRONG LOCK entries.
"""

import importlib.util
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = [
    "queens_preview_base.py",
    "queens_original.py",
    "queens_live_chart.py",
    "queens_v3_guard.py",
]

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


# --------------------------------------------------
# LOAD EXISTING DASHBOARD
# --------------------------------------------------

BASE_PATH = ROOT / "queens_preview_base.py"

spec = importlib.util.spec_from_file_location(
    "queens_preview_base",
    BASE_PATH,
)

if spec is None or spec.loader is None:
    st.error(
        "Unable to initialize Queen's Edge."
    )
    st.stop()

base = importlib.util.module_from_spec(spec)

spec.loader.exec_module(base)


# --------------------------------------------------
# LIVE CHART RECOVERY
# --------------------------------------------------

# Save the original transformer before replacing it.
# Never call the replacement recursively.

original_compact_original = base.compact_original


def recover_chart(source):
    """
    Keep the existing dashboard layout transformation.

    Replace the chart's cross-fragment placeholder
    with a local Streamlit container.

    This avoids relying on a placeholder created
    in a different Streamlit execution context.
    """

    old_chart_block = (
        "    with qe_chart_slot:\n"
        '        st.markdown('
        '"#### 📈 LIVE BITCOIN · STREAMING CHART")\n'
        "        render_live_chart("
        "strike, close_time=qe_close, "
        'market_ticker=market.get("ticker") '
        "if market else None)\n"
    )

    new_chart_block = (
        "    with st.container():\n"
        '        st.markdown('
        '"#### 📈 LIVE BITCOIN · STREAMING CHART")\n'
        "        try:\n"
        "            render_live_chart(\n"
        "                strike,\n"
        "                close_time=qe_close,\n"
        "                market_ticker=(\n"
        '                    market.get("ticker")\n'
        "                    if market else None\n"
        "                ),\n"
        "            )\n"
        "        except Exception as chart_error:\n"
        "            st.error(\n"
        '                "Live Bitcoin chart failed to render."\n'
        "            )\n"
        "            with st.expander(\n"
        '                "Chart diagnostics",\n'
        "                expanded=False,\n"
        "            ):\n"
        "                st.code(str(chart_error))\n"
    )

    if source.count(old_chart_block) != 1:
        raise RuntimeError(
            "Chart compatibility check failed. "
            "Expected chart block was not found."
        )

    source = source.replace(
        old_chart_block,
        new_chart_block,
        1,
    )

    # Call the saved original function.
    # Do not call base.compact_original here.
    return original_compact_original(source)


base.compact_original = recover_chart


# --------------------------------------------------
# START DASHBOARD
# --------------------------------------------------

if __name__ == "__main__":
    try:
        base.launch()

    except Exception as exc:
        st.error(
            "Queen's Edge could not start."
        )

        with st.expander(
            "Startup diagnostics",
            expanded=True,
        ):
            st.code(
                type(exc).__name__
                + ": "
                + str(exc)
            )

        st.info(
            "Your saved paper journal has not "
            "been intentionally modified."
        )

        st.stop()
