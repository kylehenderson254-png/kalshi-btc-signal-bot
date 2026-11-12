
"""QUEEN'S EDGE | 254 — compact preview dashboard.

PAPER ONLY. NO REAL ORDERS.

Keep queens_original.py, queens_live_chart.py and
queens_v3_guard.py beside this file.
"""
import ast
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from queens_live_chart import render_live_chart
from queens_v3_guard import render_v3_guard, assess, _get_candles

API = 'https://external-api.kalshi.com/trade-api/v2'
BUCKET = 'queens-edge-paper-v4'
CT = ZoneInfo('America/Chicago')

RISK = 5.0
ENTRY_CAP = 0.66  # Effective fill including 1c slippage; 45-65c displayed ask
MIN_ASK = 0.45
MAX_ASK = 0.65
SHADOW_TARGETS = (0.70, 0.75, 0.80)
SLIPPAGE = 0.01
TAKE_PROFIT = 0.25
STOP_REVIEW = 0.2
DAILY_STOP = 15.0


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def utc(value):
    try:
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
    except (ValueError, TypeError):
        return None


def now_utc():
    return datetime.now(timezone.utc)


def snapshot_current(quote_at, trade_time):
    now = now_utc()
    return (
        -3 <= (now - quote_at).total_seconds() <= 8
        and -3 <= (now - trade_time).total_seconds() <= 10
    )


def cents_round(value, up=True):
    return float(
        Decimal(str(value)).quantize(
            Decimal('0.01'),
            rounding=ROUND_CEILING if up else ROUND_FLOOR,
        )
    )


def fee(qty, price, multiplier):
    model = (
        Decimal('0.07')
        * Decimal(str(multiplier))
        * qty
        * Decimal(str(price))
        * (1 - Decimal(str(price)))
    )
    return float(
        model.quantize(Decimal('0.01'), rounding=ROUND_CEILING)
    )


def size_for(price, multiplier):
    if not 0 < price < 1 or not multiplier >= 0:
        return 0, 0.0, 0.0

    qty = int(RISK / price)

    while (
        qty > 0
        and qty * price + fee(qty, price, multiplier) > RISK + 1e-9
    ):
        qty -= 1

    entry_fee = fee(qty, price, multiplier)
    cost = round(qty * price + entry_fee, 6)
    return qty, entry_fee, cost


def quote(market, side, kind):
    prefix = 'yes' if side == 'ABOVE' else 'no'
    return number(market.get(prefix + '_' + kind + '_dollars'))


def available(market, side, kind):
    # NO asks mirror YES bids; NO bids mirror YES asks.
    if side == 'BELOW':
        kind = 'bid' if kind == 'ask' else 'ask'
    return number(market.get('yes_' + kind + '_size_fp'))


def public_get(path, fresh=False):
    response = requests.get(
        API + path,
        timeout=7,
        headers={'Cache-Control': 'no-cache'},
    )
    response.raise_for_status()

    if fresh:
        date = response.headers.get('Date')
        if not date:
            raise RuntimeError(
                'Quote response has no verifiable HTTP timestamp'
            )

        from email.utils import parsedate_to_datetime

        age = (
            now_utc() - parsedate_to_datetime(date)
        ).total_seconds()

        if not -3 <= age <= 12:
            raise RuntimeError('Quote response timestamp is stale')

    return response.json()


@st.cache_data(ttl=300, show_spinner=False)
def series_fee():
    series = public_get('/series/KXBTC15M')['series']
    multiplier = number(series.get('fee_multiplier'))

    if (
        series.get('fee_type') != 'quadratic'
        or multiplier is None
        or multiplier < 0
    ):
        raise RuntimeError(
            'This paper test cannot model the current series fee type'
        )

    return multiplier


def trade_snapshot():
    response = requests.get(
        'https://api.exchange.coinbase.com/products/BTC-USD/ticker',
        timeout=7,
    )
    response.raise_for_status()
    data = response.json()

    price = number(data.get('price'))
    stamp = utc(data.get('time'))
    age = (now_utc() - stamp).total_seconds() if stamp else None

    if (
        price is None
        or price <= 0
        or age is None
        or not -3 <= age <= 10
    ):
        raise RuntimeError(
            'BTC trade timestamp is stale or unavailable'
        )

    return price, stamp


class PaperJournal:
    """Private append-only events; old tracker tables are preserved."""

    def __init__(self, url, key):
        self.base = url.rstrip('/') + '/storage/v1'
        self.headers = {
            'apikey': key,
            'Authorization': 'Bearer ' + key,
        }
        self.cache = {}

    def request(self, method, path, payload=None, missing=False):
        response = requests.request(
            method,
            self.base + path,
            headers={
                **self.headers,
                'Content-Type': 'application/json',
                'x-upsert': 'false',
                'Cache-Control': 'no-cache, no-store',
            },
            json=payload,
            timeout=10,
        )

        try:
            data = response.json()
        except ValueError:
            data = {}

        if isinstance(data, dict):
            code = str(
                data.get('code') or data.get('error') or ''
            )
            status = str(data.get('statusCode', ''))
        else:
            code = ''
            status = ''

        if missing and (
            response.status_code == 404
            or status == '404'
            or code in ('NoSuchKey', 'NoSuchBucket')
        ):
            return None

        if (
            response.status_code == 409
            or status == '409'
            or code == 'Duplicate'
        ):
            return {'duplicate': True}

        if not response.ok:
            raise RuntimeError(
                'Private paper journal request failed (HTTP '
                + str(response.status_code)
                + ')'
            )

        return data

    def ensure(self, create=False):
        bucket = self.request(
            'GET',
            '/bucket/' + BUCKET,
            missing=True,
        )

        if bucket is None and create:
            self.request(
                'POST',
                '/bucket',
                {
                    'id': BUCKET,
                    'name': BUCKET,
                    'public': False,
                },
            )
            bucket = self.request('GET', '/bucket/' + BUCKET)

        if bucket is None:
            return False

        if bucket.get('public') is not False:
            raise RuntimeError(
                'The V4 journal must be private; paper entries are blocked'
            )

        return True

    def get(self, folder, key):
        path = folder + '/' + key

        if path not in self.cache:
            value = self.request(
                'GET',
                '/object/authenticated/' + BUCKET + '/' + path,
                missing=True,
            )
            if value is not None:
                self.cache[path] = value
            return value

        return self.cache[path]

    def verify_saved(self, folder, key, expected):
        """Remote read-after-write verification; never trust the local cache."""
        path = folder + '/' + key
        remote = self.request(
            'GET', '/object/authenticated/' + BUCKET + '/' + path,
            missing=True,
        )
        if not isinstance(remote, dict) or remote != expected:
            raise RuntimeError(
                'Paper journal read-back failed for ' + folder
                + '; scanner stopped to prevent an unverified entry'
            )
        self.cache[path] = remote
        return remote

    def once(self, folder, key, value):
        result = self.request(
            'POST',
            '/object/' + BUCKET + '/' + folder + '/' + key,
            value,
        )

        if result.get('duplicate'):
            existing = self.get(folder, key)
            if existing is None:
                raise RuntimeError(
                    'Could not verify the existing paper event'
                )
            return self.verify_saved(folder, key, existing)

        return self.verify_saved(folder, key, value)

    def control(self):
        # Mutable settings bypass the immutable-event cache.
        return self.request(
            'GET',
            '/object/authenticated/' + BUCKET + '/settings/control.json',
            missing=True,
        )

    def save_control(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Paper switch must be ON or PAUSED')

        self.request(
            'PUT',
            '/object/' + BUCKET + '/settings/control.json',
            {
                'enabled': enabled,
                'updated_at': now_utc().isoformat(),
            },
        )

    def rows(self):
        objects = self.request(
            'POST',
            '/object/list/' + BUCKET,
            {
                'prefix': 'entries',
                'limit': 200,
                'offset': 0,
                'sortBy': {
                    'column': 'name',
                    'order': 'desc',
                },
            },
        )

        names = [
            item['name']
            for item in objects
            if str(item.get('name', '')).endswith('.json')
        ]

        def load(key):
            entry = self.get('entries', key)

            if not entry:
                raise RuntimeError('A paper entry could not be read')

            final = self.get('finals', key)

            return {
                'key': key,
                'entry': entry,
                'final': final,
                'exit': None if final else self.get('exits', key),
                'shadow': {int(t * 100): self.get('shadow', key[:-5] + f'_{int(t*100)}.json') for t in SHADOW_TARGETS},
            }

        with ThreadPoolExecutor(max_workers=6) as pool:
            return list(pool.map(load, names))


def journal_diagnostic(store):
    """Write/read a harmless diagnostic object; never create a trade."""
    key = 'readback-v1.json'
    payload = {'kind': 'paper-journal-diagnostic', 'version': 1}
    stored = store.once('diagnostics', key, payload)
    store.verify_saved('diagnostics', key, stored)
    # Settlement arithmetic test, independent of live Kalshi outcome.
    qty, fee_estimate, risk = size_for(0.66, 1.0)
    assert qty > 0 and risk <= RISK + 1e-9
    win_net = round(qty - risk, 6)
    loss_net = round(-risk, 6)
    assert win_net > loss_net
    return 'Storage read/write verified · settlement math checked (synthetic only)'


def journal(url, key):
    return PaperJournal(url, key)

def exit_estimate(entry, market):
    bid = quote(market, entry['side'], 'bid')
    ask = quote(market, entry['side'], 'ask')
    size = available(market, entry['side'], 'bid')
    slippage = entry.get('slippage', SLIPPAGE)

    if (
        bid is None
        or ask is None
        or not slippage < bid <= ask < 1
        or size is None
        or size < entry['qty']
    ):
        return None

    price = max(0.0, cents_round(bid - slippage, False))
    exit_fee = fee(entry['qty'], price, entry['fee_multiplier'])

    return {
        'price': price,
        'fee': exit_fee,
        'pnl': round(
            entry['qty'] * price - exit_fee - entry['risk'],
            6,
        ),
    }


def close_reason(entry, estimate, spot, trend, seconds):
    if estimate['pnl'] >= (
        entry.get('take_profit_fraction', TAKE_PROFIT)
        * entry['risk']
    ):
        return 'Experimental profit review: +25% of entry risk'

    if estimate['pnl'] <= (
        -entry.get('loss_review_fraction', STOP_REVIEW)
        * entry['risk']
    ):
        return 'Experimental loss review: -20% of entry risk'

    if (
        entry['side'] == 'ABOVE' and spot < entry['target']
    ) or (
        entry['side'] == 'BELOW' and spot > entry['target']
    ):
        return 'Spot crossed the target against the position'

    if (
        entry['side'] == 'ABOVE' and trend == 'DOWNWARD'
    ) or (
        entry['side'] == 'BELOW' and trend == 'UPWARD'
    ):
        return 'Opposing EMA and momentum confirmation'

    if seconds <= 75:
        return 'Exit review before the final settlement minute'

    return None


def grade_papers(store, rows):
    checks = 0

    for row in rows:
        entry = row['entry']

        if (
            row['final']
            or now_utc().timestamp() < entry['close_ts'] + 60
            or checks >= 5
        ):
            continue

        checks += 1

        try:
            market = public_get(
                '/markets/' + entry['ticker']
            )['market']

            outcome = market.get('result')

            if (
                market.get('status') not in ('settled', 'finalized')
                or outcome not in ('yes', 'no')
            ):
                continue

            win = (
                (entry['side'] == 'ABOVE')
                == (outcome == 'yes')
            )

            hold_pnl = round(
                (entry['qty'] if win else 0) - entry['risk'],
                6,
            )

            exit_event = (
                row['exit'] or store.get('exits', row['key'])
            )

            closed_at = (
                exit_event['observed_at']
                if exit_event
                else datetime.fromtimestamp(
                    entry['close_ts'],
                    timezone.utc,
                ).isoformat()
            )

            final = {
                'outcome': outcome,
                'value': market.get('expiration_value'),
                'hold_pnl': hold_pnl,
                'managed_pnl': (
                    exit_event['pnl'] if exit_event else hold_pnl
                ),
                'exit_reason': (
                    exit_event['reason']
                    if exit_event
                    else 'Held to official settlement'
                ),
                'managed_closed_at': closed_at,
                'graded_at': now_utc().isoformat(),
            }

            row['final'] = store.once(
                'finals',
                row['key'],
                final,
            )

        except (requests.RequestException, ValueError, KeyError):
            continue


def candidate(market, result, df, spot, multiplier):
    if result.get('status') != 'CANDIDATE':
        reasons = list(result.get('reasons') or ['V3 candidate conditions did not pass'])
        side = result.get('side')
        if side in ('ABOVE', 'BELOW'):
            ask = quote(market, side, 'ask')
            if ask is not None and not MIN_ASK <= ask <= MAX_ASK:
                reasons.insert(0, f'{side} ask {ask*100:.0f}¢ outside V4 45–65¢ entry range')
        return None, reasons

    side = result.get('side')
    if df is None or len(df) < 60 or 'time' not in df:
        return None, [
            'Need 60 consecutive 1-minute candles for this forward test'
        ]

    pd = __import__('pandas')

    times = pd.to_datetime(
        df['time'].tail(60),
        utc=True,
        errors='coerce',
    )
    intervals = times.diff().dt.total_seconds().dropna()

    if (
        times.isna().any()
        or times.duplicated().any()
        or not intervals.between(45, 90).all()
    ):
        return None, [
            'Recent 1-minute candles have gaps or inconsistent timestamps'
        ]

    ask = quote(market, side, 'ask')
    bid = quote(market, side, 'bid')

    if (
        ask is None
        or bid is None
        or not 0 <= bid <= ask < 1
        or ask - bid > 0.030001
    ):
        return None, [
            'Executable quotes are missing or the spread is wider than 3¢'
        ]

    price = cents_round(ask + SLIPPAGE)

    if not MIN_ASK <= ask <= MAX_ASK or not 0 < price <= ENTRY_CAP:
        return None, [
            'Entry must be 45–65¢ ask, with 1¢ estimated adverse slippage'
        ]

    qty, entry_fee, risk = size_for(price, multiplier)
    size = available(market, side, 'ask')

    if qty < 1 or size is None or size < qty:
        return None, [
            'Insufficient displayed size for this paper entry'
        ]

    previous = df['close'].shift(1)

    ranges = pd.concat(
        [
            (df['high'] - df['low']).abs(),
            (df['high'] - previous).abs(),
            (df['low'] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = float(ranges.tail(14).mean())
    seconds = float(result['seconds_left'])

    cushion = max(
        50,
        2 * atr * math.sqrt(seconds / 60),
    )

    distance = abs(
        spot - float(market['floor_strike'])
    )

    if distance < cushion:
        return None, [
            'Target cushion is too small relative to recent volatility'
        ]

    candle_age = result.get('candle_age')

    if candle_age is None or not -3 <= candle_age <= 90:
        return None, ['Need candles no older than 90 seconds']

    return {
        'side': side,
        'price': price,
        'qty': qty,
        'fee': entry_fee,
        'risk': risk,
        'distance': distance,
        'breakeven': risk / qty,
    }, []


def _paper_toggle():
    st.session_state['qe_v4_pending'] = bool(
        st.session_state['_qe_v4_switch']
    )


def paper_enabled(store):
    # Settings are mutable. Trade/exit/final records remain append-only.
    control = store.control()

    if control is None:
        legacy = st.session_state.get('qe_v4_enabled') is True

        store.once(
            'settings',
            'control.json',
            {
                'enabled': legacy,
                'updated_at': now_utc().isoformat(),
            },
        )

        control = store.control()

    if (
        not isinstance(control, dict)
        or type(control.get('enabled')) is not bool
    ):
        raise RuntimeError(
            'Saved paper switch is invalid; no new entries'
        )

    if 'qe_v4_pending' in st.session_state:
        wanted = st.session_state['qe_v4_pending']
        store.save_control(wanted)
        control = store.control()

        if control is None or control.get('enabled') is not wanted:
            raise RuntimeError(
                'Paper switch could not be saved; retry the switch'
            )

        st.session_state.pop('qe_v4_pending', None)
        st.session_state.pop('qe_v4_confirm', None)

    enabled = control['enabled']

    st.session_state['_qe_v4_switch'] = enabled

    st.checkbox(
        'Enable new V4 paper entries — never real orders',
        key='_qe_v4_switch',
        on_change=_paper_toggle,
    )

    st.caption(
        ('ON' if enabled else 'PAUSED')
        + ' · saved across reconnects and devices. '
        'Processing runs only while the dashboard is open.'
    )

    return enabled


def fresh_snapshot(ticker):
    # Workers use HTTP and timestamps, never Streamlit widgets.
    def market_request():
        data = public_get(
            '/markets/' + ticker,
            fresh=True,
        )['market']
        return data, now_utc()

    with ThreadPoolExecutor(max_workers=2) as pool:
        market_job = pool.submit(market_request)
        trade_job = pool.submit(trade_snapshot)

        market, quote_at = market_job.result()
        price, trade_time = trade_job.result()

    if not snapshot_current(quote_at, trade_time):
        raise RuntimeError(
            'Fresh quote / BTC trade unavailable; waiting for a new snapshot'
        )

    return market, quote_at, price, trade_time


def daily_pnl(rows):
    today = now_utc().astimezone(CT).date()
    total = 0.0

    for row in rows:
        event = row['final'] or row['exit']

        if event:
            closed = utc(
                event.get('managed_closed_at')
                or event.get('observed_at')
            )

            if closed and closed.astimezone(CT).date() == today:
                total += (
                    event['managed_pnl']
                    if row['final']
                    else event['pnl']
                )

    return total


def paper_metrics(rows):
    """Compact, historical V4 paper results; never mix in shadow studies."""
    finals = [row for row in rows if row.get('final')]
    managed = sum(float(row['final'].get('managed_pnl') or 0) for row in finals)
    settlement = sum(float(row['final'].get('hold_pnl') or 0) for row in finals)
    wins = sum(float(row['final'].get('managed_pnl') or 0) > 0 for row in finals)
    pending = len(rows) - len(finals)
    st.markdown('##### 👑 V4 PAPER RESULTS')
    a, b, c, d = st.columns(4)
    a.metric('Managed net P&L', f'${managed:+.2f}')
    b.metric('Win rate', f'{wins / len(finals):.1%}' if finals else '—')
    c.metric('Graded / pending', f'{len(finals)} / {pending}')
    d.metric('Today net', f'${daily_pnl(rows):+.2f}')
    with st.expander('V4 result details · settlement comparison', expanded=False):
        x, y = st.columns(2)
        x.metric('Hold-to-settlement P&L', f'${settlement:+.2f}')
        y.metric('Total recorded entries', len(rows))
        st.caption('Only V4 entries included. Shadow and near-miss observations are separate. Estimated paper fills and fees, not real trades.')

def guidance_panel(market, verified, quote_fn, candle_fn, rows):
    """Paper-only, conservative guidance. No orders or automatic execution."""
    st.markdown('#### 👑 Trade guidance · paper only')

    ticker = str((market or {}).get('ticker') or '')

    if not ticker.startswith('KXBTC15M-'):
        st.info('WAIT · No active 15-minute BTC contract.')
        return

    try:
        df, feed_name, feed_errors, clock = _get_candles(candle_fn)
        live, quoted_at, spot, trade_at = fresh_snapshot(ticker)
        close = utc(live.get('close_time'))
        seconds = (close - now_utc()).total_seconds() if close else 0

        if live.get('status') != 'active' or seconds <= 0:
            st.info('WAIT · Contract inactive or closed.')
            return

        result = assess(
            live,
            live.get('floor_strike'),
            spot,
            df,
            seconds,
            bool(verified),
            quote_fn,
            False,
            feed_name,
        )

        if not snapshot_current(quoted_at, trade_at):
            st.warning('WAIT · Market data aged during analysis.')
            return

        trend = result.get('ema_trend')
        side = result.get('side')
        status = result.get('status')
        target = number(live.get('floor_strike'))
        distance = (
            spot - target if target is not None else None
        )

        open_rows = [
            r for r in rows
            if not r['final']
            and not r['exit']
            and r['entry']['ticker'] == ticker
        ]

        c1, c2, c3 = st.columns(3)
        c1.metric(
            'BTC vs target',
            f'${distance:+,.2f}'
            if distance is not None else '—',
        )
        c2.metric(
            'Time remaining',
            f'{max(0, int(seconds)) // 60}:'
            f'{max(0, int(seconds)) % 60:02d}',
        )
        c3.metric(
            'EMA trend',
            str(trend or 'UNCONFIRMED'),
        )
        if open_rows:
            for row in open_rows:
                entry = row['entry']
                estimate = exit_estimate(entry, live)

                if estimate is None:
                    st.warning(
                        'OPEN PAPER POSITION · Exit quote/size unavailable; '
                        'do not assume a fill.'
                    )
                    continue

                reason = close_reason(
                    entry, estimate, spot, trend, seconds
                )

                st.metric(
                    'Estimated exit P&L · paper',
                    f"${estimate['pnl']:+.2f}",
                )

                if estimate['pnl'] > 0:
                    st.success(
                        'TAKE PROFIT AVAILABLE · estimated positive net '
                        'at the observed bid after modeled exit costs. '
                        'Review limit sell; a fill is not guaranteed. '
                        + f"Estimated exit {estimate['price'] * 100:.0f}¢"
                    )
                elif reason:
                    st.warning(
                        'EXIT REVIEW · ' + reason
                        + ' · estimated exit '
                        + f"{estimate['price'] * 100:.0f}¢"
                    )
                else:
                    st.info(
                        'HOLD / MONITOR · No positive net exit available '
                        'at the observed bid. Prices can change quickly.'
                    )

        elif status == 'CANDIDATE' and side in ('ABOVE', 'BELOW'):
            st.success(
                ('BUY UP' if side == 'ABOVE' else 'BUY DOWN')
                + ' · PAPER CANDIDATE ONLY · Await entry filters '
                'and 10-second confirmation.'
            )
            st.caption(
                'Not an instruction to place a real order. '
                'Liquidity, fees and the current contract price '
                'must pass the paper entry checks.'
            )

        else:
            reasons = result.get('reasons') or []
            st.info(
                'WAIT / NO TRADE · '
                + (
                    '; '.join(map(str, reasons[:3]))
                    if reasons
                    else 'No confirmed entry setup.'
                )
            )

        if distance is not None and df is not None and len(df) >= 14:
            try:
                import pandas as pd

                prev = df['close'].shift(1)
                tr = pd.concat(
                    [
                        (df['high'] - df['low']).abs(),
                        (df['high'] - prev).abs(),
                        (df['low'] - prev).abs(),
                    ],
                    axis=1,
                ).max(axis=1)

                atr = float(tr.tail(14).mean())
                cushion = max(
                    50.0,
                    2 * atr * math.sqrt(max(seconds, 0) / 60),
                )

                if abs(distance) < cushion:
                    st.warning(
                        'FLIP RISK · BTC is inside the volatility-adjusted '
                        'target cushion. Treat either direction cautiously.'
                    )

            except (KeyError, ValueError, TypeError, OverflowError):
                pass

        with st.expander(
            'Signal diagnostics · paper only',
            expanded=False,
        ):
            st.write({
                'status': status,
                'side': side,
                'trend': trend,
                'candle_source': feed_name,
                'selected_candle_age_seconds': result.get('candle_age'),
                'feed_errors_other_sources': feed_errors,
                'reasons': result.get('reasons', []),
            })

    except Exception as exc:
        st.warning(
            'WAIT · Guidance unavailable; no signal issued.'
        )
        with st.expander(
            'Guidance diagnostics',
            expanded=False,
        ):
            st.code(str(exc))


def scan_status(kind, message, reasons=None):
    health = st.session_state.setdefault(
        'qe_v4_health',
        {
            'scans': 0,
            'counts': {},
            'reasons': {},
        },
    )

    health['scans'] += 1

    health['counts'][kind] = (
        health['counts'].get(kind, 0) + 1
    )

    for reason in reasons or []:
        health['reasons'][reason] = (
            health['reasons'].get(reason, 0) + 1
        )

    health.update(
        last=now_utc().isoformat(),
        message=message,
        kind=kind,
        latest_reasons=list(reasons or []),
    )

    if kind == 'ERROR':
        st.error(
            'Paper scanner paused · open diagnostics for details.'
        )
    elif kind == 'ENTRY':
        st.success(message)
    else:
        st.caption(message)

    with st.expander(
        'Paper scanner · health & blocking reasons',
        expanded=False,
    ):
        st.caption(
            'Session counters reset on reconnect. '
            'Saved entries and the ON/PAUSED setting do not.'
        )

        st.write(
            'Last scan CT:',
            now_utc().astimezone(CT).strftime('%m/%d %H:%M:%S'),
        )
        st.write(
            'Scans this session:',
            health['scans'],
        )
        st.write(
            'Scan results:',
            health['counts'],
        )
        st.write(
            'Last message:',
            health.get('message'),
        )

        st.dataframe(
            [
                {
                    'Blocking reason': reason,
                    'Scans': count,
                }
                for reason, count in sorted(
                    health['reasons'].items(),
                    key=lambda item: -item[1],
                )[:20]
            ],
            hide_index=True,
            use_container_width=True,
        )
def visible_scanner_status():
    """Small, always-visible explanation without another dropdown."""
    health = st.session_state.get('qe_v4_health') or {}
    kind = health.get('kind', 'WAIT')
    last = utc(health.get('last'))
    when = last.astimezone(CT).strftime('%I:%M:%S %p CT') if last else 'Not scanned yet'
    message = str(health.get('message') or 'Waiting for first paper scan.')
    reasons = health.get('latest_reasons') or []
    st.markdown('**🧪 PAPER SCANNER · ' + kind + '**')
    st.caption('Last check: ' + when + ' · ' + message)
    if reasons:
        st.caption('Why no trade? ' + '; '.join(map(str, reasons[:3])))
    elif kind == 'ENTRY':
        st.caption('Saved entry verified by a fresh journal read-back.')
    elif kind == 'ERROR':
        st.caption('Paper recording paused until the storage or data issue is resolved.')


def paper_history(rows):
    with st.expander(
        'V4 paper history · compare the same entries',
        expanded=False,
    ):
        display = []

        for row in rows:
            entry = row['entry']
            final = row['final']
            exit_event = row['exit']
            entry_shadow = row.get('shadow', {})

            display.append(
                {
                    'Observed CT': (
                        utc(entry['observed_at'])
                        .astimezone(CT)
                        .strftime('%m/%d %H:%M:%S')
                    ),
                    'Ticker': entry['ticker'],
                    'Side': entry['side'],
                    'Entry ¢': round(entry['price'] * 100, 1),
                    'Qty': entry['qty'],
                    'Entry risk $': entry['risk'],
                    'Fee estimate $': entry['fee'],
                    'Break-even': f"{entry['breakeven']:.1%}",
                    'Managed net $': (
                        final['managed_pnl']
                        if final
                        else exit_event['pnl']
                        if exit_event
                        else None
                    ),
                    'Hold net $': (
                        final['hold_pnl']
                        if final
                        else None
                    ),
                    'Official result': (
                        final['outcome']
                        if final
                        else 'PENDING'
                    ),
                    'Shadow 70¢ net $': (
                        entry_shadow.get(70) or {}
                    ).get('estimated_pnl'),
                    'Shadow 75¢ net $': (
                        entry_shadow.get(75) or {}
                    ).get('estimated_pnl'),
                    'Shadow 80¢ net $': (
                        entry_shadow.get(80) or {}
                    ).get('estimated_pnl'),
                    'Exit': (
                        final['exit_reason']
                        if final
                        else exit_event['reason']
                        if exit_event
                        else 'OPEN'
                    ),
                }
            )

        st.dataframe(
            display,
            hide_index=True,
            use_container_width=True,
        )

        st.caption(
            'Latest 200 entries shown. Shadow columns are hypothetical exits, '
            'not filled trades. Older events remain stored. '
            'Only forward observations are entered; chart arrows '
            'are never backfilled. Fills and fees are estimates. '
            'V4 is separate from Early, Locked, WATCH and V2 history.'
        )

def near_miss_observe(
    store, market, result, quote_at, trade_time, multiplier
):
    """Save one qualifying-price rejected setup per market/side, paper only."""

    if (
        result.get('status') != 'FILTERED'
        or not snapshot_current(quote_at, trade_time)
    ):
        return

    side = result.get('side')

    if (
        side not in ('ABOVE', 'BELOW')
        or result.get('candle_age') is None
    ):
        return

    if not -3 <= result['candle_age'] <= 90:
        return

    ask = quote(market, side, 'ask')
    bid = quote(market, side, 'bid')

    if (
        ask is None
        or bid is None
        or not (MIN_ASK <= ask <= MAX_ASK)
    ):
        return

    if not (
        0 < bid <= ask < 1
        and ask - bid <= 0.030001
    ):
        return

    price = cents_round(ask + SLIPPAGE)
    qty, estimated_fee, risk = size_for(price, multiplier)

    size = available(market, side, 'ask')

    if qty < 1 or size is None or size < qty:
        return

    ticker = str(market.get('ticker') or '')
    close = utc(market.get('close_time'))

    if not ticker.startswith('KXBTC15M-') or close is None:
        return

    key = (
        f'{int(close.timestamp() * 1000)}_'
        f'{ticker}_{side}.json'
    )

    event = {
        'ticker': ticker,
        'side': side,
        'observed_at': now_utc().isoformat(),
        'close_ts': close.timestamp(),
        'ask': ask,
        'price': price,
        'qty': qty,
        'risk': risk,
        'fee': estimated_fee,
        'target': number(market.get('floor_strike')),
        'distance': result.get('distance'),
        'rsi': result.get('rsi'),
        'ema_trend': result.get('ema_trend'),
        'candle_age': result.get('candle_age'),
        'reasons': list(result.get('reasons') or []),
        'note': (
            'Rejected observation, not a paper BUY or actual order'
        ),
    }

    store.once('near_misses', key, event)


def near_miss_report(store):
    """Grade rejected observations using official Kalshi settlements."""

    with st.expander(
        '👑 Near Miss Tracker · rejected setups',
        expanded=False,
    ):
        st.caption(
            'One price-eligible rejected observation per contract and side. '
            'Never counted as a paper BUY. '
            'Only official settlement grades results.'
        )
        items = store.request(
            'POST',
            '/object/list/' + BUCKET,
            {
                'prefix': 'near_misses',
                'limit': 100,
                'offset': 0,
                'sortBy': {
                    'column': 'name',
                    'order': 'desc',
                },
            },
        )

        if not isinstance(items, list):
            st.caption('Near-miss history unavailable.')
            return

        output = []
        checks = 0

        for item in items:
            key = str(item.get('name') or '')

            if not key.endswith('.json'):
                continue

            event = store.get('near_misses', key)

            if not isinstance(event, dict):
                continue

            outcome = store.get('near_miss_finals', key)

            if (
                not outcome
                and now_utc().timestamp()
                > float(event.get('close_ts') or 0) + 60
                and checks < 3
            ):
                checks += 1

                try:
                    settled = public_get(
                        '/markets/' + event['ticker']
                    )['market']

                    result = settled.get('result')

                    if (
                        settled.get('status')
                        in ('settled', 'finalized')
                        and result in ('yes', 'no')
                    ):
                        win = (
                            (event['side'] == 'ABOVE')
                            == (result == 'yes')
                        )

                        outcome = store.once(
                            'near_miss_finals',
                            key,
                            {
                                'result': result,
                                'win': win,
                                'hypothetical_pnl': round(
                                    (
                                        event['qty']
                                        if win else 0
                                    ) - event['risk'],
                                    6,
                                ),
                                'graded_at': now_utc().isoformat(),
                            },
                        )

                except (
                    requests.RequestException,
                    ValueError,
                    KeyError,
                    TypeError,
                ):
                    pass

            observed = utc(event.get('observed_at'))

            output.append(
                {
                    'Observed CT': (
                        observed.astimezone(CT).strftime(
                            '%m/%d %H:%M:%S'
                        )
                        if observed else '—'
                    ),
                    'Ticker': event.get('ticker'),
                    'Side': event.get('side'),
                    'Ask ¢': round(
                        float(event.get('ask') or 0) * 100,
                        1,
                    ),
                    'Why skipped': '; '.join(
                        event.get('reasons') or []
                    ),
                    'Outcome': (
                        'WIN' if outcome.get('win') else 'LOSS'
                    ) if outcome else 'PENDING',
                    'Hypothetical net $': (
                        outcome.get('hypothetical_pnl')
                        if outcome else None
                    ),
                }
            )

        if output:
            graded = [
                item for item in output
                if item['Outcome'] != 'PENDING'
            ]
            wins = sum(item['Outcome'] == 'WIN' for item in graded)
            losses = len(graded) - wins
            hypothetical_net = sum(
                float(item['Hypothetical net $'] or 0)
                for item in graded
            )
            st.markdown('##### 👑 Near Miss · Hypothetical Scoreboard')
            a, b, c = st.columns(3)
            a.metric('Observations', len(output))
            b.metric('Wins', wins)
            c.metric('Losses', losses)
            a, b, c = st.columns(3)
            a.metric('Win rate', f'{wins / len(graded):.1%}' if graded else '—')
            b.metric('Hypothetical P&L', f'${hypothetical_net:+.2f}')
            c.metric('Pending', len(output) - len(graded))
            st.caption(
                'Rejected setups only · official settlement grades · '
                'not actual fills or V4 paper P&L. '
                'Selected observations are not independent.'
            )
            with st.expander('Near Miss · observation details', expanded=False):
                st.dataframe(
                    output,
                    hide_index=True,
                    use_container_width=True,
                )

        else:
            st.caption(
                'No price-eligible rejected setups recorded yet.'
            )
# Independent rejected-setup study. Does not create V4 entries or exits.
SHADOW_MIN_ASK = 0.20
SHADOW_MAX_ASK = 0.85


def shadow_v1_observe(store, market, result, reasons, quote_at, trade_time, multiplier):
    """One forward observation per ticker/side; no backfills or orders."""
    if not snapshot_current(quote_at, trade_time):
        return
    if result.get('status') not in ('FILTERED', 'CANDIDATE'):
        return
    if result.get('candle_age') is None or not -3 <= result['candle_age'] <= 90:
        return
    ticker = str(market.get('ticker') or '')
    close = utc(market.get('close_time'))
    if not ticker.startswith('KXBTC15M-') or close is None:
        return
    seconds = (close - now_utc()).total_seconds()
    if not 180 <= seconds <= 600:
        return
    side = result.get('side')
    if side not in ('ABOVE', 'BELOW'):
        return
    ask = quote(market, side, 'ask')
    bid = quote(market, side, 'bid')
    if (ask is None or bid is None or
            not SHADOW_MIN_ASK <= ask <= SHADOW_MAX_ASK or
            not 0 < bid <= ask < 1 or ask - bid > 0.030001):
        return
    estimated_price = cents_round(ask + SLIPPAGE)
    qty, estimated_fee, risk = size_for(estimated_price, multiplier)
    depth = available(market, side, 'ask')
    if qty < 1 or depth is None or depth < qty:
        return
    # A candidate is shadowed only when V4's additional rules reject it.
    why = list(reasons or result.get('reasons') or [])
    if not why:
        return
    key = f'{int(close.timestamp() * 1000)}_{ticker}_{side}.json'
    event = {
        'version': 'shadow-v1', 'ticker': ticker, 'side': side,
        'observed_at': now_utc().isoformat(), 'close_ts': close.timestamp(),
        'ask': ask, 'bid': bid, 'estimated_price': estimated_price,
        'qty': qty, 'fee': estimated_fee, 'risk': risk,
        'target': number(market.get('floor_strike')),
        'distance': result.get('distance'), 'rsi': result.get('rsi'),
        'ema_trend': result.get('ema_trend'), 'reasons': why,
        'note': 'Rejected forward observation only; not a V4 paper entry or order',
    }
    store.once('shadow_v1_rejections', key, event)


def shadow_v1_report(store):
    """Read/grade separate rejected setups from official settlement only."""
    with st.expander('👑 Shadow Tracker V1 · missed opportunity study', expanded=False):
        st.caption(
            'Forward-only rejected observations at 20–85¢ ask, ≤3¢ spread, '
            'with fresh data and displayed depth. One observation per contract '
            'and side; not trades. Does not affect V4 paper P&L.'
        )
        items = store.request('POST', '/object/list/' + BUCKET, {
            'prefix': 'shadow_v1_rejections', 'limit': 200, 'offset': 0,
            'sortBy': {'column': 'name', 'order': 'desc'},
        })
        if not isinstance(items, list):
            st.caption('Shadow history unavailable.')
            return
        output = []
        checks = 0
        for item in items:
            key = str(item.get('name') or '')
            if not key.endswith('.json'):
                continue
            event = store.get('shadow_v1_rejections', key)
            if not isinstance(event, dict):
                continue
            final = store.get('shadow_v1_finals', key)
            if (not final and checks < 3 and
                    now_utc().timestamp() > float(event.get('close_ts') or 0) + 60):
                checks += 1
                try:
                    market = public_get('/markets/' + event['ticker'])['market']
                    outcome = market.get('result')
                    if (market.get('status') in ('settled', 'finalized') and
                            outcome in ('yes', 'no')):
                        won = ((event['side'] == 'ABOVE') == (outcome == 'yes'))
                        final = store.once('shadow_v1_finals', key, {
                            'result': outcome, 'win': won,
                            'hypothetical_pnl': round(
                                (event['qty'] if won else 0) - event['risk'], 6
                            ),
                            'graded_at': now_utc().isoformat(),
                        })
                except (requests.RequestException, ValueError, KeyError, TypeError):
                    pass
            observed = utc(event.get('observed_at'))
            output.append({
                'Observed CT': observed.astimezone(CT).strftime('%m/%d %H:%M:%S') if observed else '—',
                'Ticker': event.get('ticker'), 'Side': event.get('side'),
                'Ask ¢': round(float(event.get('ask') or 0) * 100, 1),
                'Contracts': int(event.get('qty') or 0),
                'Modeled risk $': round(float(event.get('risk') or 0), 2),
                'Why V4 skipped': '; '.join(map(str, event.get('reasons') or [])),
                'Official grade': ('WIN' if final.get('win') else 'LOSS') if final else 'PENDING',
                'Hypothetical net $': final.get('hypothetical_pnl') if final else None,
            })
        if not output:
            st.caption('No qualifying rejected observations recorded yet. Keep the dashboard open to collect forward data.')
            return
        graded = [row for row in output if row['Official grade'] != 'PENDING']
        wins = sum(row['Official grade'] == 'WIN' for row in graded)
        losses = len(graded) - wins
        total_net = sum(float(row['Hypothetical net $'] or 0) for row in graded)
        settled_risk = sum(float(row['Modeled risk $']) for row in graded)
        pending_risk = sum(
            float(row['Modeled risk $']) for row in output
            if row['Official grade'] == 'PENDING'
        )
        total_risk = settled_risk + pending_risk
        roi = total_net / settled_risk if settled_risk else None

        st.markdown('##### 👑 Shadow V1 · Hypothetical Scoreboard')
        a, b, c = st.columns(3)
        a.metric('Observations', len(output))
        b.metric('Wins', wins)
        c.metric('Losses', losses)
        a, b, c = st.columns(3)
        a.metric('Win rate', f'{wins / len(graded):.1%}' if graded else '—')
        b.metric('Hypothetical P&L', f'${total_net:+.2f}')
        c.metric('Risk on graded', f'${settled_risk:.2f}')
        a, b, c = st.columns(3)
        a.metric('Return on graded risk', f'{roi:+.1%}' if roi is not None else '—')
        b.metric('Pending risk', f'${pending_risk:.2f}')
        c.metric('All modeled risk', f'${total_risk:.2f}')
        st.caption(
            f'{len(graded)} officially graded · {len(output) - len(graded)} pending. '
            'P&L and return use graded observations only. '
            'Risk includes estimated entry fees; hypothetical, not actual fills or V4 P&L.'
        )
        st.markdown('**Per-observation results**')
        st.dataframe(
            [{
                'Time CT': row['Observed CT'],
                'Side': row['Side'],
                'Risk $': row['Modeled risk $'],
                'Result': row['Official grade'],
                'P&L $': row['Hypothetical net $'],
            } for row in output],
            hide_index=True,
            use_container_width=True,
        )
        with st.expander('Shadow V1 · full observation details', expanded=False):
            st.dataframe(output, hide_index=True, use_container_width=True)

def evaluate_papers(
    store,
    rows,
    market,
    verified,
    quote_fn,
    candle_fn,
    enabled,
):
    ticker = str((market or {}).get('ticker', ''))

    if not ticker.startswith('KXBTC15M-'):
        raise RuntimeError(
            'No Bitcoin 15-minute ticker; waiting for a current market'
        )

    pending = [
        row
        for row in rows
        if not row['final']
        and not row['exit']
        and row['entry']['ticker'] == ticker
    ]

    expected_close = utc(market.get('close_time'))

    remaining = (
        (expected_close - now_utc()).total_seconds()
        if expected_close
        else None
    )

    if not enabled and not pending:
        st.session_state.pop('qe_v4_confirm', None)

        return (
            'PAUSED',
            'New paper entries paused. Saved history is preserved.',
            [],
        )

    if (
        not pending
        and remaining is not None
        and not 180 <= remaining <= 600
    ):
        st.session_state.pop('qe_v4_confirm', None)

        return (
            'WINDOW',
            'WAIT · Outside entry window: this test checks '
            'from 10 to 3 minutes remaining.',
            ['Outside 10–3 minute entry window'],
        )

    # Complete slower work before requesting fresh quotes.
    df, feed_name, feed_errors, clock = _get_candles(candle_fn)

    multiplier = (
        series_fee()
        if enabled and not pending
        else None
    )

    fresh_market, quote_at, live_spot, trade_time = (
        fresh_snapshot(ticker)
    )

    close = utc(fresh_market.get('close_time'))

    if close is None or fresh_market.get('ticker') != ticker:
        raise RuntimeError(
            'Current contract identity or close time unavailable'
        )

    seconds = (close - now_utc()).total_seconds()

    if fresh_market.get('status') != 'active' or seconds <= 0:
        st.session_state.pop('qe_v4_confirm', None)

        return (
            'WINDOW',
            'WAIT · Contract closed or inactive. '
            'Official settlement remains pending.',
            [],
        )

    result = assess(
        fresh_market,
        fresh_market.get('floor_strike'),
        live_spot,
        df,
        seconds,
        bool(verified),
        quote_fn,
        False,
        feed_name,
    )

    if not snapshot_current(quote_at, trade_time):
        raise RuntimeError(
            'Quote or BTC trade aged during evaluation; '
            'waiting for a new snapshot'
        )

    # Settlement-only strategy: never create managed early exits.
    # Shadow take-profit observations are separate, immutable and do not
    # change the actual paper position or settlement result.
    for row in pending:
        entry = row['entry']
        bid = quote(fresh_market, entry['side'], 'bid')
        depth = available(fresh_market, entry['side'], 'bid')

        if bid is None or depth is None or depth < entry['qty']:
            continue

        if not snapshot_current(quote_at, trade_time):
            raise RuntimeError(
                'Shadow quote aged before observation'
            )

        shadow = row.setdefault('shadow', {})

        for threshold in SHADOW_TARGETS:
            key = int(threshold * 100)

            if shadow.get(key) is not None or bid < threshold:
                continue

            estimated_price = max(
                0.0,
                cents_round(bid - SLIPPAGE, False),
            )

            estimated_fee = fee(
                entry['qty'],
                estimated_price,
                entry['fee_multiplier'],
            )

            event = {
                'target_cents': key,
                'observed_bid': bid,
                'estimated_exit_price': estimated_price,
                'estimated_exit_fee': estimated_fee,
                'estimated_pnl': round(
                    entry['qty'] * estimated_price
                    - estimated_fee
                    - entry['risk'],
                    6,
                ),
                'observed_at': now_utc().isoformat(),
                'note': (
                    'Observed bid/depth; simulated exit only, '
                    'not an actual fill'
                ),
            }

            event_key = (
                row['key'][:-5] + f'_{key}.json'
            )

            shadow[key] = store.once(
                'shadow',
                event_key,
                event,
            )

    file_key = (
        str(int(close.timestamp() * 1000))
        + '_'
        + ticker
        + '.json'
    )

    if any(row['key'] == file_key for row in rows):
        return (
            'MONITOR',
            'This market already has its one V4 paper entry. '
            'Monitoring / awaiting official settlement.',
            [],
        )

    if not enabled:
        st.session_state.pop('qe_v4_confirm', None)

        return (
            'PAUSED',
            'New paper entries paused; existing positions monitored.',
            [],
        )

    if not 180 <= seconds <= 600:
        st.session_state.pop('qe_v4_confirm', None)

        return (
            'WINDOW',
            'WAIT · Outside the 10–3 minute paper entry window.',
            ['Outside 10–3 minute entry window'],
        )
    if daily_pnl(rows) <= -DAILY_STOP:
        return (
            'LIMIT',
            'Daily paper loss review limit reached (-$15). '
            'Waiting until the next Central date.',
            ['Daily paper loss limit'],
        )

    if any(
        not row['final']
        and row['entry']['close_ts'] > now_utc().timestamp()
        for row in rows
    ):
        return (
            'MONITOR',
            'Another paper window is still open. Waiting.',
            [],
        )

    proposed, reasons = candidate(
        fresh_market,
        result,
        df,
        live_spot,
        multiplier,
    )

    # Shadow observations must never interrupt the existing V4 scanner.
    if not proposed and enabled and df is not None:
        try:
            shadow_v1_observe(
                store, fresh_market, result, reasons,
                quote_at, trade_time, multiplier,
            )
        except Exception:
            pass

    if not proposed:
        st.session_state.pop('qe_v4_confirm', None)

        if df is None:
            reasons = reasons + [
                'No fresh candle source; open Original V3 '
                'diagnostics for feed details'
            ]

        kind = (
            'DATA'
            if result.get('status') == 'DATA ISSUE' or df is None
            else 'FILTERED'
        )

        if enabled and df is not None and result.get('status') == 'FILTERED':
            near_miss_observe(
                store,
                fresh_market,
                result,
                quote_at,
                trade_time,
                multiplier,
            )

        return kind, 'WAIT · ' + '; '.join(reasons), reasons

    stamp = now_utc().timestamp()
    old = st.session_state.get('qe_v4_confirm')

    same = (
        old
        and old['ticker'] == ticker
        and old['side'] == proposed['side']
    )

    restart = (
        not same
        or stamp - old['time'] > 45
        or proposed['distance'] < old['distance'] * 0.75
        or proposed['price'] > old['price'] + 0.03
    )

    if restart:
        st.session_state['qe_v4_confirm'] = {
            **proposed,
            'ticker': ticker,
            'time': stamp,
        }

        return (
            'CONFIRMING',
            'WAIT · First eligible quote. Need another eligible '
            'observation at least 10 seconds later.',
            [],
        )

    if stamp - old['time'] < 10:
        return (
            'CONFIRMING',
            'WAIT · Candidate has not held for 10 seconds.',
            [],
        )

    if (
        not snapshot_current(quote_at, trade_time)
        or not 180 <= (close - now_utc()).total_seconds() <= 600
    ):
        raise RuntimeError(
            'Snapshot or entry window expired before recording'
        )

    entry = {
        **proposed,
        'ticker': ticker,
        'target': float(fresh_market['floor_strike']),
        'close_ts': close.timestamp(),
        'observed_at': now_utc().isoformat(),
        'trade_time': trade_time.isoformat(),
        'fee_multiplier': multiplier,
        'fee_model': (
            'Estimated quadratic taker, whole-cent assumed fills'
        ),
        'strategy': 'V4-settlement-45-65-shadow-v2',
        'take_profit_fraction': TAKE_PROFIT,
        'loss_review_fraction': STOP_REVIEW,
        'entry_cap': ENTRY_CAP,
        'preferred_ask_min': MIN_ASK,
        'preferred_ask_max': MAX_ASK,
        'exit_policy': 'hold_until_official_settlement',
        'slippage': SLIPPAGE,
    }

    saved = store.once('entries', file_key, entry)
    # A second uncached remote read confirms the entry before it is counted.
    saved = store.verify_saved('entries', file_key, saved)

    rows.insert(
        0,
        {
            'key': file_key,
            'entry': saved,
            'exit': None,
            'final': None,
            'shadow': {},
        },
    )

    st.session_state.pop('qe_v4_confirm', None)

    return (
        'ENTRY',
        f"PAPER ENTRY SAVED · {saved['side']} · "
        f"{saved['qty']} contracts at {saved['price']*100:.0f}¢ "
        f"· risk ${saved['risk']:.2f}. No order was sent.",
        [],
    )
# Independent high-price paper experiment. V4 and historical journals stay separate.
HIGH_MIN_ASK = 0.70
HIGH_MAX_ASK = 0.90
HIGH_TP_MIN_NET = 0.01  # Net positive after modeled fees/slippage, not gross.

def high_price_rows(store):
    items = store.request('POST', '/object/list/' + BUCKET, {
        'prefix': 'high_price_entries', 'limit': 200, 'offset': 0,
        'sortBy': {'column': 'name', 'order': 'desc'},
    })
    if not isinstance(items, list):
        raise RuntimeError('High-price paper journal listing unavailable')
    output = []
    for item in items:
        key = str(item.get('name') or '')
        if not key.endswith('.json'):
            continue
        entry = store.get('high_price_entries', key)
        if not isinstance(entry, dict):
            raise RuntimeError('High-price entry record could not be read')
        output.append({
            'key': key, 'entry': entry,
            'exit': store.get('high_price_exits', key),
            'final': store.get('high_price_finals', key),
        })
    return output


def high_price_scan(store, market, verified, quote_fn, candle_fn, v4_rows):
    """Forward-only, independent hypothetical test; no order placement."""
    ticker = str((market or {}).get('ticker') or '')
    if not ticker.startswith('KXBTC15M-'):
        return 'WAIT · No active BTC 15-minute market'
    records = high_price_rows(store)
    active = next((r for r in records if r['entry'].get('ticker') == ticker), None)
    # Settle older hypothetical positions. Never overwrite immutable exit events.
    checks = 0
    for row in records:
        if row['final'] or now_utc().timestamp() < float(row['entry']['close_ts']) + 60 or checks >= 3:
            continue
        checks += 1
        try:
            settled = public_get('/markets/' + row['entry']['ticker'])['market']
            outcome = settled.get('result')
            if settled.get('status') not in ('settled', 'finalized') or outcome not in ('yes', 'no'):
                continue
            entry = row['entry']
            won = (entry['side'] == 'ABOVE') == (outcome == 'yes')
            hold_net = round((entry['qty'] if won else 0) - entry['risk'], 6)
            final = {
                'outcome': outcome, 'hold_net': hold_net,
                'modeled_net': row['exit']['pnl'] if row['exit'] else hold_net,
                'exit_mode': 'MODELED_EARLY_EXIT' if row['exit'] else 'SETTLEMENT',
                'graded_at': now_utc().isoformat(),
            }
            row['final'] = store.once('high_price_finals', row['key'], final)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            continue

    # Only current market requires live market data. Check saved open entry first.
    if active and (active['exit'] or active['final']):
        return 'MONITOR · This contract already has a high-price paper entry'
    close = utc((market or {}).get('close_time'))
    if close is None or (close - now_utc()).total_seconds() <= 0:
        return 'WAIT · Contract ended or close time unavailable'
    if not verified:
        return 'WAIT · Direction not verified'
    df, feed_name, _, _ = _get_candles(candle_fn)
    fresh, quote_at, spot, trade_at = fresh_snapshot(ticker)
    if fresh.get('status') != 'active' or not snapshot_current(quote_at, trade_at):
        return 'WAIT · Fresh market data unavailable'
    seconds = (utc(fresh['close_time']) - now_utc()).total_seconds()
    if seconds <= 0:
        return 'WAIT · Market closed'
    if active:
        entry = active['entry']
        estimate = exit_estimate(entry, fresh)
        if estimate is None:
            return 'OPEN · No verified executable-depth exit estimate'
        if estimate['pnl'] >= HIGH_TP_MIN_NET and snapshot_current(quote_at, trade_at):
            event = {
                **estimate, 'observed_bid': quote(fresh, entry['side'], 'bid'),
                'observed_at': now_utc().isoformat(),
                'reason': 'First observed positive modeled net exit',
                'note': 'Hypothetical exit at bid minus slippage, not a guaranteed fill',
            }
            store.once('high_price_exits', active['key'], event)
            return f"MODELED TAKE PROFIT · {entry['side']} · ${estimate['pnl']:+.2f} net"
        return f"OPEN · {entry['side']} · estimated exit ${estimate['pnl']:+.2f} net"

    # Preserve V4's contract-wide entry lock; shadow study can observe the
    # same market but cannot be counted as a second V4 position.
    # Observe throughout the active contract; V4 retains its own window.
    if df is None or len(df) < 60:
        return 'WAIT · Insufficient 1-minute candle history'
    result = assess(fresh, fresh.get('floor_strike'), spot, df, seconds,
                    bool(verified), quote_fn, False, feed_name)
    side = result.get('side')
    if side not in ('ABOVE', 'BELOW') or result.get('status') not in ('CANDIDATE', 'FILTERED'):
        return 'WAIT · No eligible directional observation'
    age = number(result.get('candle_age'))
    if age is None or not -3 <= age <= 90:
        return 'WAIT · Candle is stale'
    trend = str(result.get('ema_trend') or '').upper()
    if (side == 'ABOVE' and trend != 'UPWARD') or (side == 'BELOW' and trend != 'DOWNWARD'):
        return 'WAIT · EMA trend not aligned'
    ask, bid = quote(fresh, side, 'ask'), quote(fresh, side, 'bid')
    if ask is None or bid is None or not (HIGH_MIN_ASK <= ask <= HIGH_MAX_ASK):
        return 'WAIT · High-price ask outside 70–90¢'
    if not 0 < bid <= ask < 1 or ask - bid > 0.030001:
        return 'WAIT · Spread exceeds 3¢ or quotes invalid'
    multiplier = series_fee()
    price = cents_round(ask + SLIPPAGE)
    if price >= 1:
        return 'WAIT · Estimated entry fill invalid'
    qty, entry_fee, risk = size_for(price, multiplier)
    if qty < 1 or available(fresh, side, 'ask') is None or available(fresh, side, 'ask') < qty:
        return 'WAIT · Insufficient displayed entry depth'
    stamp = now_utc().timestamp()
    old = st.session_state.get('qe_high_confirm')
    if (not old or old.get('ticker') != ticker or old.get('side') != side
            or stamp - old['time'] > 45 or price > old['price'] + 0.03):
        st.session_state['qe_high_confirm'] = {
            'ticker': ticker, 'side': side, 'time': stamp, 'price': price,
        }
        return 'CONFIRMING · Waiting for second eligible observation'
    if stamp - old['time'] < 10:
        return 'CONFIRMING · Need at least 10 seconds'
    if not snapshot_current(quote_at, trade_at):
        return 'WAIT · Quotes aged before recording'
    key = f'{int(utc(fresh["close_time"]).timestamp()*1000)}_{ticker}.json'
    entry = {
        'strategy': 'high-price-70-90-early-net-v1', 'ticker': ticker,
        'side': side, 'ask': ask, 'price': price, 'qty': qty,
        'fee': entry_fee, 'risk': risk, 'fee_multiplier': multiplier,
        'slippage': SLIPPAGE, 'close_ts': utc(fresh['close_time']).timestamp(),
        'target': number(fresh.get('floor_strike')),
        'observed_at': now_utc().isoformat(), 'ema_trend': trend,
        'exit_policy': 'first_observed_positive_modeled_net',
        'note': 'Paper observation only; no actual orders or guaranteed fills',
    }
    store.once('high_price_entries', key, entry)
    st.session_state.pop('qe_high_confirm', None)
    return f'PAPER ENTRY · {side} · {qty} at {price*100:.0f}¢ · ${risk:.2f} modeled risk'


def high_price_report(store, message):
    rows = high_price_rows(store)
    completed = [r for r in rows if r['final'] or r['exit']]
    pending = len(rows) - len(completed)
    net = sum(float((r['exit'] or r['final']).get('pnl') if r['exit'] else
                    r['final'].get('modeled_net') or 0) for r in completed)
    wins = sum((r['exit']['pnl'] if r['exit'] else r['final']['modeled_net']) > 0
               for r in completed)
    st.markdown('##### 👑 HIGH-PRICE · EARLY EXIT PAPER TEST')
    a, b, c, d = st.columns(4)
    a.metric('Modeled net P&L', f'${net:+.2f}')
    b.metric('Win rate', f'{wins/len(completed):.1%}' if completed else '—')
    c.metric('Closed / open', f'{len(completed)} / {pending}')
    d.metric('Observations', len(rows))
    st.caption(message + ' · 70–90¢ entry ask; up to $5 modeled risk. '
               'Checks throughout active contracts while the app is running. '
               'Exit is simulated at observed bid minus 1¢ and estimated fees. '
               'Not guaranteed executable fills; never real orders.')
    with st.expander('High-price paper history', expanded=False):
        display = []
        for row in rows:
            entry, exit_event, final = row['entry'], row['exit'], row['final']
            display.append({
                'Time CT': (utc(entry.get('observed_at')).astimezone(CT)
                            .strftime('%m/%d %H:%M:%S')),
                'Ticker': entry['ticker'], 'Side': entry['side'],
                'Entry ¢': round(entry['price'] * 100, 1),
                'Qty': entry['qty'], 'Risk $': entry['risk'],
                'Exit ¢': round(exit_event['price']*100, 1) if exit_event else None,
                'Modeled net $': (exit_event['pnl'] if exit_event else
                                  final['modeled_net'] if final else None),
                'Status': ('MODELED EARLY EXIT' if exit_event else
                           'SETTLED' if final else 'OPEN'),
            })
        if display:
            st.dataframe(display, hide_index=True, use_container_width=True)
        else:
            st.caption('No high-price observations yet. Leave dashboard open to scan.')


def _run_paper_engine(
    market,
    strike,
    spot,
    seconds_left,
    verified,
    quote_fn,
    candle_fn,
    quote_stale=False,
):
    st.markdown(
        '#### 👑 Forward Paper Test · Entry & Exit Comparison'
    )

    st.caption(
        'Settlement test: preferred ask 45–65¢; $5 maximum including fees; '
        'spread ≤3¢; '
        '1¢ adverse slippage; two eligible observations at least '
        '10 seconds apart. Hold until settlement; shadow 70/75/80¢ exits. '
        'Profitability is unproven.'
    )

    rows = None

    try:
        url = st.secrets.get('SUPABASE_URL')
        key = st.secrets.get('SUPABASE_SERVICE_ROLE_KEY')

        if not url or not key:
            raise RuntimeError(
                'Existing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY '
                'unavailable; paper recording disabled'
            )

        store = journal(str(url), str(key))
        store.ensure(create=True)
        enabled = paper_enabled(store)
        if st.button('Verify paper journal · no trade', key='qe_v4_journal_test'):
            st.success(journal_diagnostic(store))

        rows = store.rows()
        grade_papers(store, rows)
        royal_live_status(store, market, candle_fn, enabled)

        kind, message, reasons = evaluate_papers(
            store,
            rows,
            market,
            verified,
            quote_fn,
            candle_fn,
            enabled,
        )

        paper_metrics(rows)
        # Independent forward-only high-price test; its failures never block V4.
        try:
            high_message = high_price_scan(
                store, market, verified, quote_fn, candle_fn, rows,
            ) if enabled else 'PAUSED · Enable V4 paper switch to collect new high-price observations'
            high_price_report(store, high_message)
        except Exception as high_exc:
            st.warning('High-price paper experiment paused; V4 is unaffected.')
            with st.expander('High-price diagnostics', expanded=False):
                st.code(str(high_exc))
        scan_status(kind, message, reasons)

    except Exception as exc:
        st.session_state.pop('qe_v4_confirm', None)

        if rows is not None:
            paper_metrics(rows)

        message = 'PAPER DATA / STORAGE ISSUE · ' + str(exc)

        st.caption(
            'Scan paused. Existing saved history is not deleted; '
            'verify interrupted saves before retrying.'
        )

        scan_status('ERROR', message, [message])

    if rows is not None:
        if not rows:
            st.caption(
                'No V4 entries recorded yet. Scan counts show activity; '
                'P&L starts after a qualifying entry and official settlement.'
            )

        paper_history(rows)

        try:
            near_miss_report(store)
        except Exception:
            st.caption(
                'Near-miss reporting unavailable; '
                'existing paper scanner unchanged.'
            )
        try:
            shadow_v1_report(store)
        except Exception:
            st.caption('Shadow Tracker reporting unavailable; V4 scanner unchanged.')

    st.caption(
        'V4 paper results currently use the existing settlement/managed-exit model. '
        'Shadow take-profit observations at 70/75/80¢ use observed bids '
        'and displayed depth, with estimated slippage and fees; they are '
        'not guaranteed fills. Shadow tracking runs only while this '
        'dashboard is open. Missed price touches are not backfilled.'
    )


@st.fragment(run_every="15s")
def render_paper_engine(*args, **kwargs):
    # Guidance stays visible above the preserved live chart.
    # Read-only journal access; any storage problem fails closed.
    rows = []

    try:
        url = st.secrets.get('SUPABASE_URL')
        key = st.secrets.get('SUPABASE_SERVICE_ROLE_KEY')

        if url and key:
            rows = journal(
                str(url),
                str(key),
            ).rows()

    except Exception:
        pass

    guidance_panel(
        args[0] if len(args) > 0 else kwargs.get('market'),
        args[4] if len(args) > 4 else kwargs.get('verified', False),
        args[5] if len(args) > 5 else kwargs.get('quote_fn'),
        args[6] if len(args) > 6 else kwargs.get('candle_fn'),
        rows,
    )

    visible_scanner_status()
    with st.expander(
        '🧪 Paper test · controls, P&L & history',
        expanded=False,
    ):
        _run_paper_engine(*args, **kwargs)


def compact_chart_html(html):
    """Reset old Advanced Mode preferences on startup."""
    html = html.replace(
        '<div class="panel">',
        '<div class="panel mainPanel">',
        1,
    )

    html = re.sub(
        r'(<div id="help">.*?</div>)',
        r'<details class="qe-notes">'
        r'<summary>Chart notes · paper only</summary>\1</details>',
        html,
        count=1,
        flags=re.S,
    )

    css = """
<style>
html,body{
 height:100%;
 min-height:0;
}
body{
 display:flex;
 flex-direction:column;
 overflow-y:auto;
}
#summary,#toolbar,#extras,#legend,#notice,.qe-notes{
 flex-shrink:0;
}
.mainPanel{
 flex:1 0 260px;
 min-height:260px;
}
#main{
 height:100%!important;
 min-height:258px;
}
.optionalPanel{
 flex-shrink:0;
}
#toolbar{
 position:sticky;
 top:0;
 z-index:20;
 background:#0b091b;
 padding:4px 0;
}
.qe-notes{
 font-size:10px;
 color:#bda9d5;
 padding:5px 2px;
}
.qe-notes summary{
 cursor:pointer;
 color:#bda9d5;
}
</style>
"""

    html = html.replace('</head>', css + '</head>', 1)

    reset = """
advancedMode=false;
for(const key of Object.keys(flags)){
 flags[key]=(key==='markers');
}
try{
 sessionStorage.removeItem(settingsKey);
}catch(e){}
window.scrollTo(0,0);
start();
requestResize();
"""

    html, count = re.subn(
        r'\bstart\(\);\s*requestResize\(\);',
        lambda match: reset,
        html,
        count=1,
    )

    if count != 1:
        raise RuntimeError(
            'Chart compatibility check failed: clean-mode startup'
        )

    return html


def clean_chart_renderer(path):
    """Apply compact defaults without overwriting the chart module."""
    tree = ast.parse(
        path.read_text(encoding='utf-8'),
        filename=str(path),
    )

    class CleanFrame(ast.NodeTransformer):
        count = 0

        def visit_Call(self, node):
            self.generic_visit(node)

            is_chart_frame = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == 'html'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'components'
            )

            if is_chart_frame:
                if not node.args:
                    raise RuntimeError(
                        'Chart compatibility check failed: HTML argument'
                    )

                node.args[0] = ast.Call(
                    func=ast.Name(
                        id='compact_chart_html',
                        ctx=ast.Load(),
                    ),
                    args=[node.args[0]],
                    keywords=[],
                )

                for keyword in node.keywords:
                    if keyword.arg == 'height':
                        keyword.value = ast.Constant(value=620)
                    elif keyword.arg == 'scrolling':
                        keyword.value = ast.Constant(value=True)

                self.count += 1

            return node

    transform = CleanFrame()
    tree = transform.visit(tree)

    if transform.count != 1:
        raise RuntimeError(
            'Chart compatibility check failed: expected one chart frame'
        )

    namespace = {
        '__name__': 'queens_edge_compact_chart',
        '__file__': str(path),
        'compact_chart_html': compact_chart_html,
    }

    exec(
        compile(
            ast.fix_missing_locations(tree),
            str(path),
            'exec',
        ),
        namespace,
    )

    return namespace['render_live_chart']


def expander_node(nodes, label):
    wrapper = ast.With(
        items=[
            ast.withitem(
                context_expr=ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id='st', ctx=ast.Load()),
                        attr='expander',
                        ctx=ast.Load(),
                    ),
                    args=[ast.Constant(value=label)],
                    keywords=[
                        ast.keyword(
                            arg='expanded',
                            value=ast.Constant(value=False),
                        )
                    ],
                ),
                optional_vars=None,
            )
        ],
        body=nodes,
        type_comment=None,
    )

    ast.copy_location(wrapper, nodes[0])
    wrapper.end_lineno = nodes[-1].end_lineno
    wrapper.end_col_offset = nodes[-1].end_col_offset
    return wrapper


def compact_original(source):
    """Collapse display sections while preserving their execution."""
    tree = ast.parse(source)

    grid = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == 'live_grid'
        ),
        None,
    )

    if grid is None:
        raise RuntimeError(
            'Layout compatibility check failed: live_grid'
        )

    start = None
    end = None

    for index, node in enumerate(grid.body):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
        ):
            args = node.value.args

            if (
                args
                and isinstance(args[0], ast.Constant)
                and 'Paper auto-trader' in str(args[0].value)
            ):
                start = index

        if start is not None and isinstance(node, ast.With):
            existing_signals = any(
                isinstance(item.context_expr, ast.Call)
                and item.context_expr.args
                and isinstance(
                    item.context_expr.args[0],
                    ast.Constant,
                )
                and 'Existing Bitcoin signals' in str(
                    item.context_expr.args[0].value
                )
                for item in node.items
            )

            if existing_signals:
                end = index
                break

    if start is None or end is None:
        raise RuntimeError(
            'Layout compatibility check failed: safety-console boundaries'
        )

    grid.body[start:end] = [
        expander_node(
            grid.body[start:end],
            '🛡️ Safety console · paper only',
        )
    ]

    tab_start = None
    tab_end = None

    for index, node in enumerate(tree.body):
        if isinstance(node, ast.Assign):
            names = {
                child.id
                for target in node.targets
                for child in ast.walk(target)
                if isinstance(child, ast.Name)
            }

            if {
                'overview_tab',
                'history_tab',
                'diagnostics_tab',
            }.issubset(names):
                tab_start = index

        if tab_start is not None and isinstance(node, ast.With):
            last_tab = any(
                isinstance(item.context_expr, ast.Name)
                and item.context_expr.id == 'diagnostics_tab'
                for item in node.items
            )

            if last_tab:
                tab_end = index + 1
                break

    if tab_start is None or tab_end is None:
        raise RuntimeError(
            'Layout compatibility check failed: '
            'history/performance boundaries'
        )

    tree.body[tab_start:tab_end] = [
        expander_node(
            tree.body[tab_start:tab_end],
            '📚 Performance, history & diagnostics',
        )
    ]

    return ast.fix_missing_locations(tree)


def qe_simple_status(market, errors, quote_fn, candle_fn, verified):
    """Conservative summary; no new strategy or order logic."""
    ticker = str((market or {}).get('ticker') or '')
    if not ticker.startswith('KXBTC15M-') or errors or not verified:
        return 'UNCERTAIN', 'WAIT', 'Market unavailable or quote feed stale', None
    try:
        df, source, feed_errors, _ = _get_candles(candle_fn)
        live, quote_at, spot, trade_at = fresh_snapshot(ticker)
        close = utc(live.get('close_time'))
        seconds = (close - now_utc()).total_seconds() if close else -1
        if seconds <= 0 or live.get('status') != 'active':
            return 'UNCERTAIN', 'WAIT', 'No active settlement window', None
        result = assess(live, live.get('floor_strike'), spot, df,
                        seconds, bool(verified), quote_fn, False, source)
        trend = str(result.get('ema_trend') or 'UNCERTAIN')
        if trend not in ('UPWARD', 'DOWNWARD'):
            trend = 'UNCERTAIN'
        age = result.get('candle_age')
        if (not snapshot_current(quote_at, trade_at) or age is None
                or not -3 <= age <= 90 or df is None):
            return 'UNCERTAIN', 'WAIT', 'Candle or quote data needs refreshing', live
        if result.get('status') == 'CANDIDATE':
            side = result.get('side')
            if side in ('ABOVE', 'BELOW'):
                return trend, 'WAIT', (side + ' setup detected; paper entry risk checks pending'), live
        reasons = result.get('reasons') or []
        return trend, 'WAIT', str(reasons[0]) if reasons else 'Awaiting confirmation', live
    except Exception:
        return 'UNCERTAIN', 'WAIT', 'Live verification unavailable', None


def qe_stage_row(market, errors, verified, early, locked):
    """Compact, color-coded view of existing paper calls; MID is provisional."""
    from html import escape
    import time as _time

    ticker = str((market or {}).get('ticker') or '')
    stamp = st.session_state.get('last_good_times', {}).get('KXBTC15M')
    fresh = bool(market and not errors and stamp is not None and
                 0 <= _time.time() - stamp <= 15)
    yes_bid = quote(market, 'ABOVE', 'bid') if market else None
    yes_ask = quote(market, 'ABOVE', 'ask') if market else None
    no_bid = quote(market, 'BELOW', 'bid') if market else None
    no_ask = quote(market, 'BELOW', 'ask') if market else None
    quotes_ok = (fresh and verified and all(
        v is not None and 0 <= v <= 1
        for v in (yes_bid, yes_ask, no_bid, no_ask))
        and yes_bid <= yes_ask and no_bid <= no_ask)
    mid = (yes_bid + yes_ask) / 2 if quotes_ok else None
    close = utc((market or {}).get('close_time'))
    remaining = (close - now_utc()).total_seconds() if close else None

    early_value = str(early.get('call') or 'WAIT') if early else 'WAIT'
    lock_status = str(locked.get('status') or '').lower() if locked else ''
    lock_value = str(locked.get('call') or 'WAIT') if locked else 'WAIT'
    # A saved pending lock is NOT a verified settlement result.
    lock_note = lock_status.upper() if lock_status else 'Not recorded'
    if remaining is None or remaining <= 0:
        mid_value, mid_note = 'WAIT', 'Cycle ending / rollover'
    elif remaining > 450:
        mid_value, mid_note = 'WAIT', 'Awaiting mid window'
    elif not quotes_ok or mid is None:
        mid_value, mid_note = 'WAIT', 'Quotes unverified / stale'
    elif mid >= .60:
        mid_value, mid_note = 'ABOVE', 'Provisional market lean'
    elif mid <= .40:
        mid_value, mid_note = 'BELOW', 'Provisional market lean'
    else:
        mid_value, mid_note = 'WAIT', 'No clear market lean'

    def card(label, value, note):
        v = str(value).upper()
        color = ('#53E7AB' if v in ('ABOVE', 'UP', 'YES')
                 else '#FF718E' if v in ('BELOW', 'DOWN', 'NO')
                 else '#F5C96B')
        st.markdown(
            '<div style="background:#261738;border:1px solid #5d397f;'
            'border-radius:12px;padding:10px 5px;text-align:center;min-height:95px">'
            f'<div style="font-size:.78rem;color:#D8C1EB;font-weight:700">{escape(label)}</div>'
            f'<div style="font-size:1.2rem;font-weight:900;color:{color};margin:4px 0">{escape(v)}</div>'
            f'<div style="font-size:.67rem;color:#B6A0CD">{escape(note)}</div></div>',
            unsafe_allow_html=True)

    st.markdown('**👑 EARLY · MID · LOCK**')
    a, b, c = st.columns(3, gap='small')
    with a:
        card('🌅 EARLY', early_value, 'Recorded call' if early else 'Awaiting signal')
    with b:
        card('⚡ MID', mid_value, mid_note)
    with c:
        card('🔒 LOCK', lock_value, lock_note)
    st.caption('MID is a provisional Kalshi quote lean, not a saved prediction. '
               'EARLY and LOCK reflect existing recorded paper calls.')




# ROYAL SCALP V1: independent forward-only, paper-only experiment.
# It does not alter V4 settlement positions, the V4 journal, or real orders.
def royal_objects(store, folder):
    result = store.request('POST', '/object/list/' + BUCKET, {
        'prefix': folder, 'limit': 200, 'offset': 0,
        'sortBy': {'column': 'name', 'order': 'desc'},
    })
    if not isinstance(result, list):
        raise RuntimeError('Royal scalp journal listing failed')
    names = [r['name'] for r in result if str(r.get('name', '')).endswith('.json')]
    return [(name, store.get(folder, name)) for name in names]


def royal_metrics(store):
    entries = royal_objects(store, 'royal_scalp_entries')
    exits = {k: v for k, v in royal_objects(store, 'royal_scalp_exits')}
    closed = [(k, e, exits[k]) for k, e in entries if k in exits]
    pnls = [float(x['pnl']) for _, _, x in closed]
    a, b, c, d = st.columns(4)
    a.metric('Scalp paper net', f'${sum(pnls):+.2f}')
    b.metric('Scalp wins', sum(x > 0 for x in pnls))
    c.metric('Scalp losses', sum(x < 0 for x in pnls))
    d.metric('Scalp open / closed', f'{len(entries)-len(closed)} / {len(closed)}')
    st.caption('Independent forward-only paper estimates; observed quotes, estimated fees and slippage, no actual fills.')
    if closed:
        with st.expander('Royal scalp paper history', expanded=False):
            st.dataframe([{
                'Contract': e.get('ticker'), 'Side': e.get('side'),
                'Entry ask': e.get('ask'), 'Exit bid': x.get('bid'),
                'Net P&L': x.get('pnl'), 'Exit': x.get('reason'),
            } for _, e, x in closed], use_container_width=True)
    return entries, exits


def royal_momentum(df):
    import pandas as pd
    if df is None or len(df) < 30:
        raise RuntimeError('Need at least 30 recent 1-minute candles')
    t = pd.to_datetime(df['time'].tail(30), utc=True, errors='coerce')
    gaps = t.diff().dt.total_seconds().dropna()
    if t.isna().any() or t.duplicated().any() or not gaps.between(45, 90).all():
        raise RuntimeError('Candles have gaps; no scalp entry')
    if (now_utc() - t.iloc[-1].to_pydatetime()).total_seconds() > 100:
        raise RuntimeError('Last 1-minute candle is stale')
    close = pd.to_numeric(df['close'], errors='coerce')
    if close.tail(30).isna().any():
        raise RuntimeError('Missing BTC candle prices')
    fast = close.ewm(span=9, adjust=False).mean()
    slow = close.ewm(span=21, adjust=False).mean()
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    last_loss = float(loss.iloc[-1])
    rsi = 100.0 if last_loss == 0 else 100 - 100/(1+float(gain.iloc[-1])/last_loss)
    up = sum([close.iloc[-1] > fast.iloc[-1], fast.iloc[-1] > slow.iloc[-1],
              fast.iloc[-1] > fast.iloc[-3], rsi > 52])
    down = sum([close.iloc[-1] < fast.iloc[-1], fast.iloc[-1] < slow.iloc[-1],
                fast.iloc[-1] < fast.iloc[-3], rsi < 48])
    return ('ABOVE' if up >= 3 and up > down else
            'BELOW' if down >= 3 and down > up else None), up, down, rsi


def royal_scan(store, market, candle_fn, enabled):
    ticker = str((market or {}).get('ticker') or '')
    if not ticker.startswith('KXBTC15M-'):
        return 'WAIT · No active BTC 15-minute contract'
    entries = royal_objects(store, 'royal_scalp_entries')
    exits = {k: v for k, v in royal_objects(store, 'royal_scalp_exits')}
    # Read existing entries first; never open two positions on one contract.
    open_positions = [(k, e) for k, e in entries if k not in exits]
    fresh, quote_at, spot, trade_at = fresh_snapshot(ticker)
    close = utc(fresh.get('close_time'))
    remaining = (close - now_utc()).total_seconds() if close else -1
    if not snapshot_current(quote_at, trade_at):
        return 'WAIT · Quote or BTC trade timestamp stale'
    if fresh.get('status') != 'active' or remaining <= 0:
        return 'WAIT · Contract closed; no simulated exit without a valid bid'
    df, _, feed_errors, _ = _get_candles(candle_fn)
    if feed_errors:
        return 'WAIT · Candle feed health warning'
    try:
        side, up, down, rsi = royal_momentum(df)
    except Exception as exc:
        return 'WAIT · ' + str(exc)
    multiplier = series_fee()
    for key, entry in open_positions:
        if entry.get('ticker') != ticker:
            # Never carry a paper position into a different contract.
            # No fabricated settlement P&L: pending until official grading.
            continue
        estimate = exit_estimate(entry, fresh)
        if estimate is None:
            return 'HOLD · Waiting for valid exit bid and depth'
        reversal = side is not None and side != entry['side']
        profit = estimate['pnl'] >= max(0.05, entry['risk'] * 0.12)
        loss = estimate['pnl'] <= -entry['risk'] * 0.20
        near_close = remaining <= 90
        if not (profit or loss or reversal or near_close):
            return f'HOLD {entry["side"]} · Est. cash-out ${estimate["pnl"]:+.2f}'
        reason = ('Profit review' if profit else 'Risk exit' if loss else
                  'Momentum reversal' if reversal else 'Contract ending')
        event = {
            'ticker': ticker, 'side': entry['side'], 'bid': quote(fresh, entry['side'], 'bid'),
            'exit_price': estimate['price'], 'exit_fee': estimate['fee'],
            'pnl': estimate['pnl'], 'reason': reason, 'at': now_utc().isoformat(),
            'kind': 'ROYAL_SCALP_SIMULATED_EXIT',
        }
        store.once('royal_scalp_exits', key, event)
        return f'CASH OUT {entry["side"]} · Paper ${estimate["pnl"]:+.2f} · {reason}'
    if not enabled:
        return 'PAUSED · Paper switch disabled'
    if entries and any(e.get('ticker') == ticker for _, e in entries):
        return 'WAIT · One scalp paper entry per contract'
    if not 90 < remaining < 850:
        return 'WAIT · Need at least 90 seconds before settlement'
    if side is None:
        return f'WAIT · Momentum not aligned (UP {up}/4, DOWN {down}/4, RSI {rsi:.0f})'
    ask, bid = quote(fresh, side, 'ask'), quote(fresh, side, 'bid')
    if ask is None or bid is None or not 0 < bid <= ask < 1:
        return 'WAIT · No valid two-sided contract quotes'
    if not 0.08 <= ask <= 0.90 or ask-bid > 0.05:
        return f'WAIT · {side} ask {ask*100:.0f}¢ or spread too costly'
    price = cents_round(ask + SLIPPAGE)
    qty, entry_fee, risk = size_for(price, multiplier)
    depth = available(fresh, side, 'ask')
    if qty < 1 or depth is None or depth < qty:
        return 'WAIT · Insufficient quote depth within $5 risk'
    # Avoid paper entries with no meaningful theoretical upside after fees.
    max_profit = qty - risk
    if max_profit < max(0.10, risk * 0.10):
        return 'WAIT · Upside too small relative to paper risk'
    if not snapshot_current(quote_at, trade_at):
        return 'WAIT · Quote aged before paper entry'
    key = ticker.replace('/', '_') + '.json'
    event = {
        'ticker': ticker, 'side': side, 'ask': ask, 'bid': bid,
        'price': price, 'qty': qty, 'entry_fee': entry_fee,
        'fee_multiplier': multiplier, 'risk': risk, 'slippage': SLIPPAGE,
        'target': number(fresh.get('floor_strike')), 'at': now_utc().isoformat(),
        'kind': 'ROYAL_SCALP_SIMULATED_ENTRY',
    }
    store.once('royal_scalp_entries', key, event)
    return f'BUY {side} · Paper {qty} contracts at {price*100:.0f}¢ (ask {ask*100:.0f}¢)'


def royal_live_status(store, market, candle_fn, enabled):
    try:
        message = royal_scan(store, market, candle_fn, enabled)
        st.session_state['royal_scalp_status'] = message
        return message
    except Exception as exc:
        message = 'WAIT · Royal scalp scanner unavailable: ' + str(exc)
        st.session_state['royal_scalp_status'] = message
        return message


def qe_performance_center():
    """Read-only visible V4 scoreboard; never combines hypothetical shadow returns."""
    st.markdown('#### 👑 QUEEN’S PERFORMANCE CENTER')
    st.caption('⚡ ROYAL SCALP · independent paper strategy | 🔒 ROYAL LOCK · selective settlement calls')
    try:
        url = st.secrets.get('SUPABASE_URL')
        key = st.secrets.get('SUPABASE_SERVICE_ROLE_KEY')
        if not url or not key:
            st.info('V4 performance unavailable: private paper journal not configured.')
            return
        rows = journal(str(url), str(key)).rows()
    except Exception:
        st.warning('V4 performance temporarily unavailable. Saved history has not been changed.')
        return
    st.markdown('**⚡ ROYAL SCALP · PAPER RESULTS**')
    try:
        royal_metrics(journal(str(url), str(key)))
    except Exception:
        st.caption('Scalp journal not yet available; no results invented.')
    st.caption(st.session_state.get('royal_scalp_status', 'Scalp scanner waiting for first live observation.'))
    st.markdown('**🔒 ROYAL LOCK / V4 SETTLEMENT · PAPER RESULTS**')
    finals = [r for r in rows if r.get('final')]
    values = [number(r['final'].get('managed_pnl')) or 0.0 for r in finals]
    wins = sum(v > 0 for v in values)
    losses = sum(v < 0 for v in values)
    a,b,c,d,e = st.columns(5, gap='small')
    a.metric('V4 paper P&L', f'${sum(values):+.2f}')
    b.metric('Wins', wins)
    c.metric('Losses', losses)
    d.metric('Win rate', f'{wins/len(finals):.1%}' if finals else '—')
    e.metric('Total paper entries', len(rows))
    st.caption(f'{len(finals)} graded · {len(rows)-len(finals)} pending · Estimated paper results, not real trades. Shadow studies are excluded.')
    t1,t2,t3 = st.tabs(['V4 PAPER RESULTS', 'TRADE HISTORY', 'SHADOW / STRATEGY RESULTS'])
    with t1:
        settlement = sum(number(r['final'].get('hold_pnl')) or 0 for r in finals)
        x,y = st.columns(2)
        x.metric('Hold-to-settlement net', f'${settlement:+.2f}')
        y.metric('Today managed net', f'${daily_pnl(rows):+.2f}')
    with t2:
        if rows:
            paper_history(rows)
        else:
            st.caption('No V4 paper entries recorded yet.')
    with t3:
        st.caption('Royal scalp results are independent from V4 settlement results. Old Shadow and strategy studies remain separate.')

def qe_simple_header(market, errors, quote_fn, candle_fn, verified, early=None, locked=None):
    """Only essentials above the chart; other tools stay collapsible."""
    close = utc((market or {}).get('close_time'))
    now = now_utc()
    remaining = max(0, int((close - now).total_seconds())) if close else None
    # Client-side clock: no Streamlit rerun, no chart remount or dimming.
    # The contract close timestamp is a snapshot; new contracts update on
    # the next normal dashboard execution, not via this browser timer.
    import streamlit.components.v1 as components
    close_ms = int(close.timestamp() * 1000) if close else None
    direction, signal, reason, fresh = qe_simple_status(market, errors, quote_fn, candle_fn, verified)
    yes = quote(fresh, 'ABOVE', 'ask') if fresh else None
    no = quote(fresh, 'BELOW', 'ask') if fresh else None
    from html import escape as _escape
    def _ask_text(value):
        return f'{value*100:.1f}¢' if value is not None and 0 <= value <= 1 else '—'
    close_json = json.dumps(close_ms)
    clock_html = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}html,body{margin:0;background:transparent;color:#ead7fa;font-family:system-ui,-apple-system,sans-serif}
.wrap{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding:2px 1px 4px}
.card{background:linear-gradient(110deg,#1b1030,#211331);border:1px solid #583677;border-radius:12px;padding:12px 9px;min-height:82px;text-align:center}
.label{font-size:clamp(10px,1.8vw,13px);opacity:.9}.value{font-size:clamp(18px,3.4vw,28px);font-weight:800;margin-top:7px;font-variant-numeric:tabular-nums}
.up{border-color:#198e60;color:#75efb3}.down{border-color:#b63d5a;color:#ffa3b7}
</style></head><body><div class="wrap">
<div class="card"><div class="label">⏳ SETTLEMENT IN</div><div class="value" id="settle">--:--</div></div>
<div class="card up"><div class="label">🟢 ABOVE ASK</div><div class="value">__YES__</div></div>
<div class="card down"><div class="label">🔴 BELOW ASK</div><div class="value">__NO__</div></div>
</div><script>const closeMs=__CLOSE_MS__;function tick(){const el=document.getElementById('settle');if(closeMs===null){el.textContent='—';return;}const secs=Math.max(0,Math.ceil((closeMs-Date.now())/1000));el.textContent=String(Math.floor(secs/60)).padStart(2,'0')+':'+String(secs%60).padStart(2,'0');}tick();setInterval(tick,250);</script></body></html>"""
    clock_html = (clock_html.replace('__CLOSE_MS__', close_json)
                  .replace('__YES__', _escape(_ask_text(yes)))
                  .replace('__NO__', _escape(_ask_text(no))))
    components.html(clock_html, height=100, scrolling=False)
    a, b = st.columns(2)
    a.metric('📉 MARKET DIRECTION', direction)
    b.metric('👑 QUEEN’S SIGNAL', signal)
    st.caption(reason + ' · Paper observation only; no automatic orders.')


def launch():
    # Keep the existing fragment scanner and all Streamlit status/error UI.
    # Only counteract the stale-element fade during fragment rerenders.
    st.markdown("""
    <style>
    [data-testid="stFragment"] [data-stale="true"],
    [data-testid="stFragment"][data-stale="true"],
    [data-testid="stAppViewContainer"] [data-stale="true"] {
        opacity: 1 !important;
        filter: none !important;
    }
    </style>
    """, unsafe_allow_html=True)
    original = (
        Path(__file__).resolve().parent / 'queens_original.py'
    )

    if not original.is_file():
        st.error('Missing queens_original.py beside app.py')
        st.stop()

    source = original.read_text(encoding='utf-8')
    # The chart maintains its own Coinbase WebSocket. Disable only the
    # original whole-page timer; the isolated V4 fragment scans every 15s.
    full_page_timer = 'st_autorefresh(interval=15_000, limit=None, key="btc_full_refresh")'
    if source.count(full_page_timer) != 1:
        st.error('Preview compatibility check failed: original refresh timer')
        st.stop()
    source = source.replace(full_page_timer, '# Whole-page refresh disabled in preview; V4 fragment runs independently.', 1)
    source = source.replace(
        'st.caption(\n    "⚡ Live Kalshi quotes · "\n    "🔐 Dual Strong LOCK · "\n    "🐋 Pressure Radar · "\n    "📊 Paper Performance"\n)\n',
        '', 1,
    )

    # Reserve the chart's visual position immediately beneath the
    # Bitcoin Command Center heading. The actual chart still executes
    # later, once the market, strike, and close time are available.
    chart_anchor = (
        '    st.markdown("### 👑 BITCOIN COMMAND CENTER · PAPER MODE")'
    )
    if source.count(chart_anchor) != 1:
        st.error('Preview compatibility check failed: chart anchor')
        st.stop()
    source = source.replace(
        chart_anchor,
        chart_anchor + '\n    qe_chart_slot = st.container()',
        1,
    )

    source = source.replace(
        '    qe_compact_summary(name, series, market, errors)\n'
        '    qe_visible_signal_panel(market, series)',
        '    qe_simple_header(market, errors, quote, btc_candles, verified_direction(market) if market else False, early_map.get(market.get("ticker")) if market else None, locked_map.get(market.get("ticker")) if market else None)',
        1,
    )
    if '    qe_simple_header(market, errors, quote, btc_candles, verified_direction(market) if market else False, early_map.get(market.get("ticker")) if market else None, locked_map.get(market.get("ticker")) if market else None)' not in source:
        st.error('Preview compatibility check failed: simplified header')
        st.stop()

    patches = [
        (
            'c.metric("Indicative distance", f"${abs(delta):,.2f}", '
            '"ABOVE" if delta > 0 else "BELOW" if delta < 0 else "AT TARGET")',
            'c.metric("Indicative distance", f"${abs(delta):,.2f}", '
            '"ABOVE" if delta > 0 else "BELOW" if delta < 0 else "AT TARGET", '
            'delta_color="off")',
            'distance arrow',
        ),
        (
            'side = st.radio("Side", ["YES", "NO"], horizontal=True, '
            'key=f"qe_calc_side_{series}")',
            'side = st.radio("Side · manual paper calculator", '
            '["NONE", "YES", "NO"], horizontal=True, '
            'key=f"qe_calc_side_{series}")',
            'paper calculator default',
        ),
        (
            'ask = yes_ask if side == "YES" else no_ask',
            'ask = yes_ask if side == "YES" '
            'else no_ask if side == "NO" else None',
            'paper calculator safety',
        ),
        (
            '    st.markdown("#### 📈 Live Bitcoin price action")\n'
            '    frame = btc_candles()\n'
            '    qe_btc_chart(frame, strike)',
            '    qe_close = parse_time(market.get("close_time") or '
            'market.get("latest_expiration_time")) if market else None\n'
            '    qe_seconds = (qe_close - datetime.now(timezone.utc))'
            '.total_seconds() if qe_close else None\n'
            '    with st.expander("⚙️ Advanced Tools · paper P&L, diagnostics & history", expanded=False):\n'
            '        render_paper_engine(market, strike, qe_indicative_btc(), '
            'qe_seconds, verified_direction(market) if market else False, '
            'quote, btc_candles, bool(errors.get(series)))\n'
            '        with st.expander("🔍 Original V3 diagnostics", expanded=False):\n'
            '            render_v3_guard(market, strike, qe_indicative_btc(), '
            'qe_seconds, verified_direction(market) if market else False, '
            'quote, btc_candles, bool(errors.get(series)))\n'
            '    with qe_chart_slot:\n'
            '        st.markdown("#### 📈 LIVE BITCOIN · STREAMING CHART")\n'
            '        render_live_chart(strike, close_time=qe_close, '
            'market_ticker=market.get("ticker") if market else None)\n'
            '    qe_stage_row(market, errors, verified_direction(market) if market else False, '
            'early_map.get(market.get("ticker")) if market else None, '
            'locked_map.get(market.get("ticker")) if market else None)\n'
            '    qe_performance_center()\n'
            '    visible_scanner_status()\n'
            '    with st.expander("📉 Original historical chart · Backup", '
            'expanded=False):\n'
            '        frame = btc_candles()\n'
            '        qe_btc_chart(frame, strike)',
            'forward paper engine and live chart',
        ),
    ]

    for before, after, label in patches:
        if source.count(before) != 1:
            st.error(
                'Preview compatibility check failed: '
                + label
                + '. queens_original.py was not changed.'
            )
            st.stop()

        source = source.replace(before, after, 1)

    chart_path = original.parent / 'queens_live_chart.py'

    try:
        chart = clean_chart_renderer(chart_path)
        tree = compact_original(source)

    except (RuntimeError, OSError, SyntaxError) as exc:
        st.error(
            'Compact-layout compatibility check failed: '
            + str(exc)
        )
        st.stop()

    namespace = {
        '__name__': '__main__',
        '__file__': str(original),
        '__package__': None,
        'render_live_chart': chart,
        'render_v3_guard': render_v3_guard,
        'render_paper_engine': render_paper_engine,
        'qe_simple_header': qe_simple_header,
    }

    exec(
        compile(tree, str(original), 'exec'),
        namespace,
    )


if __name__ == '__main__':
    launch()
