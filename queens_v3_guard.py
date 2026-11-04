
"""QUEEN'S EDGE | 254 — V3 Selective Gate.

Fresh Coinbase 1-minute candle feed with timestamp validation.
Experimental observation filter. PAPER ONLY.

Never places orders.
Coinbase prices may differ from Kalshi's settlement reference.
"""

from datetime import datetime, timezone
import math

import pandas as pd
import requests
import streamlit as st


OBSERVATION_START = 600
OBSERVATION_END = 180
MAX_CANDLE_AGE = 180
MIN_CANDLES = 35

COINBASE_CANDLES_URL = (
    "https://api.exchange.coinbase.com/"
    "products/BTC-USD/candles"
)


def _finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _result(status, reasons=None, **extra):
    reasons = reasons or []

    decision = (
        "PAPER CANDIDATE · REVIEW ONLY"
        if status == "CANDIDATE"
        else "SKIP / NO ENTRY"
    )

    return {
        "decision": decision,
        "status": status,
        "side": None,
        "reasons": reasons,
        "entry_ask": None,
        "distance": None,
        "rsi": None,
        "candle_age": None,
        "ema_trend": None,
        "candle_source": None,
        "seconds_left": None,
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
    if any(column not in df.columns for column in required):
        return pd.DataFrame()

    # Handle datetime strings, datetime objects, and Unix seconds.
    times = df["time"]

    if pd.api.types.is_numeric_dtype(times):
        parsed = pd.to_datetime(
            times,
            unit="s",
            utc=True,
            errors="coerce",
        )
    else:
        parsed = pd.to_datetime(
            times,
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

    # Reject malformed OHLC values.
    df = df[
        (df["high"] >= df["low"])
        & (df["close"] >= df["low"])
        & (df["close"] <= df["high"])
    ]

    return df.reset_index(drop=True)


def _candle_age(df):
    if df is None or len(df) == 0:
        return None

    try:
        last_time = df["time"].iloc[-1]
        return (
            datetime.now(timezone.utc)
            - last_time.to_pydatetime()
        ).total_seconds()
    except Exception:
        return None


def _fresh_enough(df):
    if df is None or len(df) < MIN_CANDLES:
        return False

    age = _candle_age(df)

    return (
        age is not None
        and -15 <= age <= MAX_CANDLE_AGE
    )


@st.cache_data(ttl=12, show_spinner=False)
def fetch_fresh_coinbase_candles():
    """Fetch recent Coinbase BTC/USD 1-minute OHLCV candles.

    Streamlit caches a successful response for 12 seconds.
    The response is validated again before use.
    """

    response = requests.get(
        COINBASE_CANDLES_URL,
        params={"granularity": 60},
        headers={
            "Accept": "application/json",
            "User-Agent": "QueensEdge254-PaperResearch/1.0",
        },
        timeout=6,
    )

    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, list):
        raise ValueError("Unexpected Coinbase candle response")

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
        raise ValueError("Coinbase returned no usable candles")

    df = _clean_candles(pd.DataFrame(rows))

    if len(df) < MIN_CANDLES:
        raise ValueError(
            "Coinbase returned insufficient candle history"
        )

    return df


def _get_best_candles(candle_fn):
    """Choose the freshest valid candle history.

    The original dashboard candle source is retained as a
    fallback. Neither source bypasses freshness checks.
    """

    candidates = []
    errors = []

    # Fetch a fresh independent server-side candle series.
    try:
        fresh = fetch_fresh_coinbase_candles()

        if _fresh_enough(fresh):
            candidates.append((
                "Coinbase REST · fresh 1-minute candles",
                fresh,
            ))
        else:
            age = _candle_age(fresh)
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
            f"Coinbase REST unavailable: {exc}"
        )

    # Preserve compatibility with the original dashboard.
    try:
        original = _clean_candles(candle_fn())

        if _fresh_enough(original):
            candidates.append((
                "Original dashboard candle feed",
                original,
            ))
        else:
            age = _candle_age(original)
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
            f"Original candle feed unavailable: {exc}"
        )

    if candidates:
        # Select the newest valid candle, not merely the
        # source that was checked first.
        source, df = max(
            candidates,
            key=lambda item: item[1]["time"].iloc[-1],
        )

        return df, source, errors

    return None, "NO FRESH CANDLE SOURCE", errors


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
    data_issues = []

    strike = _finite(strike)
    spot = _finite(spot)
    seconds_left = _finite(seconds_left)

    if not market or not verified:
        data_issues.append(
            "Contract or YES/NO settlement mapping unverified"
        )

    if quote_stale:
        data_issues.append(
            "Kalshi market request failed; cached quote"
        )

    if strike is None or spot is None:
        data_issues.append(
            "Target or indicative spot price unavailable"
        )

    if seconds_left is None:
        data_issues.append(
            "Contract countdown unavailable"
        )
    elif not (
        OBSERVATION_END
        <= seconds_left
        <= OBSERVATION_START
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

    if data_issues:
        return _result(
            "DATA ISSUE",
            data_issues,
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

    candle_age = _candle_age(df)

    if candle_age is None:
        data_issues.append(
            "Unable to verify candle timestamp"
        )
    elif candle_age < -15:
        data_issues.append(
            "Candle timestamp is ahead of server time"
        )
    elif candle_age > MAX_CANDLE_AGE:
        data_issues.append(
            "Underlying candle feed is stale "
            f"({candle_age:.0f} seconds old)"
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
        data_issues.append(
            "RSI calculation unavailable"
        )
    elif latest_loss == 0:
        rsi = 100.0 if latest_gain > 0 else 50.0
    else:
        relative_strength = (
            latest_gain / latest_loss
        )
        rsi = 100 - (
            100 / (1 + relative_strength)
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
            "Bitcoin is exactly at the target"
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
                "UP RSI outside required 53–68 range"
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
                "DOWN RSI outside required 32–47 range"
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
            "yes"
            if direction == "ABOVE"
            else "no"
        )

        try:
            bid = _finite(
                quote_fn(market, side, "bid")
            )
            ask = _finite(
                quote_fn(market, side, "ask")
            )
        except Exception:
            data_issues.append(
                "Unable to read Kalshi executable-side quote"
            )

        if (
            bid is None
            or ask is None
            or not 0 < bid <= ask < 1
        ):
            data_issues.append(
                "Invalid executable-side Kalshi quote"
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

    if data_issues:
        return _result(
            "DATA ISSUE",
            data_issues + reasons,
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

    # Don't fetch candles when the gate cannot evaluate
    # the contract's observation window.
    in_window = (
        seconds is not None
        and OBSERVATION_END
        <= seconds
        <= OBSERVATION_START
    )

    candles = None
    candle_source = "NOT CHECKED"
    feed_errors = []

    if in_window:
        candles, candle_source, feed_errors = (
            _get_best_candles(candle_fn)
        )

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
            candle_source=candle_source,
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

    if result["reasons"]:
        for reason in result["reasons"]:
            st.caption(f"• {reason}")

    if (
        in_window
        and candles is None
        and feed_errors
    ):
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
            result.get("candle_source")
            or "UNAVAILABLE",
        )

        age = result.get("candle_age")

        if age is not None:
            st.write(
                "**Last candle age:**",
                f"{age:.0f} seconds",
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

        if result["reasons"]:
            st.write("**Reasons:**")
            for reason in result["reasons"]:
                st.write(f"• {reason}")

        if feed_errors:
            st.write("**Candle feed diagnostics:**")
            for message in feed_errors:
                st.write(f"• {message}")

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
