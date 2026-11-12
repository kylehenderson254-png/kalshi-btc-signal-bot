
"""QUEEN'S EDGE | 254 — dashboard launcher.

PAPER ONLY — NO REAL ORDERS.

Loads the existing queens_preview_base.py dashboard without
changing the original dashboard, chart, or paper history.
"""

from pathlib import Path

BASE = Path(__file__).resolve().parent / "queens_preview_base.py"

if not BASE.is_file():
    raise FileNotFoundError(
        "Missing queens_preview_base.py beside app.py"
    )

source = BASE.read_text(encoding="utf-8")

# The dashboard dynamically executes queens_original.py.
# These three existing functions must be passed into that
# execution environment so the compact layout can call them.

anchor = (
    "        'qe_simple_header': qe_simple_header,\n"
)

replacement = (
    "        'qe_simple_header': qe_simple_header,\n"
    "        'qe_stage_row': qe_stage_row,\n"
    "        'qe_performance_center': qe_performance_center,\n"
    "        'visible_scanner_status': visible_scanner_status,\n"
)

if source.count(anchor) != 1:
    raise RuntimeError(
        "Dashboard launcher version mismatch. "
        "No changes were made to your files."
    )

source = source.replace(anchor, replacement, 1)

# Execute the patched dashboard in memory.
# The original file on GitHub remains untouched.
environment = {
    "__name__": "__main__",
    "__file__": str(BASE),
    "__package__": None,
}

exec(
    compile(source, str(BASE), "exec"),
    environment,
)
