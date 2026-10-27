"""Read-only hypothetical entry ledger. No real orders, no assumed guaranteed fills."""
from datetime import datetime, timezone
import requests

API = 'https://api.elections.kalshi.com/trade-api/v2'

class PaperStore:
    def __init__(self, url, key):
        self.endpoint = url.rstrip('/') + '/rest/v1/paper_entries'
        self.headers = {'apikey':key, 'Authorization':'Bearer '+key}

    def request(self, method, **kwargs):
        headers = {**self.headers, **kwargs.pop('headers', {})}
        response = requests.request(method, self.endpoint, headers=headers, timeout=12, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else []

    def all(self):
        return self.request('GET', params={'select':'*','order':'observed_at.desc','limit':'1000'})

    def record_once(self, market, ticker, side, ask, close_time):
        # One hypothetical entry per contract. Conflict-safe across reruns.
        row = {'ticker':ticker,'market_name':market,'side':side,
               'entry_price':round(float(ask), 4),
               'observed_at':datetime.now(timezone.utc).isoformat(),
               'close_time':close_time.isoformat() if close_time else None,
               'status':'pending'}
        self.request('POST', json=row, params={'on_conflict':'ticker'},
                     headers={'Prefer':'resolution=ignore-duplicates,return=minimal'})

    def settle(self, ticker, outcome):
        self.request('PATCH', params={'ticker':'eq.'+ticker,'status':'eq.pending'},
                     json={'status':'win' if outcome == 'yes' else 'loss', 'official_result':outcome},
                     headers={'Prefer':'return=minimal'})


def from_secrets(secrets):
    url = secrets.get('SUPABASE_URL')
    key = secrets.get('SUPABASE_SERVICE_ROLE_KEY')
    return PaperStore(url,key) if url and key else None


def official_outcome(ticker):
    response = requests.get(f'{API}/markets/{ticker}',timeout=12)
    response.raise_for_status()
    market = response.json().get('market', {})
    if str(market.get('status','')).lower() not in ('settled','finalized'):
        return None
    result = str(market.get('result','')).lower()
    return result if result in ('yes','no') else None


def grade_pending(store, rows, max_checks=8):
    """Grade closed contracts; never infer outcome from price or expiration alone."""
    now = datetime.now(timezone.utc)
    checks = 0
    for row in rows:
        if row.get('status') != 'pending' or checks >= max_checks:
            continue
        close_raw = row.get('close_time')
        try:
            close = datetime.fromisoformat(str(close_raw).replace('Z','+00:00'))
        except (TypeError,ValueError):
            continue
        if close.tzinfo is None:
            close = close.replace(tzinfo=timezone.utc)
        if close > now:
            continue
        checks += 1
        try:
            result = official_outcome(row['ticker'])
            if result:
                store.settle(row['ticker'], result)
        except (requests.RequestException, ValueError):
            continue


def pnl(row, fee_per_contract=0.02):
    """Hypothetical payout at $1 on win; entry ask + assumed total fees."""
    if row.get('status') not in ('win','loss'):
        return None
    entry = float(row['entry_price'])
    return (1.0 if row['status'] == 'win' else 0.0) - entry - fee_per_contract
