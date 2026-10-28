"""Read-only, conservative strong-lock paper-entry decisions. No real orders."""
from datetime import datetime, timezone
import requests

API = 'https://api.elections.kalshi.com/trade-api/v2'

class StrongLockStore:
    def __init__(self, url, key):
        self.endpoint = url.rstrip('/') + '/rest/v1/strong_lock_entries'
        self.headers = {'apikey': key, 'Authorization': 'Bearer ' + key}

    def request(self, method, **kwargs):
        headers = {**self.headers, **kwargs.pop('headers', {})}
        response = requests.request(method, self.endpoint, headers=headers, timeout=12, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else []

    def all(self):
        return self.request('GET', params={'select': '*', 'order': 'observed_at.desc', 'limit': '1000'})

    def record_once(self, row):
        self.request('POST', json=row, params={'on_conflict': 'ticker'},
                     headers={'Prefer': 'resolution=ignore-duplicates,return=minimal'})

    def settle(self, ticker, result, side):
        self.request('PATCH', params={'ticker': 'eq.' + ticker, 'decision': 'eq.ENTER', 'status': 'eq.pending'},
                     json={'status': 'win' if ((result == 'yes') == (side == 'ABOVE')) else 'loss', 'official_result': result},
                     headers={'Prefer': 'return=minimal'})


def from_secrets(secrets):
    url, key = secrets.get('SUPABASE_URL'), secrets.get('SUPABASE_SERVICE_ROLE_KEY')
    return StrongLockStore(url, key) if url and key else None


def evaluate(locked, market, history, verified, now=None):
    """A quote-implied filter, NOT a calibrated win probability or proven edge."""
    now = now or datetime.now(timezone.utc)
    side = locked.get('call')
    yes_mid = float(locked.get('yes_midpoint') or 0)
    confidence = yes_mid if side == 'ABOVE' else 1 - yes_mid
    ask_field = 'yes_ask' if side == 'ABOVE' else 'no_ask'
    bid_field = 'yes_bid' if side == 'ABOVE' else 'no_bid'
    ask, bid = market.get(ask_field), market.get(bid_field)
    reasons = []
    if not verified or side not in ('ABOVE', 'BELOW'):
        reasons.append('Contract direction unverified')
    if confidence < .70:
        reasons.append('Locked quote strength below 70¢')
    if ask is None or bid is None or not (0 < bid <= ask < 1):
        reasons.append('Valid side bid/ask unavailable')
    else:
        if ask > .80:
            reasons.append('Entry ask exceeds 80¢ cap')
        if ask - bid > .04:
            reasons.append('Spread exceeds 4¢')
        # Conservative price sanity check; this is not proof of positive expected value.
        if ask + .02 >= 1:
            reasons.append('No upside after assumed 2¢ fee')
    if len(history) < 3:
        reasons.append('Insufficient live quote history')
    else:
        latest = history[-1][0]
        moves = []
        for i in range(1, len(history)):
            if (latest - history[i][0]).total_seconds() > 90:
                continue
            delta = history[i][1] - history[i-1][1]
            if delta >= .005:
                moves.append(1)
            elif delta <= -.005:
                moves.append(-1)
        required = [1, 1] if side == 'ABOVE' else [-1, -1]
        if moves[-2:] != required:
            reasons.append('Recent momentum not confirmed')
    return ('SKIP' if reasons else 'ENTER'), ('; '.join(reasons) if reasons else 'All experimental filters passed'), ask, confidence


def grade_pending(store, rows, max_checks=8):
    now = datetime.now(timezone.utc)
    checks = 0
    for row in rows:
        if row.get('decision') != 'ENTER' or row.get('status') != 'pending' or checks >= max_checks:
            continue
        try:
            close = datetime.fromisoformat(str(row.get('close_time')).replace('Z', '+00:00'))
            if close.tzinfo is None:
                close = close.replace(tzinfo=timezone.utc)
            if close > now:
                continue
            checks += 1
            response = requests.get(f"{API}/markets/{row['ticker']}", timeout=12)
            response.raise_for_status()
            market = response.json().get('market', {})
            result = str(market.get('result') or '').lower()
            if str(market.get('status') or '').lower() in ('settled', 'finalized') and result in ('yes', 'no'):
                # ABOVE maps to YES only after verification at decision time.
                store.settle(row['ticker'], result, row['side'])
        except (requests.RequestException, ValueError, TypeError):
            continue
