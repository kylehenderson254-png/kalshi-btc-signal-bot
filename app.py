
"""
QUEEN'S EDGE | 254
LIVE BITCOIN CHART RECOVERY

PAPER ONLY — NO REAL ORDERS

This entry point restores the original Coinbase
streaming chart renderer without modifying its
JavaScript or changing the existing paper engine.

Required companion files:
- queens_preview_base.py
- queens_original.py
- queens_live_chart.py
- queens_v3_guard.py

This is a chart recovery release.
It does not add automatic STRONG LOCK paper entries.
"""

import ast
import importlib.util
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = (
    "queens_preview_base.py",
    "queens_original.py",
    "queens_live_chart.py",
    "queens_v3_guard.py",
)

missing = [
    name
    for name in REQUIRED_FILES
    if not (ROOT / name).is_file()
]

if missing:
    st.error(
        "Missing Queen's Edge files: "
        + ", ".join(missing)
    )
    st.stop()


# -------------------------------------------------
# LOAD EXISTING QUEEN'S EDGE BASE
# -------------------------------------------------

BASE_FILE = ROOT / "queens_preview_base.py"

spec = importlib.util.spec_from_file_location(
    "queens_preview_base",
    BASE_FILE,
)

if spec is None or spec.loader is None:
    st.error(
        "Queen's Edge base module could not be loaded."
    )
    st.stop()

base = importlib.util.module_from_spec(spec)

try:
    spec.loader.exec_module(base)
except Exception as exc:
    st.error("Queen's Edge initialization failed.")
    st.exception(exc)
    st.stop()


# -------------------------------------------------
# RESTORE ORIGINAL STREAMING CHART RENDERER
# -------------------------------------------------

# The existing chart already supports Coinbase
# WebSocket updates, candles, indicators and WATCH
# signals.
#
# Use that renderer directly. Do not apply another
# HTML or JavaScript startup transformation.

original_chart_renderer = base.render_live_chart


def use_original_chart_renderer(path):
    """
    Keep the existing streaming chart implementation.

    The path is supplied by the base compatibility
    loader, but the already-imported renderer is used
    directly.
    """
    if not Path(path).is_file():
        raise RuntimeError(
            "Streaming chart module is missing."
        )

    return original_chart_renderer


base.clean_chart_renderer = use_original_chart_renderer


# -------------------------------------------------
# REPAIR CHART CONTAINER OWNERSHIP
# -------------------------------------------------

# Capture the original layout transformer BEFORE
# overriding it. This avoids calling the replacement
# recursively.

original_layout_transformer = base.compact_original


class LocalChartContainer(ast.NodeTransformer):
    """
    Replace the chart's external placeholder with
    a local container at its actual render location.

    This does not change the paper scanner, signals,
    market calculations or historical records.
    """

    def __init__(self):
        self.replacements = 0

    def visit_With(self, node):
        self.generic_visit(node)

        for item in node.items:
            expression = item.context_expr

            if (
                isinstance(expression, ast.Name)
                and expression.id == "qe_chart_slot"
            ):
                item.context_expr = ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(
                            id="st",
                            ctx=ast.Load(),
                        ),
                        attr="container",
                        ctx=ast.Load(),
                    ),
                    args=[],
                    keywords=[],
                )

                self.replacements += 1

        return node


def recover_chart_layout(source):
    """
    Apply the existing compact dashboard layout,
    then repair only the chart container.

    Never call base.compact_original from here,
    because that name now refers to this function.
    """

    tree = original_layout_transformer(source)

    repair = LocalChartContainer()
    tree = repair.visit(tree)

    if repair.replacements != 1:
        raise RuntimeError(
            "Chart compatibility check failed: "
            "expected one chart container, found "
            + str(repair.replacements)
        )

    return ast.fix_missing_locations(tree)


base.compact_original = recover_chart_layout


# -------------------------------------------------
# START EXISTING QUEEN'S EDGE DASHBOARD
# -------------------------------------------------

def main():
    try:
        base.launch()

    except Exception as exc:
        st.error(
            "Queen's Edge encountered a startup error."
        )

        st.exception(exc)

        st.caption(
            "No real orders are placed by this "
            "application. Existing paper records "
            "are not intentionally modified."
        )


if __name__ == "__main__":
    main()
