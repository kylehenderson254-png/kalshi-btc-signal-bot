
"""QUEEN'S EDGE | 254 — Unified Scoreboard

PAPER ONLY — NO REAL ORDERS.

Keeps the existing dashboard, live chart, signals,
settlement rules, scanner, and paper journals.

Changes only how performance results are displayed.
"""

from pathlib import Path

BASE = (
    Path(__file__).resolve().parent
    / "queens_preview_base.py"
)

if not BASE.is_file():
    raise FileNotFoundError(
        "Missing queens_preview_base.py beside app.py"
    )

source = BASE.read_text(encoding="utf-8")


# ==================================================
# 1. PRESERVE STABLE LIVE CHART
# ==================================================

disabled_refresh = (
    "source = source.replace(full_page_timer, "
    "'# Whole-page refresh disabled in preview; "
    "V4 fragment runs independently.', 1)"
)

if source.count(disabled_refresh) != 1:
    raise RuntimeError(
        "Dashboard version mismatch: chart refresh."
    )


# ==================================================
# 2. PRESERVE DASHBOARD FUNCTION REFERENCES
# ==================================================

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


# ==================================================
# 3. KEEP ROYAL SCALP SEASON 2
# ==================================================

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


def royal_season_data(store):
    """Read Season 2 without changing old records."""

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

        completed.append({
            "Strategy": "Royal Scalp S2",
            "Contract": entry.get("ticker"),
            "Side": entry.get("side"),
            "Entry UTC": entry.get("at"),
            "Entry ask": entry.get("ask"),
            "Exit UTC": event.get("at"),
            "Exit bid": event.get("bid"),
            "Net P&L": number(event.get("pnl")),
            "Reason": event.get("reason"),
        })

    return entries, completed, unresolved


def royal_metrics(store):
    """Retained for compatibility with existing calls."""

    entries, completed, unresolved = (
        royal_season_data(store)
    )

    values = [
        row["Net P&L"]
        for row in completed
        if row["Net P&L"] is not None
    ]

    st.markdown("##### 👑 ROYAL SCALP · SEASON 2")

    a, b, c, d = st.columns(4)

    a.metric("Season 2 net", f"${sum(values):+.2f}")
    b.metric("Wins", sum(v > 0 for v in values))
    c.metric("Losses", sum(v < 0 for v in values))
    d.metric(
        "Open / completed",
        f"{len(unresolved)} / {len(completed)}"
    )

    st.caption(
        "Started Nov 11, 2026 at 10 PM Central. "
        "Earlier Royal Scalp records remain untouched."
    )

    return entries, completed

'''

source = source.replace(
    next_function,
    season_code + next_function,
    1,
)


# ==================================================
# 4. SINGLE PERFORMANCE SCOREBOARD
# ==================================================

performance_start = "def qe_performance_center():"
performance_end = "def qe_simple_header("

if (
    source.count(performance_start) != 1
    or source.count(performance_end) != 1
):
    raise RuntimeError(
        "Dashboard version mismatch: performance center."
    )

source = source.replace(
    performance_start,
    "def qe_performance_center_original():",
    1,
)

scoreboard_code = '''
def qe_score_money(value):
    if value is None:
        return "—"
    return f"${value:+.2f}"


def qe_score_row(name, values, pending, kind):
    """One row, with no mixing of strategy results."""

    wins = sum(v > 0 for v in values)
    losses = sum(v < 0 for v in values)
    breakeven = sum(v == 0 for v in values)
    graded = len(values)

    return {
        "Strategy": name,
        "Type": kind,
        "Wins": wins,
        "Losses": losses,
        "Flat": breakeven,
        "Win rate": (
            f"{wins / graded:.1%}"
            if graded else "—"
        ),
        "Completed": graded,
        "Pending": pending,
        "Net P&L": qe_score_money(
            sum(values) if graded else None
        ),
    }


def qe_score_objects(store, folder):
    """Read existing strategy objects, never write."""

    items = store.request(
        "POST",
        "/object/list/" + BUCKET,
        {
            "prefix": folder,
            "limit": 200,
            "offset": 0,
            "sortBy": {
                "column": "name",
                "order": "desc",
            },
        },
    )

    if not isinstance(items, list):
        raise RuntimeError(
            "Cannot list " + folder
        )

    output = []

    for item in items:
        key = str(item.get("name") or "")

        if not key.endswith(".json"):
            continue

        event = store.get(folder, key)

        if isinstance(event, dict):
            output.append((key, event))

    return output


def qe_score_observation_study(
    store, name, entries_folder,
    finals_folder
):
    entries = qe_score_objects(
        store, entries_folder
    )

    values = []
    details = []
    pending = 0

    for key, entry in entries:
        final = store.get(finals_folder, key)

        if not isinstance(final, dict):
            pending += 1
            continue

        pnl = number(
            final.get("hypothetical_pnl")
        )

        if pnl is None:
            pending += 1
            continue

        values.append(pnl)

        details.append({
            "Strategy": name,
            "Contract": entry.get("ticker"),
            "Side": entry.get("side"),
            "Entry UTC": entry.get("observed_at"),
            "Net P&L": pnl,
            "Status": "Officially graded",
        })

    return (
        qe_score_row(
            name, values, pending,
            "Hypothetical"
        ),
        details,
    )


def qe_performance_center():
    """One read-only scoreboard for all strategies."""

    st.markdown(
        "### 👑 QUEEN'S PERFORMANCE SCOREBOARD"
    )

    st.caption(
        "All strategies in one place. "
        "Paper trades and hypothetical observations "
        "are reported separately, never added together."
    )

    try:
        url = st.secrets.get("SUPABASE_URL")
        key = st.secrets.get(
            "SUPABASE_SERVICE_ROLE_KEY"
        )

        if not url or not key:
            st.warning(
                "Private paper journal unavailable. "
                "Existing results are not changed."
            )
            return

        store = journal(str(url), str(key))

    except Exception as exc:
        st.warning(
            "Could not connect to performance journal."
        )
        return

    scoreboard = []
    history = []
    unavailable = []

    # ----------------------------------------------
    # ROYAL SCALP — SEASON 2 ONLY
    # ----------------------------------------------

    try:
        entries, completed, unresolved = (
            royal_season_data(store)
        )

        values = [
            row["Net P&L"]
            for row in completed
            if row["Net P&L"] is not None
        ]

        scoreboard.append(
            qe_score_row(
                "Royal Scalp S2",
                values,
                len(unresolved),
                "Paper scalp",
            )
        )

        history.extend(completed)

    except Exception:
        unavailable.append("Royal Scalp S2")

    # ----------------------------------------------
    # ROYAL LOCK / V4
    # ----------------------------------------------

    try:
        rows = store.rows()
        finals = [
            row for row in rows
            if row.get("final")
        ]

        values = [
            number(
                row["final"].get("managed_pnl")
            )
            for row in finals
        ]

        values = [
            v for v in values
            if v is not None
        ]

        scoreboard.append(
            qe_score_row(
                "Royal Lock / V4",
                values,
                len(rows) - len(finals),
                "Paper settlement",
            )
        )

        for row in rows:
            entry = row["entry"]
            final = row.get("final")
            exit_event = row.get("exit")

            event = final or exit_event

            history.append({
                "Strategy": "Royal Lock / V4",
                "Contract": entry.get("ticker"),
                "Side": entry.get("side"),
                "Entry UTC": entry.get(
                    "observed_at"
                ),
                "Net P&L": (
                    number(
                        final.get("managed_pnl")
                    )
                    if final
                    else number(
                        exit_event.get("pnl")
                    )
                    if exit_event
                    else None
                ),
                "Status": (
                    "Graded"
                    if final
                    else "Exit observed"
                    if exit_event
                    else "Pending"
                ),
            })

    except Exception:
        unavailable.append("Royal Lock / V4")

    # ----------------------------------------------
    # HIGH-PRICE EARLY EXIT TEST
    # ----------------------------------------------

    try:
        rows = high_price_rows(store)

        values = []
        pending = 0

        for row in rows:
            entry = row["entry"]
            exit_event = row.get("exit")
            final = row.get("final")

            pnl = (
                number(exit_event.get("pnl"))
                if exit_event
                else number(
                    final.get("modeled_net")
                )
                if final
                else None
            )

            if pnl is None:
                pending += 1
            else:
                values.append(pnl)

            history.append({
                "Strategy": "High-Price Test",
                "Contract": entry.get("ticker"),
                "Side": entry.get("side"),
                "Entry UTC": entry.get(
                    "observed_at"
                ),
                "Net P&L": pnl,
                "Status": (
                    "Modeled exit"
                    if exit_event
                    else "Settled model"
                    if final
                    else "Pending"
                ),
            })

        scoreboard.append(
            qe_score_row(
                "High-Price Test",
                values,
                pending,
                "Modeled exits",
            )
        )

    except Exception:
        unavailable.append("High-Price Test")

    # ----------------------------------------------
    # NEAR MISS — REJECTED OBSERVATIONS
    # ----------------------------------------------

    try:
        summary, details = (
            qe_score_observation_study(
                store,
                "Near Miss",
                "near_misses",
                "near_miss_finals",
            )
        )

        scoreboard.append(summary)
        history.extend(details)

    except Exception:
        unavailable.append("Near Miss")

    # ----------------------------------------------
    # SHADOW V1 — REJECTED OBSERVATIONS
    # ----------------------------------------------

    try:
        summary, details = (
            qe_score_observation_study(
                store,
                "Shadow V1",
                "shadow_v1_rejections",
                "shadow_v1_finals",
            )
        )

        scoreboard.append(summary)
        history.extend(details)

    except Exception:
        unavailable.append("Shadow V1")

    # ----------------------------------------------
    # MAIN SCOREBOARD
    # ----------------------------------------------

    if scoreboard:
        st.dataframe(
            scoreboard,
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info(
            "No performance results available yet."
        )

    st.caption(
        "Net P&L includes only completed or graded "
        "records for each strategy. "
        "A high win rate does not guarantee profit. "
        "No actual orders or guaranteed fills."
    )

    # ----------------------------------------------
    # COMPACT STATUS
    # ----------------------------------------------

    status = st.session_state.get(
        "royal_scalp_status",
        "Waiting for live observation.",
    )

    st.caption(
        "⚡ Royal Scalp: " + str(status)
    )

    # ----------------------------------------------
    # SINGLE TRADE HISTORY
    # ----------------------------------------------

    st.markdown(
        "##### 📋 RECENT PAPER & STUDY HISTORY"
    )

    if history:
        history.sort(
            key=lambda row: str(
                row.get("Entry UTC") or ""
            ),
            reverse=True,
        )

        st.dataframe(
            history[:30],
            hide_index=True,
            use_container_width=True,
        )

        st.caption(
            "Latest 30 records shown. "
            "Original journals retain their saved history."
        )
    else:
        st.caption(
            "No completed history to display yet."
        )

    if unavailable:
        st.warning(
            "Some results could not be read: "
            + ", ".join(unavailable)
            + ". Existing saved data was not changed."
        )

'''

source = source.replace(
    performance_end,
    scoreboard_code + performance_end,
    1,
)


# ==================================================
# 5. KEEP BACKGROUND SCANNERS, REDUCE DUPLICATES
# ==================================================
#
# These report functions perform some grading work.
# They must still execute.
#
# Their old visual output is cleared after execution,
# because the new scoreboard shows the results.
#
# The paper engine and chart are not modified.

visual_reports = [
    "paper_metrics",
    "paper_history",
    "high_price_report",
    "near_miss_report",
    "shadow_v1_report",
]

for name in visual_reports:
    marker = "def " + name + "("

    if source.count(marker) != 1:
        raise RuntimeError(
            "Dashboard version mismatch: " + name
        )

    source = source.replace(
        marker,
        "def _qe_original_" + name + "(",
        1,
    )

silent_code = '''
def _qe_silent_report(fn, *args, **kwargs):
    """Run original report and hide its duplicate UI."""

    placeholder = st.empty()

    try:
        with placeholder.container():
            return fn(*args, **kwargs)
    finally:
        placeholder.empty()


def paper_metrics(*args, **kwargs):
    return _qe_silent_report(
        _qe_original_paper_metrics,
        *args, **kwargs
    )


def paper_history(*args, **kwargs):
    return _qe_silent_report(
        _qe_original_paper_history,
        *args, **kwargs
    )


def high_price_report(*args, **kwargs):
    return _qe_silent_report(
        _qe_original_high_price_report,
        *args, **kwargs
    )


def near_miss_report(*args, **kwargs):
    return _qe_silent_report(
        _qe_original_near_miss_report,
        *args, **kwargs
    )


def shadow_v1_report(*args, **kwargs):
    return _qe_silent_report(
        _qe_original_shadow_v1_report,
        *args, **kwargs
    )


'''

source = source.replace(
    performance_end,
    silent_code + performance_end,
    1,
)


# ==================================================
# 6. CLEAN UP EXPANDER LABELS
# ==================================================

source = source.replace(
    "⚙️ Advanced Tools · paper P&L, diagnostics & history",
    "⚙️ BACKGROUND PAPER ENGINE · Settings & diagnostics",
    1,
)

source = source.replace(
    "📉 Original historical chart · Backup",
    "📉 Backup chart · optional",
    1,
)


# ==================================================
# 7. START EXISTING DASHBOARD
# ==================================================

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
