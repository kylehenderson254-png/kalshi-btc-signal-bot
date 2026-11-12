
"""QUEEN'S EDGE | 254 — Stable Chart + Season 2

PAPER ONLY. NO REAL ORDERS.

Uses the existing dashboard, chart, and journal.
Restores the original chart-friendly refresh behavior.
"""

from pathlib import Path

BASE = Path(__file__).resolve().parent / "queens_preview_base.py"

if not BASE.is_file():
    raise FileNotFoundError(
        "Missing queens_preview_base.py beside app.py"
    )

source = BASE.read_text(encoding="utf-8")

# ----------------------------------------
# 1. KEEP THE LIVE CHART STABLE
# ----------------------------------------
# queens_preview_base.py already disables
# the full-page timer. Leave that behavior
# intact so the Coinbase chart is not
# recreated every 15 seconds.

disabled_refresh = (
    "source = source.replace(full_page_timer, "
    "'# Whole-page refresh disabled in preview; "
    "V4 fragment runs independently.', 1)"
)

if source.count(disabled_refresh) != 1:
    raise RuntimeError(
        "Dashboard version mismatch: refresh control."
    )

# ----------------------------------------
# 2. FIX DASHBOARD FUNCTION REFERENCES
# ----------------------------------------

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
        "Dashboard version mismatch: function namespace."
    )

source = source.replace(anchor, replacement, 1)

# ----------------------------------------
# 3. ROYAL SCALP SEASON 2
# ----------------------------------------

old_function = "def royal_metrics(store):"
next_function = "def royal_momentum(df):"

if (
    source.count(old_function) != 1
    or source.count(next_function) != 1
):
    raise RuntimeError(
        "Dashboard version mismatch: Royal Scalp."
    )

source = source.replace(
    old_function,
    "def royal_metrics_original(store):",
    1,
)

season_code = '''
from datetime import datetime as _season_dt
from datetime import timezone as _season_tz

SEASON_2_START = _season_dt(
    2026, 11, 12, 4, 0, 0,
    tzinfo=_season_tz.utc,
)


def season_time(value):
    if not value:
        return None

    try:
        dt = _season_dt.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_season_tz.utc)

        return dt.astimezone(_season_tz.utc)

    except (ValueError, TypeError):
        return None


def royal_metrics(store):
    """Season 2 paper results; old journal untouched."""

    st.markdown("##### 👑 ROYAL SCALP · SEASON 2")

    st.caption(
        "Started November 11, 2026 at "
        "10:00 PM Central. "
        "Earlier results remain in the original journal."
    )

    entries = [
        (key, entry)
        for key, entry in royal_objects(
            store, "royal_scalp_entries"
        )
        if isinstance(entry, dict)
        and (
            (stamp := season_time(entry.get("at")))
            is not None
        )
        and stamp >= SEASON_2_START
    ]

    exits = {
        key: event
        for key, event in royal_objects(
            store, "royal_scalp_exits"
        )
        if isinstance(event, dict)
    }

    completed = []
    unresolved = []

    for key, entry in entries:
        event = exits.get(key)

        if event is None:
            unresolved.append(entry)
            continue

        pnl = number(event.get("pnl"))

        completed.append({
            "Contract": entry.get("ticker"),
            "Side": entry.get("side"),
            "Entry UTC": entry.get("at"),
            "Entry ask": entry.get("ask"),
            "Exit UTC": event.get("at"),
            "Exit bid": event.get("bid"),
            "Net P&L": pnl,
            "Reason": event.get("reason"),
        })

    values = [
        row["Net P&L"]
        for row in completed
        if row["Net P&L"] is not None
    ]

    wins = sum(v > 0 for v in values)
    losses = sum(v < 0 for v in values)

    a, b, c, d = st.columns(4)

    a.metric(
        "Season 2 net",
        f"${sum(values):+.2f}"
    )

    b.metric("Wins", wins)
    c.metric("Losses", losses)

    d.metric(
        "Open / completed",
        f"{len(unresolved)} / {len(completed)}"
    )

    st.caption(
        f"{len(entries)} Season 2 paper entries · "
        f"{len(completed)} simulated exits · "
        f"{len(unresolved)} unresolved. "
        "Estimated fills and fees; no real orders."
    )

    with st.expander(
        "Season 2 · complete paper history",
        expanded=False
    ):
        if completed:
            st.dataframe(
                completed,
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption(
                "No completed Season 2 paper trades yet."
            )

    if unresolved:
        with st.expander(
            "Season 2 · unresolved entries",
            expanded=False
        ):
            st.dataframe(
                [
                    {
                        "Contract": e.get("ticker"),
                        "Side": e.get("side"),
                        "Entry UTC": e.get("at"),
                        "Entry ask": e.get("ask"),
                        "Status": "UNRESOLVED",
                    }
                    for e in unresolved
                ],
                hide_index=True,
                use_container_width=True,
            )

    return entries, exits


'''

source = source.replace(
    next_function,
    season_code + next_function,
    1,
)

# ----------------------------------------
# 4. COMPILE AND START EXISTING DASHBOARD
# ----------------------------------------

program = compile(
    source,
    str(BASE),
    "exec",
)

environment = {
    "__name__": "__main__",
    "__file__": str(BASE),
    "__package__": None,
}

exec(program, environment)
