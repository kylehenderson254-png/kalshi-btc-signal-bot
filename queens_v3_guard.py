
"""QUEEN'S EDGE | 254 — V3 Selective Gate + Feed Health.

PAPER ONLY. No order placement.

Uses:
- Coinbase standard 1-minute candles
- Coinbase explicit-window candle request
- Original dashboard candle fallback
- Coinbase latest-trade freshness cross-check
- UTC clock and HTTP cache diagnostics

A fresh trade does NOT make stale candles usable.
Coinbase is not the Kalshi settlement reference.
"""

from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import math
import time

import pandas as pd
import requests
import streamlit as st


OBSERVATION_START = 600
OBSERVATION_END = 180
MAX_CANDLE_AGE = 180
MIN_CANDLES = 35

COINBASE_URL = (
    "https://api.exchange.coinbase.com/"
    "products/BTC-USD/candles"
)

TRADE_URL = (
    "https://api.exchange.coinbase.com/"
    "products/BTC-USD/trades"
)

HEADERS = {
    "Accept": "application/json",
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
    "User-Agent": "QueensEdge254-PaperResearch/1.1",
}


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _utc(value):
    if value is None:
        return "UNAVAILABLE"

    try:
        if isinstance(value, (int, float)):
            value = datetime.fromtimestamp(
                value, timezone.utc
            )

        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    except Exception:
        return "UNAVAILABLE"


def _parse_http_date(value):
    if not value:
        return None

    try:
        result = parsedate_to_datetime(value)

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result.astimezone(timezone.utc)
    except Exception:
        return None


def _result(status, reasons=None, **extra):
    decision = (
        "PAPER CANDIDATE · REVIEW ONLY"
        if status == "CANDIDATE"
        else "SKIP / NO ENTRY"
    )

    return {
        "decision": decision,
        "status": status,
        "side": None,
        "reasons": reasons or [],
        "entry_ask": None,
        "distance": None,
        "rsi": None,
        "candle_age": None,
        "ema_trend": None,
        "seconds_left": None,
        "candle_source": None,
        **extra,
    }


def _clean_candles(candles):
    if candles is None:
        return pd.DataFrame()

    try:
        df = pd.DataFrame(candles).copy()
    except Exception:
        return pd.DataFrame()

    required = ("time", "close", "high", "low")

    if any(col not in df.columns for col in required):
        return pd.DataFrame()

    raw = df["time"]

    if pd.api.types.is_numeric_dtype(raw):
        numeric = pd.to_numeric(raw, errors="coerce")
        sample = numeric.dropna()

        if len(sample) == 0:
            return pd.DataFrame()

        # Support both Unix seconds and Unix milliseconds.
        unit = "ms" if sample.median() > 1e11 else "s"

        df["time"] = pd.to_datetime(
            numeric,
            unit=unit,
            utc=True,
            errors="coerce",
        )
    else:
        df["time"] = pd.to_datetime(
            raw,
            utc=True,
            errors="coerce",
        )

    for col in ("close", "high", "low"):
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = (
        df.dropna(subset=list(required))
        .sort_values("time")
        .drop_duplicates("time", keep="last")
        .reset_index(drop=True)
    )

    df = df[
        (df["high"] >= df["low"])
        & (df["close"] >= df["low"])
        & (df["close"] <= df["high"])
        & (df["close"] > 0)
    ]

    return df.reset_index(drop=True)


def _age(df, reference_time=None):
    if df is None or len(df) == 0:
        return None

    try:
        now = (
            reference_time
            if reference_time is not None
            else datetime.now(timezone.utc)
        )

        if isinstance(now, pd.Timestamp):
            now = now.to_pydatetime()

        latest = df["time"].iloc[-1].to_pydatetime()

        return (now - latest).total_seconds()
    except Exception:
        return None


def _fresh(df):
    if df is None or len(df) < MIN_CANDLES:
        return False

    age = _age(df)

    if age is None or not -15 <= age <= MAX_CANDLE_AGE:
        return False

    # Make sure the series actually contains recent
    # consecutive minute observations, not only an
    # isolated current timestamp.
    recent = df["time"].tail(5)

    if len(recent) < 5:
        return False

    gaps = recent.diff().dropna().dt.total_seconds()

    if gaps.empty or (gaps > 120).any():
        return False

    return True


@st.cache_data(ttl=10, show_spinner=False)
def fetch_coinbase_with_clock(mode="recent", bucket=None):
    """Fetch Coinbase 1-minute OHLCV.

    Two request shapes may reveal intermediary caching.
    They are NOT independent market-data providers.
    """

    started = datetime.now(timezone.utc)
    monotonic_start = time.monotonic()

    params = {"granularity": 60}

    if mode == "window":
        end = (
            started.replace(second=0, microsecond=0)
            + timedelta(minutes=1)
        )
        start = end - timedelta(minutes=180)

        params.update({
            "start": start.isoformat().replace(
                "+00:00", "Z"
            ),
            "end": end.isoformat().replace(
                "+00:00", "Z"
            ),
        })

    response = requests.get(
        COINBASE_URL,
        params=params,
        headers=HEADERS,
        timeout=8,
    )

    received = datetime.now(timezone.utc)
    elapsed = time.monotonic() - monotonic_start

    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, list):
        raise ValueError(
            "Unexpected Coinbase candle response"
        )

    rows = []

    for item in payload:
        if not isinstance(item, (list, tuple)):
            continue

        if len(item) < 6:
            continue

        try:
            rows.append({
                "time": float(item[0]),
                "low": float(item[1]),
                "high": float(item[2]),
                "open": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            })
        except (TypeError, ValueError):
            continue

    df = _clean_candles(
        pd.DataFrame(rows)
    ) if rows else pd.DataFrame()

    details = {
        "mode": mode,
        "request_started": started,
        "response_received": received,
        "request_seconds": elapsed,
        "http_date": _parse_http_date(
            response.headers.get("Date")
        ),
        "http_date_raw": response.headers.get("Date"),
        "cache_age": response.headers.get(
            "Age", "not supplied"
        ),
        "row_count": len(df),
        "latest_candle": (
            df["time"].iloc[-1].to_pydatetime()
            if len(df) else None
        ),
        "earliest_candle": (
            df["time"].iloc[0].to_pydatetime()
            if len(df) else None
        ),
    }

    return df, details


@st.cache_data(ttl=10, show_spinner=False)
def fetch_latest_trade(bucket=None):
    """Cross-check current market data.

    Trade timestamps are diagnostic only.
    They cannot validate old candle history.
    """

    started = datetime.now(timezone.utc)

    response = requests.get(
        TRADE_URL,
        params={"limit": 1},
        headers=HEADERS,
        timeout=8,
    )

    received = datetime.now(timezone.utc)
    response.raise_for_status()

    trades = response.json()

    if not isinstance(trades, list) or not trades:
        raise ValueError(
            "Coinbase returned no recent trades"
        )

    trade = trades[0]

    stamp = pd.to_datetime(
        trade.get("time"),
        utc=True,
        errors="coerce",
    )

    if pd.isna(stamp):
        raise ValueError(
            "Latest trade timestamp unavailable"
        )

    trade_time = stamp.to_pydatetime()

    return {
        "trade_time": trade_time,
        "trade_age": (
            received - trade_time
        ).total_seconds(),
        "request_started": started,
        "response_received": received,
        "http_date": _parse_http_date(
            response.headers.get("Date")
        ),
    }


def _get_candles(candle_fn):
    candidates = []
    errors = []

    clock = {
        "server_now": datetime.now(timezone.utc),
        "coinbase": None,
        "alternate": None,
        "trade": None,
        "trade_error": None,
        "original_latest": None,
        "original_age": None,
    }

    bucket = int(time.time() // 10)

    for mode, label, field in (
        (
            "recent",
            "Coinbase REST · standard request",
            "coinbase",
        ),
        (
            "window",
            "Coinbase REST · explicit time window",
            "alternate",
        ),
    ):
        try:
            df, details = fetch_coinbase_with_clock(
                mode, bucket
            )

            clock[field] = details
            age = _age(df)

            if _fresh(df):
                candidates.append((label, df))
            else:
                age_text = (
                    f"{age:.0f}s old"
                    if age is not None
                    else "unknown age"
                )

                errors.append(
                    f"{label} not fresh "
                    f"({age_text}, {len(df)} candles)"
                )

        except Exception as exc:
            errors.append(
                f"{label} error: {exc}"
            )

    try:
        clock["trade"] = fetch_latest_trade(bucket)
    except Exception as exc:
        clock["trade_error"] = str(exc)

        errors.append(
            f"Latest-trade cross-check unavailable: {exc}"
        )

    # Retain the original candle feed as a fallback.
    try:
        original_df = _clean_candles(candle_fn())

        if len(original_df):
            clock["original_latest"] = (
                original_df["time"]
                .iloc[-1]
                .to_pydatetime()
            )

        clock["original_age"] = _age(original_df)

        if _fresh(original_df):
            candidates.append((
                "Original dashboard candle feed",
                original_df,
            ))
        else:
            age = _age(original_df)

            errors.append(
                "Original dashboard candles not fresh"
                + (
                    f" ({age:.0f}s old)"
                    if age is not None
                    else ""
                )
            )

    except Exception as exc:
        errors.append(
            f"Original dashboard candle error: {exc}"
        )

    clock["server_now"] = datetime.now(timezone.utc)

    if candidates:
        source, df = max(
            candidates,
            key=lambda item: item[1]["time"].iloc[-1],
        )

        return df, source, errors, clock

    return (
        None,
        "NO FRESH CANDLE SOURCE",
        errors,
        clock,
    )


def assess(
    market,
    strike,
    spot,
    candles,
    seconds_left,
    verified,
    quote_fn,
    quote_stale=False,
    candle_source="Original dashboard candle feed",
):
    reasons = []
    issues = []

    strike = _finite(strike)
    spot = _finite(spot)
    seconds_left = _finite(seconds_left)

    if not market or not verified:
        issues.append(
            "Contract or YES/NO settlement mapping unverified"
        )

    if quote_stale:
        issues.append(
            "Kalshi market request failed; cached quote"
        )

    if strike is None or spot is None:
        issues.append(
            "Target or indicative spot price unavailable"
        )

    if seconds_left is None:
        issues.append(
            "Contract countdown unavailable"
        )

    if (
        seconds_left is not None
        and not (
            OBSERVATION_END
            <= seconds_left
            <= OBSERVATION_START
        )
    ):
        return _result(
            "WAITING",
            [
                "Outside observation window",
                "V3 checks from 10 to 3 minutes remaining",
            ],
            seconds_left=seconds_left,
            candle_source=candle_source,
        )

    if issues:
        return _result(
            "DATA ISSUE",
            issues,
            seconds_left=seconds_left,
            candle_source=candle_source,
        )

    df = _clean_candles(candles)

    if len(df) < MIN_CANDLES:
        return _result(
            "DATA ISSUE",
            ["Not enough usable Bitcoin candle history"],
            seconds_left=seconds_left,
            candle_source=candle_source,
        )

    candle_age = _age(df)

    if candle_age is None:
        issues.append(
            "Candle timestamp unavailable"
        )
    elif candle_age < -15:
        issues.append(
            "Candle timestamp ahead of server clock"
        )
    elif candle_age > MAX_CANDLE_AGE:
        issues.append(
            f"Underlying candles stale "
            f"({candle_age:.0f}s old)"
        )

    if not _fresh(df):
        issues.append(
            "Candle freshness or continuity check failed"
        )

    close = df["close"]

    ema9 = close.ewm(
        span=9,
        adjust=False,
    ).mean()

    ema21 = close.ewm(
        span=21,
        adjust=False,
    ).mean()

    change = close.diff()

    gain = change.clip(lower=0).ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    loss = (-change.clip(upper=0)).ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    latest_gain = _finite(gain.iloc[-1])
    latest_loss = _finite(loss.iloc[-1])

    if latest_gain is None or latest_loss is None:
        rsi = None
        issues.append(
            "RSI calculation unavailable"
        )
    elif latest_loss == 0:
        rsi = (
            100.0 if latest_gain > 0 else 50.0
        )
    else:
        rsi = 100 - (
            100 / (1 + latest_gain / latest_loss)
        )

    if spot > strike:
        direction = "ABOVE"
    elif spot < strike:
        direction = "BELOW"
    else:
        direction = None

    distance = abs(spot - strike)

    minimum_distance = 25.0  # Paper-test threshold; other filters remain active

    if direction is None:
        reasons.append(
            "Bitcoin exactly at target"
        )
    elif distance < minimum_distance:
        reasons.append(
            f"Too close to target: ${distance:.2f} "
            f"(minimum ${minimum_distance:.2f})"
        )

    rising = (
        ema9.iloc[-1] > ema21.iloc[-1]
        and ema9.iloc[-1] > ema9.iloc[-4]
        and close.iloc[-1] > close.iloc[-4]
    )

    falling = (
        ema9.iloc[-1] < ema21.iloc[-1]
        and ema9.iloc[-1] < ema9.iloc[-4]
        and close.iloc[-1] < close.iloc[-4]
    )

    if rising:
        ema_trend = "UPWARD"
    elif falling:
        ema_trend = "DOWNWARD"
    else:
        ema_trend = "MIXED / UNCONFIRMED"

    if direction == "ABOVE":
        if not rising:
            reasons.append(
                "UP momentum / EMA confirmation missing"
            )

        if rsi is None or not 53 <= rsi <= 68:
            reasons.append(
                "UP RSI outside 53–68 range"
            )

        if (
            close.iloc[-1]
            < close.iloc[-2]
            < close.iloc[-3]
        ):
            reasons.append(
                "Recent bearish reversal warning"
            )

    elif direction == "BELOW":
        if not falling:
            reasons.append(
                "DOWN momentum / EMA confirmation missing"
            )

        if rsi is None or not 32 <= rsi <= 47:
            reasons.append(
                "DOWN RSI outside 32–47 range"
            )

        if (
            close.iloc[-1]
            > close.iloc[-2]
            > close.iloc[-3]
        ):
            reasons.append(
                "Recent bullish reversal warning"
            )

    bid = None
    ask = None

    if direction is not None:
        side = (
            "yes" if direction == "ABOVE" else "no"
        )

        try:
            bid = _finite(
                quote_fn(market, side, "bid")
            )
            ask = _finite(
                quote_fn(market, side, "ask")
            )
        except Exception:
            issues.append(
                "Unable to read Kalshi quote"
            )

        if (
            bid is None
            or ask is None
            or not 0 < bid <= ask < 1
        ):
            issues.append(
                "Invalid Kalshi executable-side quote"
            )
        else:
            if ask > 0.79:
                reasons.append(
                    f"Contract too expensive: "
                    f"{ask * 100:.1f}¢ "
                    "(maximum 79¢)"
                )

            if ask - bid > 0.03:
                reasons.append(
                    "Bid/ask spread too wide: "
                    f"{(ask - bid) * 100:.1f}¢ "
                    "(maximum 3¢)"
                )

    details = {
        "side": direction,
        "entry_ask": ask,
        "distance": distance,
        "rsi": rsi,
        "candle_age": candle_age,
        "ema_trend": ema_trend,
        "seconds_left": seconds_left,
        "minimum_distance": minimum_distance,
        "candle_source": candle_source,
    }

    if issues:
        return _result(
            "DATA ISSUE",
            issues + reasons,
            **details,
        )

    if reasons:
        return _result(
            "FILTERED",
            reasons,
            **details,
        )

    return _result(
        "CANDIDATE",
        [],
        **details,
    )


def _show_clock_diagnostics(clock):
    st.markdown(
        "**🕒 Candle Clock Diagnostics**"
    )

    now = datetime.now(timezone.utc)

    st.write(
        "**Streamlit server UTC now:**",
        _utc(now),
    )

    for field, heading in (
        (
            "coinbase",
            "Coinbase standard candles",
        ),
        (
            "alternate",
            "Coinbase explicit-window candles",
        ),
    ):
        details = clock.get(field)

        st.markdown(f"**{heading}**")

        if details is None:
            st.write(
                "Unavailable / not checked"
            )
            continue

        st.write(
            "Request started:",
            _utc(details.get("request_started")),
        )

        st.write(
            "Response received:",
            _utc(details.get("response_received")),
        )

        st.write(
            "HTTP Date:",
            _utc(details.get("http_date")),
        )

        st.write(
            "Latest candle:",
            _utc(details.get("latest_candle")),
        )

        st.write(
            "Earliest candle:",
            _utc(details.get("earliest_candle")),
        )

        st.write(
            "Candle count:",
            details.get("row_count"),
        )

        st.write(
            "Request duration:",
            f"{details.get('request_seconds', 0):.2f}s",
        )

        st.write(
            "HTTP Age header:",
            details.get("cache_age"),
        )

        latest = details.get("latest_candle")

        if latest is not None:
            st.write(
                "Latest candle age vs server:",
                f"{(now - latest).total_seconds():.1f}s",
            )

        http_date = details.get("http_date")

        if http_date is not None:
            st.write(
                "Server minus HTTP Date:",
                f"{(now - http_date).total_seconds():.1f}s",
            )

    st.markdown(
        "**Coinbase latest-trade endpoint · cross-check**"
    )

    trade = clock.get("trade")

    if trade:
        st.write(
            "Latest trade UTC:",
            _utc(trade.get("trade_time")),
        )

        st.write(
            "Latest trade age at request:",
            f"{trade.get('trade_age', 0):.1f}s",
        )

        st.write(
            "Trade response received:",
            _utc(trade.get("response_received")),
        )
    else:
        st.write(
            "Unavailable:",
            clock.get("trade_error")
            or "Not checked",
        )

    st.markdown(
        "**Original dashboard candle feed**"
    )

    st.write(
        "Latest candle:",
        _utc(clock.get("original_latest")),
    )

    original_age = clock.get("original_age")

    if original_age is not None:
        st.write(
            "Candle age:",
            f"{original_age:.1f}s",
        )

    st.caption(
        "Candle timestamps represent the start "
        "of each minute. A recent trade does NOT "
        "make old candles fresh. HTTP Date and "
        "cache headers may come from intermediaries. "
        "V3 retains the 180-second safety threshold."
    )


def render_v3_guard(
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
        "#### 👑 V3 Selective Gate · Experimental / Paper Only"
    )

    seconds = _finite(seconds_left)

    in_window = (
        seconds is not None
        and OBSERVATION_END
        <= seconds
        <= OBSERVATION_START
    )

    candles = None
    source = "NOT CHECKED"
    feed_errors = []

    clock = {
        "server_now": datetime.now(timezone.utc),
        "coinbase": None,
        "alternate": None,
        "trade": None,
        "trade_error": None,
        "original_latest": None,
        "original_age": None,
    }

    if in_window:
        (
            candles,
            source,
            feed_errors,
            clock,
        ) = _get_candles(candle_fn)

    try:
        result = assess(
            market=market,
            strike=strike,
            spot=spot,
            candles=candles,
            seconds_left=seconds_left,
            verified=verified,
            quote_fn=quote_fn,
            quote_stale=quote_stale,
            candle_source=source,
        )

    except Exception as exc:
        st.warning(
            "🛑 DATA ISSUE · SKIP / NO ENTRY"
        )
        st.caption(
            f"V3 assessment unavailable: {exc}"
        )
        return

    if not in_window:
        st.caption(
            "📡 FEED HEALTH: NOT CHECKED · "
            "Waiting for 10–3 minute window"
        )

    elif candles is None:
        st.error(
            "📡 FEED HEALTH: STALE / UNAVAILABLE · "
            "V3 blocked"
        )

    else:
        live_age = _age(candles)

        if live_age is not None and _fresh(candles):
            st.success(
                f"📡 FEED HEALTH: FRESH · {source} · "
                f"latest candle {live_age:.0f}s old"
            )
        else:
            st.error(
                "📡 FEED HEALTH: STALE / UNAVAILABLE · "
                "V3 blocked"
            )

    status = result["status"]

    if status == "WAITING":
        st.info(
            "⏳ WAITING · OUTSIDE OBSERVATION WINDOW"
        )

    elif status == "DATA ISSUE":
        st.warning(
            "🛑 DATA ISSUE · SKIP / NO ENTRY"
        )

    elif status == "FILTERED":
        st.warning(
            "🟡 FILTERED · SKIP / NO ENTRY"
        )

    elif status == "CANDIDATE":
        st.success(
            "🔎 PAPER CANDIDATE · REVIEW ONLY · "
            f"{result['side']}"
        )

    for reason in result["reasons"]:
        st.caption(f"• {reason}")

    if in_window and candles is None:
        st.caption(
            "• No fresh candle source available"
        )

    with st.expander(
        "🔍 V3 Diagnostics · Why did it decide this?",
        expanded=False,
    ):
        st.write(
            "**Gate status:**",
            status,
        )

        remaining = result.get("seconds_left")

        if remaining is not None:
            st.write(
                "**Time remaining:**",
                f"{remaining / 60:.1f} minutes",
            )

        st.write(
            "**Observation window:**",
            "10 to 3 minutes remaining",
        )

        st.write(
            "**Candle source:**",
            source,
        )

        age = result.get("candle_age")

        if age is not None:
            st.write(
                "**Last candle age:**",
                f"{age:.0f}s",
            )

        st.write(
            "**Direction:**",
            result.get("side")
            or "UNDETERMINED",
        )

        distance = result.get("distance")

        if distance is not None:
            st.write(
                "**Distance to target:**",
                f"${distance:.2f}",
            )

        st.write(
            "**EMA / momentum trend:**",
            result.get("ema_trend")
            or "UNAVAILABLE",
        )

        rsi_value = result.get("rsi")

        if rsi_value is not None:
            st.write(
                "**RSI (14):**",
                f"{rsi_value:.1f}",
            )

        ask = result.get("entry_ask")

        if ask is not None:
            st.write(
                "**Kalshi entry ask:**",
                f"{ask * 100:.1f}¢",
            )

        st.write(
            "**Decision:**",
            result["decision"],
        )

        st.divider()

        _show_clock_diagnostics(clock)

        if feed_errors:
            st.markdown(
                "**Candle feed diagnostics:**"
            )

            for message in feed_errors:
                st.write(f"• {message}")

        if result["reasons"]:
            st.markdown(
                "**Filter reasons:**"
            )

            for reason in result["reasons"]:
                st.write(f"• {reason}")

    st.caption(
        "V3 is an experimental observation filter. "
        "A PAPER CANDIDATE is not a validated trade "
        "signal or proof of positive expected value. "
        "Coinbase prices may differ from Kalshi settlement. "
        "No real orders are placed."
    )

    st.caption(
        "Existing Early, Locked, and V2 paper trackers "
        "remain unchanged."
    )
