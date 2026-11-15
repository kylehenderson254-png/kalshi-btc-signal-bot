"""QUEEN'S EDGE | 254: $20 paired paper exit study. No trading endpoints."""
from datetime import datetime, timezone

START_UTC = datetime(2026, 11, 15, 6, 43, tzinfo=timezone.utc)
BUDGET = 20.0
FOLDERS = {'Original Cash-Out': 'qe20_original', 'Royal Smart Profit': 'qe20_smart'}

def _time(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

def _objects(store, folder):
    result = []
    offset = 0
    while True:
        batch = store.request('POST', '/object/list/queens-edge-paper-v4', {
            'prefix': folder, 'limit': 100, 'offset': offset,
            'sortBy': {'column': 'name', 'order': 'desc'}})
        if not isinstance(batch, list):
            raise RuntimeError('Paper storage listing unavailable')
        for item in batch:
            key = item.get('name', '')
            if key.endswith('.json'):
                row = store.get(folder, key)
                if isinstance(row, dict):
                    result.append((key, row))
        if len(batch) < 100:
            break
        offset += len(batch)
    return result

def _sizing(price, multiplier, fee_fn):
    qty = int(BUDGET / price)
    while qty and qty * price + fee_fn(qty, price, multiplier) > BUDGET + 1e-9:
        qty -= 1
    if not qty:
        return None
    entry_fee = fee_fn(qty, price, multiplier)
    return qty, entry_fee, round(qty * price + entry_fee, 6)

def _estimate(entry, market, quote_fn, available_fn, fee_fn, slip, round_fn):
    side = entry['side']
    bid, ask = quote_fn(market, side, 'bid'), quote_fn(market, side, 'ask')
    depth = available_fn(market, side, 'bid')
    if (bid is None or ask is None or not 0 < bid <= ask < 1
            or bid <= slip or depth is None or depth < entry['qty']):
        return None
    price = round_fn(bid - slip, False)
    exit_fee = fee_fn(entry['qty'], price, entry['fee_multiplier'])
    pnl = round(entry['qty'] * price - exit_fee - entry['risk'], 6)
    return {'price': price, 'bid': bid, 'fee': exit_fee,
            'pnl': pnl, 'roi': pnl / entry['risk'] * 100}

def _smart_rule(roi, peak, remaining, side, momentum_side, score):
    aligned = momentum_side == side and score >= 3
    opposite = momentum_side not in (None, side) and score >= 3
    if remaining <= 18:
        return 'Pre-expiry exit review'
    if roi <= -45:
        return 'Emergency loss review'
    if peak >= 15 and roi <= max(10, peak - 10):
        return 'Trailing profit protection'
    if roi >= 60:
        return 'Maximum profit target'
    if roi >= 20 and (not aligned or opposite or remaining <= 75):
        return 'Adaptive profit target'
    if 10 <= roi < 20 and (opposite or not aligned):
        return 'Early profit protection'
    if roi <= -20 and (opposite or remaining <= 75):
        return 'Loss / reversal review'
    return None

def cycle(store, market, enabled, *, quote_fn, available_fn, fee_fn,
          round_fn, slippage, momentum_fn, candle_fn, fresh_fn,
          series_fee_fn, original_close_fn, snapshot_fn):
    """Called after the unchanged Royal Scalp signal engine. Fails closed."""
    ticker = str((market or {}).get('ticker') or '')
    if not ticker.startswith('KXBTC15M-'):
        return
    now = datetime.now(timezone.utc)
    if now < START_UTC:
        return
    fresh, quote_at, spot, trade_at = fresh_fn(ticker)
    if (fresh.get('ticker') != ticker or fresh.get('status') != 'active'
            or not snapshot_fn(quote_at, trade_at)):
        return
    close = _time(fresh.get('close_time'))
    if close is None:
        return
    remaining = (close - now).total_seconds()
    if remaining <= 0:
        return
    # Both comparison legs use identical original Royal Scalp entry opportunities.
    original_entries = dict(_objects(store, 'royal_scalp_entries'))
    key = ticker.replace('/', '_') + '.json'
    source = original_entries.get(key)
    if not isinstance(source, dict):
        return
    source_time = _time(source.get('at'))
    if source_time is None or source_time < START_UTC:
        return
    # Do not retrospectively create positions from an old signal.
    if not enabled or not 0 <= (now - source_time).total_seconds() <= 35:
        allow_entry = False
    else:
        allow_entry = True
    df, _, errors, _ = __import__('queens_v3_guard')._get_candles(candle_fn)
    if errors:
        return
    momentum_side, up, down, _ = momentum_fn(df)
    strength = up if momentum_side == 'ABOVE' else down if momentum_side == 'BELOW' else 0
    multiplier = series_fee_fn()
    for label, prefix in FOLDERS.items():
        entry_folder, exit_folder, peak_folder = prefix + '_entries', prefix + '_exits', prefix + '_peaks'
        entry = store.get(entry_folder, key)
        if entry is None and allow_entry:
            side = source.get('side')
            if side not in ('ABOVE', 'BELOW') or source.get('target') is None:
                continue
            ask = quote_fn(fresh, side, 'ask')
            bid = quote_fn(fresh, side, 'bid')
            if ask is None or bid is None or not 0 < bid <= ask < 1:
                continue
            if ask - bid > .05 or not .08 <= ask <= .90:
                continue
            price = round_fn(ask + slippage)
            sizing = _sizing(price, multiplier, fee_fn)
            if sizing is None:
                continue
            qty, entry_fee, risk = sizing
            depth = available_fn(fresh, side, 'ask')
            if depth is None or depth < qty:
                continue
            entry = {'ticker': ticker, 'side': side, 'qty': qty, 'price': price,
                     'ask': ask, 'risk': risk, 'entry_fee': entry_fee,
                     'fee_multiplier': multiplier, 'slippage': slippage,
                     'target': source.get('target'), 'at': now.isoformat(),
                     'source_signal_at': source.get('at'), 'strategy': label,
                     'budget': BUDGET, 'close_time': close.isoformat()}
            entry = store.once(entry_folder, key, entry)
        if not isinstance(entry, dict) or store.get(exit_folder, key) is not None:
            continue
        estimate = _estimate(entry, fresh, quote_fn, available_fn, fee_fn, slippage, round_fn)
        if estimate is None:
            continue
        peak = store.get(peak_folder, key)
        observed_peak = max(float(peak.get('peak_roi', estimate['roi'])) if isinstance(peak, dict)
                            else estimate['roi'], estimate['roi'])
        # Append-only storage: record new highs as separate checkpoint objects.
        # Rebuild peak from checkpoints to survive Streamlit restarts.
        checkpoints = _objects(store, peak_folder + '/' + key[:-5])
        for _, point in checkpoints:
            try:
                observed_peak = max(observed_peak, float(point['peak_roi']))
            except (KeyError, ValueError, TypeError):
                pass
        if observed_peak >= 0:
            checkpoint = int(observed_peak // 5) * 5
            if checkpoint >= 5:
                store.once(peak_folder + '/' + key[:-5], f'{checkpoint:03d}.json',
                           {'peak_roi': float(checkpoint), 'at': now.isoformat()})
        if label == 'Original Cash-Out':
            trend = 'UPWARD' if momentum_side == 'ABOVE' else 'DOWNWARD' if momentum_side == 'BELOW' else 'SIDEWAYS'
            reason = original_close_fn(entry, estimate, spot, trend, remaining)
        else:
            reason = _smart_rule(estimate['roi'], observed_peak, remaining,
                                 entry['side'], momentum_side, strength)
        if reason:
            store.once(exit_folder, key, {'ticker': ticker, 'side': entry['side'],
                'at': now.isoformat(), 'bid': estimate['bid'],
                'exit_price': estimate['price'], 'exit_fee': estimate['fee'],
                'pnl': estimate['pnl'], 'roi': estimate['roi'],
                'peak_roi_observed': observed_peak, 'reason': reason,
                'strategy': label, 'kind': 'PAPER_SIMULATED_EXIT'})

def dashboard(store, st):
    st.markdown('### 👑 $20 CASH-OUT SHOWDOWN')
    st.caption('Fresh paired paper study only · prior $5 results hidden, not deleted · indicative fills')
    records, details = [], []
    for label, prefix in FOLDERS.items():
        entries = _objects(store, prefix + '_entries')
        exits = dict(_objects(store, prefix + '_exits'))
        values = []
        pending = 0
        for key, entry in entries:
            event = exits.get(key)
            if event is None:
                pending += 1
                continue
            pnl = event.get('pnl')
            if not isinstance(pnl, (float, int)):
                pending += 1
                continue
            values.append(pnl)
            details.append({'Strategy': label, 'Contract': entry.get('ticker'),
                            'Side': entry.get('side'), 'Entry': entry.get('at'),
                            'Exit': event.get('at'), 'Net P&L': pnl,
                            'ROI %': event.get('roi'),
                            'Peak ROI %': event.get('peak_roi_observed'),
                            'Reason': event.get('reason')})
        wins = sum(x > 0 for x in values)
        losses = sum(x < 0 for x in values)
        records.append({'Strategy': label, 'Wins': wins, 'Losses': losses,
                        'Flat': sum(x == 0 for x in values),
                        'Win rate': f'{wins / len(values):.1%}' if values else '—',
                        'Completed': len(values), 'Pending': pending,
                        'Net P&L': f'${sum(values):+.2f}'})
    st.dataframe(records, use_container_width=True, hide_index=True)
    if details:
        st.dataframe(sorted(details, key=lambda x: x.get('Exit') or '', reverse=True),
                     use_container_width=True, hide_index=True)
    st.caption('Open or unpriced contracts are pending, not wins or losses. A quote is not a guaranteed fill.')
