
"""
QUEEN'S EDGE | 254
LIVE CHART STABILITY UPDATE

PAPER ONLY — NO REAL ORDERS.

Purpose:
- Keep the Coinbase streaming chart mounted outside
  Streamlit's five-second market fragment.
- Preserve the existing dashboard and V4 paper engine.
- Preserve existing paper records in Supabase.
- Retain the existing chart's indicators and WATCH logic.
- Avoid changing entry rules or sending real orders.

This update does not implement a new automatic
STRONG LOCK paper-entry strategy.
"""

import ast
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "queens_preview_base.py"

REQUIRED_FILES = [
    "queens_preview_base.py",
    "queens_original.py",
    "queens_live_chart.py",
    "queens_v3_guard.py",
]

missing = [
    name
    for name in REQUIRED_FILES
    if not (ROOT / name).is_file()
]

if missing:
    st.error(
        "Queen's Edge is missing required files: "
        + ", ".join(missing)
    )
    st.stop()


# ------------------------------------------------------------
# LOAD EXISTING QUEEN'S EDGE ENGINE
# ------------------------------------------------------------

spec = importlib.util.spec_from_file_location(
    "queens_preview_base",
    BASE_PATH,
)

if spec is None or spec.loader is None:
    st.error("Unable to load Queen's Edge base engine.")
    st.stop()

base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

VERSION = "QUEEN'S EDGE | 254 — LIVE CHART"


# ------------------------------------------------------------
# PRESERVE COMPLETE PAPER JOURNAL PAGINATION
# ------------------------------------------------------------

def complete_paper_rows(self):
    names = []
    offset = 0
    page_size = 200

    while True:
        objects = self.request(
            "POST",
            "/object/list/" + base.BUCKET,
            {
                "prefix": "entries",
                "limit": page_size,
                "offset": offset,
                "sortBy": {
                    "column": "name",
                    "order": "desc",
                },
            },
        )

        if not isinstance(objects, list):
            raise RuntimeError(
                "Paper journal listing unavailable"
            )

        names.extend(
            item["name"]
            for item in objects
            if isinstance(item, dict)
            and str(
                item.get("name", "")
            ).endswith(".json")
        )

        if len(objects) < page_size:
            break

        offset += page_size

        if offset >= 10000:
            raise RuntimeError(
                "Paper journal pagination limit reached"
            )

    def load(key):
        entry = self.get("entries", key)

        if not isinstance(entry, dict):
            raise RuntimeError(
                "Could not load saved paper entry: " + key
            )

        final = self.get("finals", key)

        return {
            "key": key,
            "entry": entry,
            "final": final,
            "exit": (
                None
                if final
                else self.get("exits", key)
            ),
        }

    with ThreadPoolExecutor(
        max_workers=6
    ) as pool:
        rows = list(pool.map(load, names))

    st.session_state["qe_paper_rows"] = rows
    return rows


base.PaperJournal.rows = complete_paper_rows


# ------------------------------------------------------------
# CHART RELOCATION
# ------------------------------------------------------------

original_compact_original = base.compact_original


def relocate_live_chart(source):
    """
    Run the original dashboard's compact-layout
    transformation first.

    Then move the live chart out of live_grid(),
    which is a five-second Streamlit fragment.

    The original chart renderer is reused.
    """

    tree = original_compact_original(source)

    live_grid = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "live_grid"
        ),
        None,
    )

    if live_grid is None:
        raise RuntimeError(
            "Compatibility check failed: live_grid"
        )

    chart_found = False
    new_grid_body = []

    for node in live_grid.body:
        is_chart_block = (
            isinstance(node, ast.With)
            and any(
                isinstance(
                    item.context_expr,
                    ast.Name,
                )
                and item.context_expr.id
                == "qe_chart_slot"
                for item in node.items
            )
        )

        if is_chart_block:
            chart_found = True
            continue

        new_grid_body.append(node)

    if not chart_found:
        raise RuntimeError(
            "Compatibility check failed: "
            "live chart block not found"
        )

    live_grid.body = new_grid_body

    # The chart placeholder created inside the fragment
    # is no longer needed.
    live_grid.body = [
        node
        for node in live_grid.body
        if not (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "qe_chart_slot"
                for target in node.targets
            )
        )
    ]

    # The streaming chart is rendered once per full-page
    # execution, outside the five-second fragment.
    #
    # The existing Coinbase WebSocket updates the chart
    # without requiring a Streamlit rerun.
    #
    # Use fresh contract metadata when initializing it.
    chart_source = '''
st.markdown(
    "#### 📈 LIVE BITCOIN · STREAMING CHART"
)

try:
    qe_chart_markets, qe_chart_errors = active_markets()

    qe_chart_series = SERIES[0][1]
    qe_chart_market = qe_chart_markets.get(
        qe_chart_series
    )

    qe_chart_target = None
    qe_chart_close = None
    qe_chart_ticker = None

    if qe_chart_market:
        qe_chart_ticker = qe_chart_market.get(
            "ticker"
        )

        qe_chart_target = next(
            (
                number(qe_chart_market.get(key))
                for key in (
                    "floor_strike",
                    "functional_strike",
                    "custom_strike",
                    "cap_strike",
                )
                if number(
                    qe_chart_market.get(key)
                ) is not None
            ),
            None,
        )

        qe_chart_close = parse_time(
            qe_chart_market.get("close_time")
            or qe_chart_market.get(
                "latest_expiration_time"
            )
        )

    render_live_chart(
        qe_chart_target,
        close_time=qe_chart_close,
        market_ticker=qe_chart_ticker,
    )

except Exception as qe_chart_exception:
    st.warning(
        "Live chart unavailable. "
        "Paper entries remain separate."
    )

    with st.expander(
        "Chart diagnostics",
        expanded=False,
    ):
        st.code(str(qe_chart_exception))
'''

    chart_nodes = ast.parse(
        chart_source
    ).body

    inserted = False
    new_module_body = []

    for node in tree.body:
        new_module_body.append(node)

        is_live_grid_call = (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(
                node.value.func,
                ast.Name,
            )
            and node.value.func.id == "live_grid"
        )

        if is_live_grid_call and not inserted:
            new_module_body.extend(chart_nodes)
            inserted = True

    if not inserted:
        raise RuntimeError(
            "Compatibility check failed: "
            "live_grid invocation not found"
        )

    tree.body = new_module_body

    # Reduce full-page remounts.
    # The existing five-second market fragment
    # remains active independently.
    #
    # Coinbase WebSocket updates remain event-driven,
    # rather than waiting for this interval.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if not (
            isinstance(node.func, ast.Name)
            and node.func.id == "st_autorefresh"
        ):
            continue

        for keyword in node.keywords:
            if keyword.arg == "interval":
                keyword.value = ast.Constant(
                    value=300000
                )

    return ast.fix_missing_locations(tree)


base.compact_original = relocate_live_chart


# ------------------------------------------------------------
# START QUEEN'S EDGE
# ------------------------------------------------------------

if __name__ == "__main__":
    try:
        base.launch()

    except (RuntimeError, SyntaxError) as exc:
        st.error(
            "Queen's Edge compatibility check failed."
        )

        with st.expander(
            "Error details",
            expanded=True,
        ):
            st.code(str(exc))

        st.info(
            "Restore the previous app.py from "
            "GitHub history if necessary."
        )

        st.stop()
