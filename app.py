
"""QUEEN'S EDGE | 254 — V4 paper tracking overlay. PAPER ONLY.

Keep queens_preview_base.py, queens_original.py, queens_live_chart.py,
and queens_v3_guard.py in this directory. Never sends orders.
"""
import importlib.util
import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'queens_preview_base.py'
if not BASE.is_file():
    st.error('Missing queens_preview_base.py. Rename the previous app.py to that name first.')
    st.stop()

spec = importlib.util.spec_from_file_location('queens_preview_base', BASE)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def all_paper_rows(self):
    """Read every stored V4 entry, not just the first 200."""
    names = []
    offset = 0
    page_size = 200
    while True:
        objects = self.request('POST', '/object/list/' + base.BUCKET, {
            'prefix': 'entries', 'limit': page_size, 'offset': offset,
            'sortBy': {'column': 'name', 'order': 'desc'},
        })
        if not isinstance(objects, list):
            raise RuntimeError('Unexpected paper journal listing response')
        names.extend(item['name'] for item in objects
                     if isinstance(item, dict) and
                     str(item.get('name', '')).endswith('.json'))
        if len(objects) < page_size:
            break
        offset += page_size
        if offset >= 10000:
            raise RuntimeError('Paper journal exceeds safe pagination limit')

    def load(key):
        entry = self.get('entries', key)
        if not isinstance(entry, dict):
            raise RuntimeError('Stored paper entry could not be read: ' + key)
        final = self.get('finals', key)
        return {'key': key, 'entry': entry, 'final': final,
                'exit': None if final else self.get('exits', key)}

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(load, names))


def clearer_paper_metrics(rows):
    """Show provisional exits separately from officially matched P&L."""
    finals = [r for r in rows if r.get('final')]
    exited_pending = [r for r in rows if not r.get('final') and r.get('exit')]
    open_rows = [r for r in rows if not r.get('final') and not r.get('exit')]
    matched_managed = sum(float(r['final']['managed_pnl']) for r in finals)
    matched_hold = sum(float(r['final']['hold_pnl']) for r in finals)
    pending_exit = sum(float(r['exit']['pnl']) for r in exited_pending)

    a, b = st.columns(2)
    a.metric('Matched managed P&L · official', f'${matched_managed:+.2f}')
    b.metric('Matched settlement P&L · official', f'${matched_hold:+.2f}')
    a.metric('Paper entries / official outcomes', f'{len(rows)} / {len(finals)}')
    b.metric('Today · managed realized', f'${base.daily_pnl(rows):+.2f}')
    a, b, c = st.columns(3)
    a.metric('Open positions', len(open_rows))
    b.metric('Exited · awaiting settlement', len(exited_pending))
    c.metric('Officially graded', len(finals))
    st.metric('Estimated exited P&L · pending official result',
              f'${pending_exit:+.2f}')
    st.caption('Pending exit P&L uses estimated fills and fees. It is NOT '
               'included in officially matched totals; it may change after '
               'settlement reconciliation. No real orders are placed.')
    if finals:
        wins = sum(float(r['final']['managed_pnl']) > 0 for r in finals)
        st.caption(f'Officially matched managed wins: {wins}/{len(finals)} '
                   f'({wins / len(finals):.1%}). Paper estimates only.')


def fix_mid_quote_mapping():
    """Preserve the original cards while correcting ABOVE/BELOW lookup."""
    source = inspect.getsource(base.qe_stage_row)
    replacements = {
        "quote(market, 'yes', 'bid')": "quote(market, 'ABOVE', 'bid')",
        "quote(market, 'yes', 'ask')": "quote(market, 'ABOVE', 'ask')",
        "quote(market, 'no', 'bid')": "quote(market, 'BELOW', 'bid')",
        "quote(market, 'no', 'ask')": "quote(market, 'BELOW', 'ask')",
    }
    for before, after in replacements.items():
        if source.count(before) != 1:
            raise RuntimeError('MID quote compatibility check failed: ' + before)
        source = source.replace(before, after)
    exec(compile(source, str(BASE), 'exec'), base.__dict__)


base.PaperJournal.rows = all_paper_rows
base.paper_metrics = clearer_paper_metrics
try:
    fix_mid_quote_mapping()
except (OSError, TypeError, RuntimeError) as exc:
    st.error('Compatibility check failed. No trading logic was changed: ' + str(exc))
    st.stop()

if __name__ == '__main__':
    base.launch()
