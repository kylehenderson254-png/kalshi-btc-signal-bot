
"""QUEEN'S EDGE | 254 — Royal Scalp Season 2.

PAPER ONLY. NO REAL ORDERS.

New scoreboard; old journal records preserved.
Original scalp entry and exit logic remains unchanged.
"""

from pathlib import Path

BASE = Path(__file__).resolve().parent / "queens_preview_base.py"

if not BASE.is_file():
    raise FileNotFoundError(
        "Missing queens_preview_base.py beside app.py"
    )

source = BASE.read_text(encoding="utf-8")

# Preserve the working dashboard function fix.
anchor = "        'qe_simple_header': qe_simple_header,\n"

replacement = (
    "        'qe_simple_header': qe_simple_header,\n"
    "        'qe_stage_row': qe_stage_row,\n"
    "        'qe_performance_center': qe_performance_center,\n"
    "        'visible_scanner_status': visible_scanner_status,\n"
)

if source.count(anchor) != 1:
    raise RuntimeError("Dashboard launcher version mismatch")

source = source.replace(anchor, replacement, 1)

# Keep the previous results function available.
old_metrics = "def royal_metrics(store):"

if source.count(old_metrics) != 1:
    raise RuntimeError("Royal Scalp metrics function not found")

source = source.replace(
    old_metrics,
    "def royal_metrics_previous(store):",
    1,
)

# Inject Season 2 reporting without changing royal_scan.
extension = r'''

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
    entries = royal_objects(store, 'royal_scalp_entries')
    result = []

    for key, entry in entries:
        if not isinstance(entry, dict):
            continue

        stamp = qe_season_timestamp(entry.get('at'))

        if stamp is not None and stamp >= QE_SEASON_2_START:
            result.append((key, entry))

    return result


def qe_season_settlements(store, entries, exits):
    """Reconcile expired paper positions from official results.

    Never create a fake cash-out or alter a saved scalp exit.
    """
    existing = dict(
        royal_objects(store, 'royal_scalp_season2_finals')
    )

    checked = 0
    warnings = []

    for key, entry in entries:
        if key in exits or key in existing:
            continue

        if checked >= 4:
            break

        ticker = str(entry.get('ticker') or '')

        if not ticker.startswith('KXBTC15M-'):
            warnings.append('Invalid contract: ' + ticker)
            continue

        try:
            # Retrieve the actual close time from Kalshi.
            market = public_get(
                '/markets/' + ticker
            )['market']

            close = utc(market.get('close_time'))

            if close is None:
                warnings.append(
                    'Missing close time: ' + ticker
                )
                continue

            if now_utc().timestamp() < close.timestamp() + 60:
                continue

            checked += 1

            status = str(
                market.get('status') or ''
            ).lower()

            outcome = str(
                market.get('result') or ''
            ).lower()

            if (
                status not in ('settled', 'finalized')
                or outcome not in ('yes', 'no')
            ):
                continue

            side = entry.get('side')
            qty = int(entry.get('qty') or 0)
            risk = number(entry.get('risk'))

            if (
                side not in ('ABOVE', 'BELOW')
                or qty <= 0
                or risk is None
                or risk <= 0
            ):
                warnings.append(
                    'Invalid entry accounting: ' + ticker
                )
                continue

            won = (
                (side == 'ABOVE' and outcome == 'yes')
                or (side == 'BELOW' and outcome == 'no')
            )

            pnl = round(
                (qty if won else 0) - risk,
                6,
            )

            record = {
                'ticker': ticker,
                'side': side,
                'result': outcome,
                'won': won,
                'qty': qty,
                'risk': risk,
                'pnl': pnl,
                'at': now_utc().isoformat(),
                'kind': 'OFFICIAL_PAPER_SETTLEMENT',
                'note': (
                    'Official settlement, not a simulated '
                    'cash-out or real trade.'
                ),
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
    """Season 2 scoreboard; original records are not deleted."""
    st.markdown('##### 👑 ROYAL SCALP · SEASON 2')
    st.caption(
        'Started November 11, 2026 at 10:00 PM Central. '
        'Earlier results remain in the original journal.'
    )

    entries = qe_season_entries(store)

    all_exits = dict(
        royal_objects(store, 'royal_scalp_exits')
    )

    exits = {
        key: event
        for key, event in all_exits.items()
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

    wins = sum(value > 0 for value in values)
    losses = sum(value < 0 for value in values)

    a, b, c, d = st.columns(4)

    a.metric(
        'Season 2 net',
        f'${sum(values):+.2f}',
    )
    b.metric('Wins', wins)
    c.metric('Losses', losses)
    d.metric(
        'Open / completed',
        f'{len(open_rows)} / {len(completed)}',
    )

    st.caption(
        f'{len(entries)} Season 2 entries recorded · '
        f'{len(values)} graded or closed · '
        'Estimated paper fills and fees. '
        'No real orders.'
    )

    if open_rows:
        with st.expander(
            'Open / awaiting settlement',
            expanded=False,
        ):
            st.dataframe(
                [
                    {
                        'Contract': entry.get('ticker'),
                        'Side': entry.get('side'),
                        'Entry UTC': entry.get('at'),
                        'Entry ask': entry.get('ask'),
                        'Risk $': entry.get('risk'),
                        'Status': 'OPEN / UNRESOLVED',
                    }
                    for _, entry in open_rows
                ],
                hide_index=True,
                use_container_width=True,
            )

    with st.expander(
        'Season 2 · complete trade history',
        expanded=False,
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
            expanded=False,
        ):
            for warning in warnings:
                st.caption(warning)

    return entries, exits

'''

insertion = "def royal_momentum(df):"

if source.count(insertion) != 1:
    raise RuntimeError("Royal Scalp insertion point not found")

source = source.replace(
    insertion,
    extension + "\n" + insertion,
    1,
)

# Check syntax before running the existing dashboard.
program = compile(source, str(BASE), "exec")

environment = {
    "__name__": "__main__",
    "__file__": str(BASE),
    "__package__": None,
}

exec(program, environment)
