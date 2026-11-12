
"""QUEEN'S EDGE | 254
Automatic Kalshi rollover + Royal Scalp Season 2

PAPER ONLY — NO REAL ORDERS.
Preserves existing journal and trading calculations.
"""

from pathlib import Path

BASE = Path(__file__).resolve().parent / "queens_preview_base.py"

if not BASE.is_file():
    raise FileNotFoundError("Missing queens_preview_base.py")

source = BASE.read_text(encoding="utf-8")

# -----------------------------------------
# 1. RESTORE AUTOMATIC CONTRACT REFRESH
# -----------------------------------------

# The preview disabled the original whole-page
# refresh. Restore it so each run retrieves the
# latest Kalshi contract and target.
#
# This is a reliability-first fix. The Streamlit
# page may briefly redraw on each refresh.

disabled_refresh = (
    "source = source.replace(full_page_timer, "
    "'# Whole-page refresh disabled in preview; "
    "V4 fragment runs independently.', 1)"
)

enabled_refresh = (
    "source = source.replace("
    "full_page_timer, "
    "'st_autorefresh(interval=15_000, "
    "limit=None, key=\"qe_contract_rollover\")', "
    "1)"
)

if source.count(disabled_refresh) != 1:
    raise RuntimeError(
        "Rollover compatibility check failed. "
        "Your dashboard files were not changed."
    )

source = source.replace(
    disabled_refresh,
    enabled_refresh,
    1,
)

# -----------------------------------------
# 2. PRESERVE WORKING DASHBOARD FUNCTIONS
# -----------------------------------------

anchor = "        'qe_simple_header': qe_simple_header,\n"

replacement = (
    "        'qe_simple_header': qe_simple_header,\n"
    "        'qe_stage_row': qe_stage_row,\n"
    "        'qe_performance_center': qe_performance_center,\n"
    "        'visible_scanner_status': visible_scanner_status,\n"
)

if source.count(anchor) != 1:
    raise RuntimeError(
        "Dashboard function compatibility check failed"
    )

source = source.replace(anchor, replacement, 1)

# -----------------------------------------
# 3. PRESERVE ROYAL SCALP SEASON 2
# -----------------------------------------

old_metrics = "def royal_metrics(store):"

if source.count(old_metrics) != 1:
    raise RuntimeError(
        "Original Royal Scalp metrics function not found"
    )

source = source.replace(
    old_metrics,
    "def royal_metrics_previous(store):",
    1,
)

season_code = r'''

from datetime import datetime as _qe_datetime
from datetime import timezone as _qe_timezone

QE_SEASON_2_START = _qe_datetime(
    2026, 11, 12, 4, 0, 0,
    tzinfo=_qe_timezone.utc,
)


def qe_season_timestamp(value):
    if not value:
        return None
    try:
        dt = _qe_datetime.fromisoformat(
            str(value).replace('Z', '+00:00')
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_qe_timezone.utc)
        return dt.astimezone(_qe_timezone.utc)
    except (ValueError, TypeError):
        return None


def qe_season_entries(store):
    result = []

    for key, entry in royal_objects(
        store, 'royal_scalp_entries'
    ):
        if not isinstance(entry, dict):
            continue

        stamp = qe_season_timestamp(
            entry.get('at')
        )

        if (
            stamp is not None
            and stamp >= QE_SEASON_2_START
        ):
            result.append((key, entry))

    return result


def qe_season_settlements(store, entries, exits):
    """Grade expired positions from official results.

    Never invent a simulated exit bid.
    """

    existing = dict(
        royal_objects(
            store,
            'royal_scalp_season2_finals'
        )
    )

    warnings = []
    checked = 0

    for key, entry in entries:
        if key in exits or key in existing:
            continue

        if checked >= 4:
            break

        ticker = str(
            entry.get('ticker') or ''
        )

        if not ticker.startswith('KXBTC15M-'):
            warnings.append(
                'Invalid ticker: ' + ticker
            )
            continue

        try:
            market = public_get(
                '/markets/' + ticker
            )['market']

            close = utc(
                market.get('close_time')
            )

            if close is None:
                continue

            if (
                now_utc().timestamp()
                < close.timestamp() + 60
            ):
                continue

            checked += 1

            status = str(
                market.get('status') or ''
            ).lower()

            result = str(
                market.get('result') or ''
            ).lower()

            if (
                status not in (
                    'settled', 'finalized'
                )
                or result not in ('yes', 'no')
            ):
                continue

            side = entry.get('side')
            qty = int(
                entry.get('qty') or 0
            )
            risk = number(
                entry.get('risk')
            )

            if (
                side not in ('ABOVE', 'BELOW')
                or qty <= 0
                or risk is None
                or risk <= 0
            ):
                warnings.append(
                    'Invalid entry accounting: '
                    + ticker
                )
                continue

            won = (
                (
                    side == 'ABOVE'
                    and result == 'yes'
                )
                or (
                    side == 'BELOW'
                    and result == 'no'
                )
            )

            pnl = round(
                (qty if won else 0) - risk,
                6,
            )

            record = {
                'ticker': ticker,
                'side': side,
                'result': result,
                'won': won,
                'qty': qty,
                'risk': risk,
                'pnl': pnl,
                'at': now_utc().isoformat(),
                'kind': 'OFFICIAL_PAPER_SETTLEMENT',
            }

            existing[key] = store.once(
                'royal_scalp_season2_finals',
                key,
                record,
            )

        except Exception as exc:
            warnings.append(
                ticker + ': ' + str(exc)[:100]
            )

    return existing, warnings


def royal_metrics(store):
    """Season 2 results, without deleting history."""

    st.markdown(
        '##### 👑 ROYAL SCALP · SEASON 2'
    )

    st.caption(
        'Started November 11, 2026 at '
        '10:00 PM Central. '
        'Previous paper results remain archived.'
    )

    entries = qe_season_entries(store)

    exits = {
        key: event
        for key, event in royal_objects(
            store, 'royal_scalp_exits'
        )
        if isinstance(event, dict)
    }

    finals, warnings = qe_season_settlements(
        store, entries, exits
    )

    completed = []
    open_rows = []

    for key, entry in entries:
        if key in exits:
            event = exits[key]
            kind = 'SIMULATED CASH OUT'
            pnl = number(event.get('pnl'))

        elif key in finals:
            event = finals[key]
            kind = 'OFFICIAL SETTLEMENT'
            pnl = number(event.get('pnl'))

        else:
            open_rows.append((key, entry))
            continue

        completed.append({
            'Contract': entry.get('ticker'),
            'Side': entry.get('side'),
            'Entry UTC': entry.get('at'),
            'Entry ask': entry.get('ask'),
            'Exit / grade UTC': event.get('at'),
            'Exit bid': (
                event.get('bid')
                if kind == 'SIMULATED CASH OUT'
                else None
            ),
            'Type': kind,
            'Net P&L': pnl,
            'Reason': (
                event.get('reason')
                if kind == 'SIMULATED CASH OUT'
                else (
                    'WIN' if event.get('won')
                    else 'LOSS'
                )
            ),
        })

    values = [
        row['Net P&L']
        for row in completed
        if row['Net P&L'] is not None
    ]

    wins = sum(v > 0 for v in values)
    losses = sum(v < 0 for v in values)

    a, b, c, d = st.columns(4)

    a.metric(
        'Season 2 net',
        f'${sum(values):+.2f}'
    )
    b.metric('Wins', wins)
    c.metric('Losses', losses)
    d.metric(
        'Open / completed',
        f'{len(open_rows)} / {len(completed)}'
    )

    st.caption(
        f'{len(entries)} Season 2 entries · '
        f'{len(completed)} completed · '
        'Paper-only estimated results.'
    )

    if open_rows:
        with st.expander(
            'Open / unresolved positions',
            expanded=False
        ):
            st.dataframe(
                [
                    {
                        'Contract': entry.get('ticker'),
                        'Side': entry.get('side'),
                        'Entry UTC': entry.get('at'),
                        'Entry ask': entry.get('ask'),
                        'Risk $': entry.get('risk'),
                        'Status': 'UNRESOLVED',
                    }
                    for _, entry in open_rows
                ],
                hide_index=True,
                use_container_width=True,
            )

    with st.expander(
        'Season 2 · complete trade history',
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
                'No completed Season 2 paper trades yet.'
            )

    if warnings:
        with st.expander(
            'Tracker verification warnings',
            expanded=False
        ):
            for warning in warnings:
                st.caption(warning)

    return entries, exits

'''

insertion = "def royal_momentum(df):"

if source.count(insertion) != 1:
    raise RuntimeError(
        "Royal Scalp insertion point not found"
    )

source = source.replace(
    insertion,
    season_code + "\n" + insertion,
    1,
)

# -----------------------------------------
# 4. VALIDATE AND LAUNCH
# -----------------------------------------

program = compile(
    source,
    str(BASE),
    "exec"
)

environment = {
    "__name__": "__main__",
    "__file__": str(BASE),
    "__package__": None,
}

exec(program, environment)
