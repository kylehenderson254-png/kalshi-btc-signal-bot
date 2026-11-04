
"""QUEEN'S EDGE | 254 — V3 Selective Gate + Clock Diagnostics.

Experimental paper-only observation gate.

Features:
- Fresh Coinbase REST candle request
- Original dashboard candle fallback
- Server UTC clock diagnostics
- Coinbase HTTP Date clock diagnostics
- Candle timestamps and age comparisons
- Safe SKIP when data cannot be verified

No real orders are ever placed.
Coinbase spot is not Kalshi's verified settlement reference.
"""

from datetime import datetime, timezone
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


def _finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
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

        return value.astimezone(
            timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")

    except Exception:
        return "UNAVAILABLE"


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

    if any(x not in df.columns for x in required):
        return pd.DataFrame()

    raw_time = df["time"]

    if pd.api.types.is_numeric_dtype(raw_time):
        parsed = pd.to_datetime(
            raw_time,
            unit="s",
            utc=True,
            errors="coerce",
        )
    else:
        parsed = pd.to_datetime(
            raw_time,
            utc=True,
            errors="coerce",
        )

    df["time"] = parsed

    for column in ("close", "high", "low"):
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = (
        df.dropna(subset=list(required))
        .sort_values("time")
        .drop_duplicates(subset=["time"], keep="last")
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
        now = reference_time or datetime.now(timezone.utc)

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

    return (
        age is not None
        and -15 <= age <= MAX_CANDLE_AGE
    )


def _parse_http_date(value):
    if not value:
        return None

    try:
        parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except Exception:
        return None


@st.cache_data(ttl=12, show_spinner=False)
def fetch_coinbase_with_clock():
    """Fetch candles and capture response-clock diagnostics.

    The response is cached briefly, but freshness is always
    checked again using the current server time.
    """

    start_wall = datetime.now(timezone.utc)
    start_mono = time.monotonic()

    response = requests.get(
        COINBASE_URL,
        params={"granularity": 60},
        headers={
            "Accept": "application/json",
            "User-Agent": "QueensEdge254-PaperResearch/1.0",
        },
        timeout=8,
    )

    elapsed = time.monotonic() - start_mono
    received = datetime.now(timezone.utc)

    response.raise_for_status()

    http_date_raw = response.headers.get("Date")
    http_date = _parse_http_date(http_date_raw)

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

    if not rows:
        raise ValueError(
            "Coinbase returned no usable candles"
        )

    df = _clean_candles(pd.DataFrame(rows))

    clock = {
        "request_started": start_wall,
        "response_received": received,
        "request_seconds": elapsed,
        "http_date": http_date,
        "http_date_raw": http_date_raw,
        "row_count": len(df),
        "latest_candle": (
            df["time"].iloc[-1].to_pydatetime()
            if len(df)
            else None
        ),
        "earliest_candle": (
            df["time"].iloc[0].to_pydatetime()
            if len(df)
            else None
        ),
    }

    return df, clock


def _get_candles(candle_fn):
    candidates = []
    errors = []
    clock = {
        "server_now": datetime.now(timezone.utc),
        "coinbase": None,
        "original_latest": None,
        "original_age": None,
    }

    try:
        fresh_df, cb_clock = fetch_coinbase_with_clock()
        clock["coinbase"] = cb_clock

        age = _age(fresh_df)

        if _fresh(fresh_df):
            candidates.append((
                "Coinbase REST · 1-minute candles",
                fresh_df,
            ))
        else:
            errors.append(
                "Coinbase REST candles not fresh"
                + (
                    f" ({age:.0f}s old)"
                    if age is not None
                    else ""
                )
            )

    except Exception as exc:
        errors.append(
            f"Coinbase REST error: {exc}"
        )

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
            key=lambda x: x[1]["time"].iloc[-1],
        )

        return df, source, errors, clock

    return None, "NO FRESH CANDLE SOURCE", errors, clock


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
            "Contract or settlement mapping unverified"
        )

    if quote_stale:
        issues.append(
            "Kalshi market request failed; cached quote"
        )

    if strike is None or spot is None:
        issues.append(
            "Target or indicative spot unavailable"
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
        rsi = 100.0 if latest_gain > 0 else 50.0
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
    minimum_distance = max(
        50.0,
        spot * 0.0007,
    )

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

    server_now = datetime.now(timezone.utc)

    st.write(
        "**Streamlit server UTC now:**",
        _utc(server_now),
    )

    cb = clock.get("coinbase")

    if cb:
        st.write(
            "**Coinbase request started:**",
            _utc(cb.get("request_started")),
        )

        st.write(
            "**Coinbase response received:**",
            _utc(cb.get("response_received")),
        )

        st.write(
            "**Coinbase HTTP Date header:**",
            _utc(cb.get("http_date")),
        )

        st.write(
            "**Latest Coinbase candle:**",
            _utc(cb.get("latest_candle")),
        )

        st.write(
            "**Earliest Coinbase candle:**",
            _utc(cb.get("earliest_candle")),
        )

        st.write(
            "**Coinbase candle count:**",
            cb.get("row_count"),
        )

        request_seconds = cb.get("request_seconds")

        if request_seconds is not None:
            st.write(
                "**Coinbase request duration:**",
                f"{request_seconds:.2f}s",
            )

        latest = cb.get("latest_candle")

        if latest is not None:
            st.write(
                "**Latest candle age vs server:**",
                f"{(server_now - latest).total_seconds():.1f}s",
            )

        http_date = cb.get("http_date")

        if http_date is not None:
            st.write(
                "**Server minus Coinbase HTTP Date:**",
                f"{(server_now - http_date).total_seconds():.1f}s",
            )

            if latest is not None:
                st.write(
                    "**HTTP Date minus latest candle:**",
                    f"{(http_date - latest).total_seconds():.1f}s",
                )

        st.caption(
            "HTTP Date may reflect an intermediary or "
            "cached response. It is not necessarily an "
            "authoritative Coinbase clock measurement."
        )

    else:
        st.write(
            "**Coinbase clock details:**",
            "Unavailable — check feed errors below",
        )

    original_latest = clock.get("original_latest")

    st.write(
        "**Original feed latest candle:**",
        _utc(original_latest),
    )

    original_age = clock.get("original_age")

    if original_age is not None:
        st.write(
            "**Original candle age:**",
            f"{original_age:.1f}s",
        )

    st.caption(
        "Coinbase 1-minute candle timestamps represent "
        "the START of each minute. A healthy current "
        "candle may already be 0–60 seconds old. "
        "The safety threshold remains 180 seconds."
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
            st.markdown("**Filter reasons:**")

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
