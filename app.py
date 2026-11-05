"""QUEEN'S EDGE | 254 — preview-only forward paper test.

NO REAL ORDERS.

Keep these files beside app.py:
- queens_original.py
- queens_live_chart.py
- queens_v3_guard.py
"""

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from queens_live_chart import render_live_chart
from queens_v3_guard import (
    render_v3_guard,
    assess,
    _get_candles,
)


API = "https://external-api.kalshi.com/trade-api/v2"
BUCKET = "queens-edge-paper-v4"
CT = ZoneInfo("America/Chicago")

# Experimental paper-test settings, not optimized parameters.
RISK = 5.0
ENTRY_CAP = 0.70
SLIPPAGE = 0.01
TAKE_PROFIT = 0.25
STOP_REVIEW = 0.20
DAILY_STOP = 15.0


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def utc(value):
    try:
        value = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

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
    rounding = ROUND_CEILING if up else ROUND_FLOOR

    return float(
        Decimal(str(value)).quantize(
            Decimal("0.01"),
            rounding=rounding,
        )
    )


def fee(qty, price, multiplier):
    """
    Conservative fee estimate for whole-contract,
    whole-cent assumed fills.

    This is not an account execution or exact fee statement.
    """
    model = (
        Decimal("0.07")
        * Decimal(str(multiplier))
        * qty
        * Decimal(str(price))
        * (1 - Decimal(str(price)))
    )

    return float(
        model.quantize(
            Decimal("0.01"),
            rounding=ROUND_CEILING,
        )
    )


def size_for(price, multiplier):
    if not 0 < price < 1 or not multiplier >= 0:
        return 0, 0.0, 0.0

    qty = int(RISK / price)

    while (
        qty > 0
        and qty * price + fee(qty, price, multiplier)
        > RISK + 1e-9
    ):
        qty -= 1

    entry_fee = fee(qty, price, multiplier)
    cost = round(qty * price + entry_fee, 6)

    return qty, entry_fee, cost


def quote(market, side, kind):
    prefix = "yes" if side == "ABOVE" else "no"

    return number(
        market.get(prefix + "_" + kind + "_dollars")
    )


def available(market, side, kind):
    # NO asks mirror YES bids.
    # NO bids mirror YES asks.
    if side == "BELOW":
        kind = "bid" if kind == "ask" else "ask"

    return number(
        market.get("yes_" + kind + "_size_fp")
    )


def public_get(path, fresh=False):
    response = requests.get(
        API + path,
        timeout=7,
        headers={"Cache-Control": "no-cache"},
    )

    response.raise_for_status()

    if fresh:
        date = response.headers.get("Date")

        if not date:
            raise RuntimeError(
                "Quote response has no verifiable HTTP timestamp"
            )

        from email.utils import parsedate_to_datetime

        age = (
            now_utc() - parsedate_to_datetime(date)
        ).total_seconds()

        if not -3 <= age <= 12:
            raise RuntimeError(
                "Quote response timestamp is stale"
            )

    return response.json()


@st.cache_data(ttl=300, show_spinner=False)
def series_fee():
    series = public_get(
        "/series/KXBTC15M"
    )["series"]

    multiplier = number(
        series.get("fee_multiplier")
    )

    if (
        series.get("fee_type") != "quadratic"
        or multiplier is None
        or multiplier < 0
    ):
        raise RuntimeError(
            "This paper test cannot model the current series fee type"
        )

    return multiplier


def trade_snapshot():
    response = requests.get(
        "https://api.exchange.coinbase.com/"
        "products/BTC-USD/ticker",
        timeout=7,
    )

    response.raise_for_status()
    data = response.json()

    price = number(data.get("price"))
    stamp = utc(data.get("time"))

    age = (
        (now_utc() - stamp).total_seconds()
        if stamp
        else None
    )

    if (
        price is None
        or price <= 0
        or age is None
        or not -3 <= age <= 10
    ):
        raise RuntimeError(
            "BTC trade timestamp is stale or unavailable"
        )

    return price, stamp


class PaperJournal:
    """
    Private, append-only paper events.

    Existing tracker tables are never edited.
    """

    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/storage/v1"

        self.headers = {
            "apikey": key,
            "Authorization": "Bearer " + key,
        }

        self.cache = {}

    def request(
        self,
        method,
        path,
        payload=None,
        missing=False,
    ):
        response = requests.request(
            method,
            self.base + path,
            headers={
                **self.headers,
                "Content-Type": "application/json",
                "x-upsert": "false",
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
                data.get("code")
                or data.get("error")
                or ""
            )

            status = str(
                data.get("statusCode", "")
            )
        else:
            code = ""
            status = ""

        if missing and (
            response.status_code == 404
            or status == "404"
            or code in (
                "NoSuchKey",
                "NoSuchBucket",
            )
        ):
            return None

        if (
            response.status_code == 409
            or status == "409"
            or code == "Duplicate"
        ):
            return {"duplicate": True}

        if not response.ok:
            raise RuntimeError(
                "Private paper journal request failed "
                "(HTTP "
                + str(response.status_code)
                + ")"
            )

        return data

    def ensure(self, create=False):
        bucket = self.request(
            "GET",
            "/bucket/" + BUCKET,
            missing=True,
        )

        if bucket is None and create:
            self.request(
                "POST",
                "/bucket",
                {
                    "id": BUCKET,
                    "name": BUCKET,
                    "public": False,
                },
            )

            bucket = self.request(
                "GET",
                "/bucket/" + BUCKET,
            )

        if bucket is None:
            return False

        if bucket.get("public") is not False:
            raise RuntimeError(
                "The V4 journal must be private; "
                "paper entries are blocked"
            )

        return True

    def get(self, folder, key):
        path = folder + "/" + key

        if path not in self.cache:
            value = self.request(
                "GET",
                "/object/authenticated/"
                + BUCKET
                + "/"
                + path,
                missing=True,
            )

            if value is not None:
                self.cache[path] = value

            return value

        return self.cache[path]

    def once(self, folder, key, value):
        result = self.request(
            "POST",
            "/object/"
            + BUCKET
            + "/"
            + folder
            + "/"
            + key,
            value,
        )

        if result.get("duplicate"):
            existing = self.get(folder, key)

            if existing is None:
                raise RuntimeError(
                    "Could not verify the existing paper event"
                )

            return existing

        self.cache[folder + "/" + key] = value
        return value

    def rows(self):
        objects = self.request(
            "POST",
            "/object/list/" + BUCKET,
            {
                "prefix": "entries",
                "limit": 200,
                "offset": 0,
                "sortBy": {
                    "column": "name",
                    "order": "desc",
                },
            },
        )

        names = [
            item["name"]
            for item in objects
            if str(item.get("name", "")).endswith(".json")
        ]

        def load(key):
            entry = self.get("entries", key)

            if not entry:
                raise RuntimeError(
                    "A paper entry could not be read"
                )

            final = self.get("finals", key)

            return {
                "key": key,
                "entry": entry,
                "final": final,
                "exit": (
                    None
                    if final
                    else self.get("exits", key)
                ),
            }

        with ThreadPoolExecutor(max_workers=6) as pool:
            return list(pool.map(load, names))


@st.cache_resource(show_spinner=False)
def journal(url, key):
    return PaperJournal(url, key)


def exit_estimate(entry, market):
    bid = quote(
        market,
        entry["side"],
        "bid",
    )

    ask = quote(
        market,
        entry["side"],
        "ask",
    )

    size = available(
        market,
        entry["side"],
        "bid",
    )

    slippage = entry.get(
        "slippage",
        SLIPPAGE,
    )

    if (
        bid is None
        or ask is None
        or not slippage < bid <= ask < 1
        or size is None
        or size < entry["qty"]
    ):
        return None

    price = max(
        0.0,
        cents_round(
            bid - slippage,
            False,
        ),
    )

    exit_fee = fee(
        entry["qty"],
        price,
        entry["fee_multiplier"],
    )

    pnl = round(
        entry["qty"] * price
        - exit_fee
        - entry["risk"],
        6,
    )

    return {
        "price": price,
        "fee": exit_fee,
        "pnl": pnl,
    }


def close_reason(
    entry,
    estimate,
    spot,
    trend,
    seconds,
):
    profit_fraction = entry.get(
        "take_profit_fraction",
        TAKE_PROFIT,
    )

    loss_fraction = entry.get(
        "loss_review_fraction",
        STOP_REVIEW,
    )

    if (
        estimate["pnl"]
        >= profit_fraction * entry["risk"]
    ):
        return (
            "Experimental profit review: "
            "+25% of entry risk"
        )

    if (
        estimate["pnl"]
        <= -loss_fraction * entry["risk"]
    ):
        return (
            "Experimental loss review: "
            "-20% of entry risk"
        )

    target_crossed = (
        entry["side"] == "ABOVE"
        and spot < entry["target"]
    ) or (
        entry["side"] == "BELOW"
        and spot > entry["target"]
    )

    if target_crossed:
        return (
            "Spot crossed the target "
            "against the position"
        )

    opposing_trend = (
        entry["side"] == "ABOVE"
        and trend == "DOWNWARD"
    ) or (
        entry["side"] == "BELOW"
        and trend == "UPWARD"
    )

    if opposing_trend:
        return (
            "Opposing EMA and "
            "momentum confirmation"
        )

    if seconds <= 75:
        return (
            "Exit review before the "
            "final settlement minute"
        )

    return None


def grade_papers(store, rows):
    checks = 0

    for row in rows:
        entry = row["entry"]

        if (
            row["final"]
            or now_utc().timestamp()
            < entry["close_ts"] + 60
            or checks >= 5
        ):
            continue

        checks += 1

        try:
            market = public_get(
                "/markets/" + entry["ticker"]
            )["market"]

            outcome = market.get("result")

            if (
                market.get("status")
                not in ("settled", "finalized")
                or outcome not in ("yes", "no")
            ):
                continue

            win = (
                entry["side"] == "ABOVE"
            ) == (
                outcome == "yes"
            )

            hold_pnl = round(
                (
                    entry["qty"]
                    if win
                    else 0
                )
                - entry["risk"],
                6,
            )

            exit_event = (
                row["exit"]
                or store.get(
                    "exits",
                    row["key"],
                )
            )

            managed_closed_at = (
                exit_event["observed_at"]
                if exit_event
                else datetime.fromtimestamp(
                    entry["close_ts"],
                    timezone.utc,
                ).isoformat()
            )

            final = {
                "outcome": outcome,
                "value": market.get(
                    "expiration_value"
                ),
                "hold_pnl": hold_pnl,
                "managed_pnl": (
                    exit_event["pnl"]
                    if exit_event
                    else hold_pnl
                ),
                "exit_reason": (
                    exit_event["reason"]
                    if exit_event
                    else "Held to official settlement"
                ),
                "managed_closed_at": (
                    managed_closed_at
                ),
                "graded_at": (
                    now_utc().isoformat()
                ),
            }

            row["final"] = store.once(
                "finals",
                row["key"],
                final,
            )

        except (
            requests.RequestException,
            ValueError,
            KeyError,
        ):
            continue


def candidate(
    market,
    result,
    df,
    spot,
    multiplier,
):
    if result.get("status") != "CANDIDATE":
        return None, list(
            result.get("reasons")
            or [
                "V3 candidate conditions "
                "did not pass"
            ]
        )

    side = result.get("side")

    if (
        df is None
        or len(df) < 60
        or "time" not in df
    ):
        return None, [
            "Need 60 consecutive 1-minute "
            "candles for this forward test"
        ]

    pd = __import__("pandas")

    times = pd.to_datetime(
        df["time"].tail(60),
        utc=True,
        errors="coerce",
    )

    intervals = (
        times.diff()
        .dt.total_seconds()
        .dropna()
    )

    if (
        times.isna().any()
        or times.duplicated().any()
        or not intervals.between(45, 90).all()
    ):
        return None, [
            "Recent 1-minute candles have gaps "
            "or inconsistent timestamps"
        ]

    ask = quote(market, side, "ask")
    bid = quote(market, side, "bid")

    if (
        ask is None
        or bid is None
        or not 0 <= bid <= ask < 1
        or ask - bid > 0.030001
    ):
        return None, [
            "Executable quotes are missing "
            "or the spread is wider than 3¢"
        ]

    price = cents_round(
        ask + SLIPPAGE
    )

    if not 0 < price <= ENTRY_CAP:
        return None, [
            "Experimental effective-entry cap "
            "is 70¢, including 1¢ slippage"
        ]

    qty, entry_fee, risk = size_for(
        price,
        multiplier,
    )

    size = available(
        market,
        side,
        "ask",
    )

    if (
        qty < 1
        or size is None
        or size < qty
    ):
        return None, [
            "Insufficient displayed size "
            "for this paper entry"
        ]

    previous = df["close"].shift(1)

    ranges = pd.concat(
        [
            (
                df["high"] - df["low"]
            ).abs(),
            (
                df["high"] - previous
            ).abs(),
            (
                df["low"] - previous
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = float(
        ranges.tail(14).mean()
    )

    seconds = float(
        result["seconds_left"]
    )

    cushion = max(
        50,
        2 * atr * math.sqrt(seconds / 60),
    )

    distance = abs(
        spot - float(
            market["floor_strike"]
        )
    )

    if distance < cushion:
        return None, [
            "Target cushion is too small "
            "relative to recent volatility"
        ]

    candle_age = result.get(
        "candle_age"
    )

    if (
        candle_age is None
        or not -3 <= candle_age <= 90
    ):
        return None, [
            "Need candles no older "
            "than 90 seconds"
        ]

    return {
        "side": side,
        "price": price,
        "qty": qty,
        "fee": entry_fee,
        "risk": risk,
        "distance": distance,
        "breakeven": risk / qty,
    }, []


def render_paper_engine(
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
        "#### 👑 Forward Paper Test "
        "· Entry & Exit Comparison"
    )

    enabled = st.checkbox(
        "Enable new V4 paper entries "
        "— never real orders",
        value=False,
        key="qe_v4_enabled",
    )

    st.caption(
        "Experimental fixed settings: "
        "effective entry ≤70¢; spread ≤3¢; "
        "$5 risk including fee estimate; "
        "1¢ adverse slippage each way. "
        "Two independent quote observations "
        "at least 10 seconds apart are required. "
        "These settings are not optimized "
        "or proven profitable."
    )

    url = st.secrets.get(
        "SUPABASE_URL"
    )

    key = st.secrets.get(
        "SUPABASE_SERVICE_ROLE_KEY"
    )

    if not url or not key:
        st.warning(
            "V4 paper recording is disabled: "
            "existing Supabase credentials "
            "are unavailable. No temporary "
            "results will be presented "
            "as saved history."
        )
        return

    try:
        store = journal(
            str(url),
            str(key),
        )

        if not store.ensure(
            create=enabled
        ):
            st.info(
                "Enable the paper test to "
                "initialize its separate "
                "private journal. Existing "
                "tracker tables are preserved."
            )
            return

        rows = store.rows()

        grade_papers(
            store,
            rows,
        )

        final_rows = [
            row
            for row in rows
            if row["final"]
        ]

        managed = sum(
            row["final"]["managed_pnl"]
            for row in final_rows
        )

        hold = sum(
            row["final"]["hold_pnl"]
            for row in final_rows
        )

        today = (
            now_utc()
            .astimezone(CT)
            .date()
        )

        day_pnl = 0.0

        for row in rows:
            event = (
                row["final"]
                or row["exit"]
            )

            if event:
                closed = utc(
                    event.get(
                        "managed_closed_at"
                    )
                    or event.get(
                        "observed_at"
                    )
                )

                if (
                    closed
                    and closed.astimezone(CT).date()
                    == today
                ):
                    day_pnl += (
                        event["managed_pnl"]
                        if row["final"]
                        else event["pnl"]
                    )

        cols = st.columns(4)

        cols[0].metric(
            "Matched managed P&L",
            f"${managed:+.2f}",
        )

        cols[1].metric(
            "Matched settlement P&L",
            f"${hold:+.2f}",
        )

        cols[2].metric(
            "Paper entries / official outcomes",
            f"{len(rows)} / {len(final_rows)}",
        )

        cols[3].metric(
            "Today · managed realized",
            f"${day_pnl:+.2f}",
        )

        message = (
            "New entries paused; existing "
            "paper positions are still monitored."
        )

        if not enabled:
            st.session_state.pop(
                "qe_v4_confirm",
                None,
            )

        ticker = str(
            (market or {}).get(
                "ticker",
                "",
            )
        )

        if not ticker.startswith(
            "KXBTC15M-"
        ):
            raise RuntimeError(
                "No verified Bitcoin "
                "15-minute ticker"
            )

        fresh_market = public_get(
            "/markets/" + ticker,
            fresh=True,
        )["market"]

        quote_at = now_utc()

        close = utc(
            fresh_market.get(
                "close_time"
            )
        )

        if (
            close is None
            or fresh_market.get("ticker")
            != ticker
        ):
            raise RuntimeError(
                "Current contract identity "
                "or close time unavailable"
            )

        seconds = (
            close - now_utc()
        ).total_seconds()

        if (
            fresh_market.get("status")
            == "active"
            and seconds > 0
        ):
            live_spot, trade_time = (
                trade_snapshot()
            )

            (
                df,
                feed_name,
                errors,
                clock,
            ) = _get_candles(
                candle_fn
            )

            result = assess(
                fresh_market,
                fresh_market.get(
                    "floor_strike"
                ),
                live_spot,
                df,
                seconds,
                bool(verified),
                quote_fn,
                False,
                feed_name,
            )

            if not snapshot_current(
                quote_at,
                trade_time,
            ):
                raise RuntimeError(
                    "Quote or BTC tick aged "
                    "during evaluation; waiting "
                    "for a new snapshot"
                )

            for row in rows:
                entry = row["entry"]

                if (
                    entry["ticker"] == ticker
                    and not row["final"]
                    and not row["exit"]
                ):
                    estimate = exit_estimate(
                        entry,
                        fresh_market,
                    )

                    if estimate:
                        reason = close_reason(
                            entry,
                            estimate,
                            live_spot,
                            result.get(
                                "ema_trend"
                            ),
                            seconds,
                        )

                        if reason:
                            row["exit"] = (
                                store.once(
                                    "exits",
                                    row["key"],
                                    {
                                        **estimate,
                                        "reason": reason,
                                        "observed_at": (
                                            now_utc()
                                            .isoformat()
                                        ),
                                    },
                                )
                            )

                            message = (
                                "Paper exit observed: "
                                + reason
                            )

            if enabled:
                multiplier = (
                    series_fee()
                )

                proposed, reasons = (
                    candidate(
                        fresh_market,
                        result,
                        df,
                        live_spot,
                        multiplier,
                    )
                )

                active = any(
                    not row["final"]
                    and row["entry"]["close_ts"]
                    > now_utc().timestamp()
                    for row in rows
                )

                file_key = (
                    str(
                        int(
                            close.timestamp()
                            * 1000
                        )
                    )
                    + "_"
                    + ticker
                    + ".json"
                )

                existing = store.get(
                    "entries",
                    file_key,
                )

                if existing:
                    message = (
                        "This market already has "
                        "its one V4 paper entry. "
                        "No repeat or opposite-side "
                        "entry."
                    )

                elif day_pnl <= -DAILY_STOP:
                    message = (
                        "Daily paper loss review "
                        "limit reached (-$15). "
                        "New entries paused "
                        "for today."
                    )

                elif active:
                    message = (
                        "Another paper window "
                        "is still open. Waiting."
                    )

                elif not proposed:
                    st.session_state.pop(
                        "qe_v4_confirm",
                        None,
                    )

                    message = (
                        "WAIT · "
                        + "; ".join(reasons)
                    )

                else:
                    stamp = (
                        now_utc()
                        .timestamp()
                    )

                    old = (
                        st.session_state.get(
                            "qe_v4_confirm"
                        )
                    )

                    same = (
                        old
                        and old["ticker"]
                        == ticker
                        and old["side"]
                        == proposed["side"]
                    )

                    restart_confirmation = (
                        not same
                        or stamp - old["time"] > 45
                        or proposed["distance"]
                        < old["distance"] * .75
                        or proposed["price"]
                        > old["price"] + .03
                    )

                    if restart_confirmation:
                        st.session_state[
                            "qe_v4_confirm"
                        ] = {
                            **proposed,
                            "ticker": ticker,
                            "time": stamp,
                        }

                        message = (
                            "WAIT · First eligible "
                            "quote. Waiting for "
                            "another observation "
                            "at least 10 seconds "
                            "later."
                        )

                    elif (
                        stamp - old["time"]
                        < 10
                    ):
                        message = (
                            "WAIT · Candidate has "
                            "not held for "
                            "10 seconds."
                        )

                    else:
                        current_seconds = (
                            close - now_utc()
                        ).total_seconds()

                        if (
                            not snapshot_current(
                                quote_at,
                                trade_time,
                            )
                            or current_seconds < 180
                        ):
                            raise RuntimeError(
                                "Snapshot or entry "
                                "window expired "
                                "before recording"
                            )

                        entry = {
                            **proposed,
                            "ticker": ticker,
                            "target": float(
                                fresh_market[
                                    "floor_strike"
                                ]
                            ),
                            "close_ts": (
                                close.timestamp()
                            ),
                            "observed_at": (
                                now_utc()
                                .isoformat()
                            ),
                            "trade_time": (
                                trade_time
                                .isoformat()
                            ),
                            "fee_multiplier": (
                                multiplier
                            ),
                            "fee_model": (
                                "Estimated quadratic "
                                "taker, whole-cent "
                                "assumed fills"
                            ),
                            "strategy": (
                                "V4-forward-v1"
                            ),
                            "take_profit_fraction": (
                                TAKE_PROFIT
                            ),
                            "loss_review_fraction": (
                                STOP_REVIEW
                            ),
                            "entry_cap": (
                                ENTRY_CAP
                            ),
                            "slippage": (
                                SLIPPAGE
                            ),
                        }

                        store.once(
                            "entries",
                            file_key,
                            entry,
                        )

                        st.session_state.pop(
                            "qe_v4_confirm",
                            None,
                        )

                        message = (
                            "PAPER ENTRY SAVED · "
                            f"{entry['side']} · "
                            f"{entry['qty']} contracts "
                            f"at {entry['price']*100:.0f}¢ "
                            f"· risk ${entry['risk']:.2f} "
                            "· settlement break-even "
                            f"{entry['breakeven']:.1%}. "
                            "No order was sent."
                        )

        else:
            st.session_state.pop(
                "qe_v4_confirm",
                None,
            )

            message = (
                "WAIT · Current contract is "
                "closed or inactive. Official "
                "outcomes remain pending "
                "until published."
            )

        st.info(message)

        st.caption(
            "Managed policy: review exit at "
            "+25% / -20% of entry risk, "
            "a target cross, opposing confirmed "
            "EMA trend, or ≤75 seconds remaining. "
            "Evaluated only while this dashboard "
            "is open, at its refresh interval. "
            "Stops are not guaranteed fills; "
            "slippage can exceed this assumption. "
            "Matched P&L compares the same "
            "officially settled entry cohort; "
            "early exits awaiting settlement "
            "appear in history. Latest 200 entries "
            "displayed; older events remain stored."
        )

        with st.expander(
            "V4 paper history "
            "· compare the same entries",
            expanded=False,
        ):
            display = []

            for row in rows:
                entry = row["entry"]
                final = row["final"]
                exit_event = row["exit"]

                observed = utc(
                    entry["observed_at"]
                )

                display.append(
                    {
                        "Observed CT": (
                            observed
                            .astimezone(CT)
                            .strftime(
                                "%m/%d %H:%M:%S"
                            )
                        ),
                        "Ticker": (
                            entry["ticker"]
                        ),
                        "Side": (
                            entry["side"]
                        ),
                        "Entry ¢": round(
                            entry["price"] * 100,
                            1,
                        ),
                        "Qty": entry["qty"],
                        "Entry risk $": (
                            entry["risk"]
                        ),
                        "Fee estimate $": (
                            entry["fee"]
                        ),
                        "Break-even": (
                            f"{entry['breakeven']:.1%}"
                        ),
                        "Managed net $": (
                            final["managed_pnl"]
                            if final
                            else exit_event["pnl"]
                            if exit_event
                            else None
                        ),
                        "Hold net $": (
                            final["hold_pnl"]
                            if final
                            else None
                        ),
                        "Official result": (
                            final["outcome"]
                            if final
                            else "PENDING"
                        ),
                        "Exit": (
                            final["exit_reason"]
                            if final
                            else exit_event["reason"]
                            if exit_event
                            else "OPEN"
                        ),
                    }
                )

            st.dataframe(
                display,
                hide_index=True,
                use_container_width=True,
            )

            st.caption(
                "Only observations recorded after "
                "enabling this test are entered. "
                "Historical chart arrows are never "
                "backfilled as trades. All fills "
                "and fees are estimates, not account "
                "executions. The V4 sample is "
                "separate from Early, Locked, "
                "WATCH and V2 history."
            )

    except Exception as exc:
        st.session_state.pop(
            "qe_v4_confirm",
            None,
        )

        st.warning(
            "V4 new entries / unverified exits "
            "are paused: "
            + str(exc)
        )

        st.caption(
            "Existing paper events are preserved. "
            "No real orders are placed."
        )


def launch():
    original = (
        Path(__file__).resolve().parent
        / "queens_original.py"
    )

    if not original.is_file():
        st.error(
            "Missing queens_original.py "
            "beside app.py"
        )
        st.stop()

    source = original.read_text(
        encoding="utf-8"
    )

    patches = [
        (
            'c.metric("Indicative distance", '
            'f"${abs(delta):,.2f}", '
            '"ABOVE" if delta > 0 else '
            '"BELOW" if delta < 0 else '
            '"AT TARGET")',

            'c.metric("Indicative distance", '
            'f"${abs(delta):,.2f}", '
            '"ABOVE" if delta > 0 else '
            '"BELOW" if delta < 0 else '
            '"AT TARGET", delta_color="off")',

            "distance arrow",
        ),

        (
            'side = st.radio("Side", '
            '["YES", "NO"], horizontal=True, '
            'key=f"qe_calc_side_{series}")',

            'side = st.radio('
            '"Side · manual paper calculator", '
            '["NONE", "YES", "NO"], horizontal=True, '
            'key=f"qe_calc_side_{series}")',

            "paper calculator default",
        ),

        (
            'ask = yes_ask if side == "YES" '
            'else no_ask',

            'ask = yes_ask if side == "YES" '
            'else no_ask if side == "NO" '
            'else None',

            "paper calculator safety",
        ),

        (
            '    st.markdown('
            '"#### 📈 Live Bitcoin price action")\n'
            '    frame = btc_candles()\n'
            '    qe_btc_chart(frame, strike)',

            '    qe_close = parse_time('
            'market.get("close_time") or '
            'market.get("latest_expiration_time")) '
            'if market else None\n'

            '    qe_seconds = '
            '(qe_close - datetime.now(timezone.utc))'
            '.total_seconds() if qe_close else None\n'

            '    render_paper_engine('
            'market, strike, qe_indicative_btc(), '
            'qe_seconds, '
            'verified_direction(market) '
            'if market else False, '
            'quote, btc_candles, '
            'bool(errors.get(series)))\n'

            '    with st.expander('
            '"🔍 Original V3 diagnostics", '
            'expanded=False):\n'

            '        render_v3_guard('
            'market, strike, qe_indicative_btc(), '
            'qe_seconds, '
            'verified_direction(market) '
            'if market else False, '
            'quote, btc_candles, '
            'bool(errors.get(series)))\n'

            '    st.markdown('
            '"#### 📈 LIVE BITCOIN · STREAMING CHART")\n'

            '    render_live_chart('
            'strike, close_time=qe_close, '
            'market_ticker=market.get("ticker") '
            'if market else None)\n'

            '    with st.expander('
            '"📉 Original historical chart · Backup", '
            'expanded=False):\n'

            '        frame = btc_candles()\n'
            '        qe_btc_chart(frame, strike)',

            "forward paper engine and live chart",
        ),
    ]

    for before, after, label in patches:
        if source.count(before) != 1:
            st.error(
                "Preview compatibility check failed: "
                + label
                + ". queens_original.py was not changed."
            )
            st.stop()

        source = source.replace(
            before,
            after,
            1,
        )

    namespace = {
        "__name__": "__main__",
        "__file__": str(original),
        "__package__": None,
        "render_live_chart": render_live_chart,
        "render_v3_guard": render_v3_guard,
        "render_paper_engine": render_paper_engine,
    }

    exec(
        compile(
            source,
            str(original),
            "exec",
        ),
        namespace,
    )


if __name__ == "__main__":
    launch()
