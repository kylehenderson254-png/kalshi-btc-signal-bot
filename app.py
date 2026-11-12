
"""QUEEN'S EDGE | 254 — Royal Scalp tracker repair.

PAPER ONLY. NO REAL ORDERS.

Keeps the existing dashboard and scalp trading rules.
Adds official settlement reconciliation for expired scalp
positions, without inventing simulated cash-out fills.
"""

from pathlib import Path

BASE = Path(__file__).resolve().parent / "queens_preview_base.py"

if not BASE.is_file():
    raise FileNotFoundError(
        "Missing queens_preview_base.py beside app.py"
    )

source = BASE.read_text(encoding="utf-8")

# Preserve the previously working dashboard launcher fix.
anchor = "        'qe_simple_header': qe_simple_header,\n"

replacement = (
    "        'qe_simple_header': qe_simple_header,\n"
    "        'qe_stage_row': qe_stage_row,\n"
    "        'qe_performance_center': qe_performance_center,\n"
    "        'visible_scanner_status': visible_scanner_status,\n"
)

if source.count(anchor) != 1:
    raise RuntimeError(
        "Dashboard version mismatch. No files were changed."
    )

source = source.replace(anchor, replacement, 1)

# Extend the existing Royal Scalp tracker in memory.
# Do not edit or overwrite its original stored records.
TRACKER_EXTENSION = r'''

def qe_reconcile_royal_scalp(store, entries=None, exits=None):
    """Grade expired open paper entries from official Kalshi results.

    A settlement is NOT recorded as a simulated cash-out.
    The separate final record preserves that distinction.
    """
    if entries is None:
        entries = royal_objects(store, 'royal_scalp_entries')
    if exits is None:
        exits = dict(royal_objects(store, 'royal_scalp_exits'))

    finals = dict(royal_objects(store, 'royal_scalp_finals'))
    checked = 0
    issues = []

    for key, entry in entries:
        if key in exits or key in finals:
            continue

        if not isinstance(entry, dict):
            issues.append('Invalid saved entry: ' + str(key))
            continue

        ticker = str(entry.get('ticker') or '')
        if not ticker.startswith('KXBTC15M-'):
            issues.append('Unrecognized contract: ' + ticker)
            continue

        # Kalshi 15-minute ticker contains its contract start
        # timestamp. Use the saved contract ticker, not the
        # current market, when checking old paper positions.
        import re
        from datetime import datetime, timedelta, timezone

        match = re.search(
            r'-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})-',
            ticker,
        )

        if match is None:
            issues.append('Cannot parse expiry: ' + ticker)
            continue

        yy, month, dd, hh, mm = match.groups()

        months = {
            'JAN': 1, 'FEB': 2, 'MAR': 3,
            'APR': 4, 'MAY': 5, 'JUN': 6,
            'JUL': 7, 'AUG': 8, 'SEP': 9,
            'OCT': 10, 'NOV': 11, 'DEC': 12,
        }

        try:
            start = datetime(
                2000 + int(yy),
                months[month],
                int(dd),
                int(hh),
                int(mm),
                tzinfo=timezone.utc,
            )
        except (ValueError, KeyError):
            issues.append('Invalid contract timestamp: ' + ticker)
            continue

        # Only examine contracts that are definitely old.
        if now_utc() < start + timedelta(minutes=17):
            continue

        if checked >= 5:
            break

        checked += 1

        try:
            market = public_get('/markets/' + ticker)['market']
            result = str(market.get('result') or '').lower()
            status = str(market.get('status') or '').lower()

            if status not in ('settled', 'finalized'):
                continue

            if result not in ('yes', 'no'):
                continue

            side = entry.get('side')
            if side not in ('ABOVE', 'BELOW'):
                issues.append('Invalid saved side: ' + ticker)
                continue

            qty = int(entry.get('qty') or 0)
            risk = number(entry.get('risk'))

            if qty <= 0 or risk is None or risk <= 0:
                issues.append('Invalid entry accounting: ' + ticker)
                continue

            won = (
                (side == 'ABOVE' and result == 'yes')
                or (side == 'BELOW' and result == 'no')
            )

            settlement_pnl = round(
                (qty if won else 0) - risk,
                6,
            )

            final = {
                'kind': 'ROYAL_SCALP_OFFICIAL_SETTLEMENT',
                'ticker': ticker,
                'side': side,
                'official_result': result,
                'won': won,
                'qty': qty,
                'entry_risk': risk,
                'settlement_pnl': settlement_pnl,
                'graded_at': now_utc().isoformat(),
                'note': (
                    'Official settlement of an expired paper '
                    'position; NOT a simulated cash-out fill.'
                ),
            }

            finals[key] = store.once(
                'royal_scalp_finals',
                key,
                final,
            )

        except Exception as exc:
            issues.append(
                ticker + ': ' + str(exc)[:100]
            )

    return finals, issues


def qe_royal_metrics_repaired(store):
    """Show cash-outs, settlements, and unresolved opens separately."""
    entries = royal_objects(store, 'royal_scalp_entries')
    exits = dict(royal_objects(store, 'royal_scalp_exits'))

    finals, issues = qe_reconcile_royal_scalp(
        store, entries, exits
    )

    cashouts = []
    settlements = []
    unresolved = []

    for key, entry in entries:
        if not isinstance(entry, dict):
            continue

        if key in exits and isinstance(exits[key], dict):
            cashouts.append((key, entry, exits[key]))
        elif key in finals and isinstance(finals[key], dict):
            settlements.append((key, entry, finals[key]))
        else:
            unresolved.append((key, entry))

    cashout_pnls = [
        number(event.get('pnl'))
        for _, _, event in cashouts
    ]
    cashout_pnls = [x for x in cashout_pnls if x is not None]

    settlement_pnls = [
        number(event.get('settlement_pnl'))
        for _, _, event in settlements
    ]
    settlement_pnls = [
        x for x in settlement_pnls if x is not None
    ]

    combined = cashout_pnls + settlement_pnls

    st.markdown('##### ⚡ ROYAL SCALP · VERIFIED PAPER RESULTS')

    a, b, c, d = st.columns(4)

    a.metric(
        'Total paper net',
        f'${sum(combined):+.2f}',
    )
    b.metric(
        'Wins',
        sum(value > 0 for value in combined),
    )
    c.metric(
        'Losses',
        sum(value < 0 for value in combined),
    )
    d.metric(
        'Open / completed',
        f'{len(unresolved)} / '
        f'{len(cashouts) + len(settlements)}',
    )

    st.caption(
        f'{len(cashouts)} simulated cash-outs · '
        f'{len(settlements)} official settlements · '
        f'{len(unresolved)} unresolved paper positions. '
        'Estimated fills and fees; no real orders.'
    )

    if unresolved:
        st.warning(
            f'{len(unresolved)} paper position(s) remain unresolved. '
            'They are not counted as wins or losses.'
        )

        with st.expander(
            '⚠️ Open / unresolved scalp positions',
            expanded=True,
        ):
            st.dataframe(
                [
                    {
                        'Contract': entry.get('ticker'),
                        'Side': entry.get('side'),
                        'Entry time UTC': entry.get('at'),
                        'Entry ask': entry.get('ask'),
                        'Risk $': entry.get('risk'),
                        'Status': 'Awaiting verification',
                    }
                    for _, entry in unresolved
                ],
                hide_index=True,
                use_container_width=True,
            )

    if cashouts or settlements:
        with st.expander(
            'Royal Scalp · complete paper history',
            expanded=False,
        ):
            history = []

            for _, entry, event in cashouts:
                history.append({
                    'Contract': entry.get('ticker'),
                    'Side': entry.get('side'),
                    'Entry time UTC': entry.get('at'),
                    'Entry ask': entry.get('ask'),
                    'Exit time UTC': event.get('at'),
                    'Exit bid': event.get('bid'),
                    'Net P&L': event.get('pnl'),
                    'Outcome type': 'Simulated cash-out',
                    'Reason': event.get('reason'),
                })

            for _, entry, event in settlements:
                history.append({
                    'Contract': entry.get('ticker'),
                    'Side': entry.get('side'),
                    'Entry time UTC': entry.get('at'),
                    'Entry ask': entry.get('ask'),
                    'Exit time UTC': event.get('graded_at'),
                    'Exit bid': None,
                    'Net P&L': event.get('settlement_pnl'),
                    'Outcome type': 'Official settlement',
                    'Reason': (
                        'WIN' if event.get('won')
                        else 'LOSS'
                    ),
                })

            st.dataframe(
                history,
                hide_index=True,
                use_container_width=True,
            )

    if issues:
        with st.expander(
            'Tracker verification warnings',
            expanded=False,
        ):
            for issue in issues:
                st.caption(issue)

    return entries, exits


# Replace only the display/accounting function.
# The original royal_scan entry/exit logic is untouched.
royal_metrics = qe_royal_metrics_repaired

'''

target = "def qe_performance_center():"

if source.count(target) != 1:
    raise RuntimeError(
        "Royal Scalp integration point not found. "
        "No files were changed."
    )

source = source.replace(
    target,
    TRACKER_EXTENSION + "\n" + target,
    1,
)

# Compile the modified dashboard before launching it.
compile(source, str(BASE), "exec")

environment = {
    "__name__": "__main__",
    "__file__": str(BASE),
    "__package__": None,
}

exec(
    compile(source, str(BASE), "exec"),
    environment,
)
