
"""QUEEN'S EDGE | 254 — V3 Selective Gate.

Experimental paper-only observation gate.
Never places real orders.

Coinbase spot and candles are indicative and may differ
from Kalshi's official settlement reference.
"""

from datetime import datetime, timezone
import math

import pandas as pd
import streamlit as st


OBSERVATION_START = 600
OBSERVATION_END = 180
MAX_CANDLE_AGE = 180
MIN_CANDLES = 35


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
        **extra,
    }


def assess(
    market,
    strike,
    spot,
    candles,
    seconds_left,
    verified,
    quote_fn,
    quote_stale=False,
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
    elif not OBSERVATION_END <= seconds_left <= OBSERVATION_START:
        return _result(
            "WAITING",
            [
                "Outside observation window",
                "V3 checks from 10 to 3 minutes remaining",
            ],
            seconds_left=seconds_left,
        )

    if candles is None or len(candles) < MIN_CANDLES:
        data_issues.append(
            "Not enough Bitcoin candle history"
        )

    if data_issues:
        return _result(
            "DATA ISSUE",
            data_issues,
            seconds_left=seconds_left,
        )

    df = candles.copy()

    required = ("time", "close", "high", "low")
    missing = [col for col in required if col not in df.columns]

    if missing:
        return _result(
            "DATA ISSUE",
            ["Missing candle columns: " + ", ".join(missing)],
        )

    df["time"] = pd.to_datetime(
        df["time"],
        utc=True,
        errors="coerce",
    )

    for column in ("close", "high", "low"):
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = (
        df.dropna(subset=list(required))
        .sort_values("time")
        .drop_duplicates(subset=["time"], keep="last")
    )

    if len(df) < MIN_CANDLES:
        return _result(
            "DATA ISSUE",
            ["Incomplete candle history after cleaning"],
        )

    last_time = df["time"].iloc[-1]
    now = datetime.now(timezone.utc)

    candle_age = (
        now - last_time.to_pydatetime()
    ).total_seconds()

    # The latest candle may be the currently forming candle.
    # Future timestamps beyond a small clock tolerance are
    # considered invalid.
    if candle_age < -15:
        data_issues.append(
            "Candle timestamp is ahead of server time"
        )
    elif candle_age > MAX_CANDLE_AGE:
        data_issues.append(
            f"Underlying candle feed is stale "
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

    gain = (
        change.clip(lower=0)
        .ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        )
        .mean()
    )

    loss = (
        (-change.clip(upper=0))
        .ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        )
        .mean()
    )

    latest_gain = _finite(gain.iloc[-1])
    latest_loss = _finite(loss.iloc[-1])

    if latest_gain is None or latest_loss is None:
        rsi = None
        data_issues.append("RSI calculation unavailable")
    elif latest_loss == 0:
        rsi = 100.0 if latest_gain > 0 else 50.0
    else:
        relative_strength = latest_gain / latest_loss
        rsi = 100 - (100 / (1 + relative_strength))

    if spot > strike:
        direction = "ABOVE"
    elif spot < strike:
        direction = "BELOW"
    else:
        direction = None

    distance = abs(spot - strike)
    minimum_distance = max(50.0, spot * 0.0007)

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

        if close.iloc[-1] < close.iloc[-2] < close.iloc[-3]:
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

        if close.iloc[-1] > close.iloc[-2] > close.iloc[-3]:
            reasons.append(
                "Recent bullish reversal warning"
            )

    bid = None
    ask = None

    if direction is not None:
        side = "yes" if direction == "ABOVE" else "no"

        try:
            bid = _finite(quote_fn(market, side, "bid"))
            ask = _finite(quote_fn(market, side, "ask"))
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
                    f"Contract too expensive: {ask * 100:.1f}¢ "
                    "(maximum 79¢)"
                )

            if ask - bid > 0.03:
                reasons.append(
                    f"Bid/ask spread too wide: "
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

    try:
        candles = candle_fn()

        result = assess(
            market=market,
            strike=strike,
            spot=spot,
            candles=candles,
            seconds_left=seconds_left,
            verified=verified,
            quote_fn=quote_fn,
            quote_stale=quote_stale,
        )

    except Exception as exc:
        st.warning("🛑 DATA ISSUE · SKIP / NO ENTRY")
        st.caption(f"V3 assessment unavailable: {exc}")
        return

    status = result["status"]

    if status == "WAITING":
        st.info("⏳ WAITING · OUTSIDE OBSERVATION WINDOW")

    elif status == "DATA ISSUE":
        st.warning("🛑 DATA ISSUE · SKIP / NO ENTRY")

    elif status == "FILTERED":
        st.warning("🟡 FILTERED · SKIP / NO ENTRY")

    elif status == "CANDIDATE":
        st.success(
            f"🔎 PAPER CANDIDATE · REVIEW ONLY · "
            f"{result['side']}"
        )

    if result["reasons"]:
        for reason in result["reasons"]:
            st.caption(f"• {reason}")

    with st.expander(
        "🔍 V3 Diagnostics · Why did it decide this?",
        expanded=False,
    ):
        st.write("**Gate status:**", status)

        seconds = result.get("seconds_left")
        if seconds is not None:
            st.write(
                "**Time remaining:**",
                f"{seconds / 60:.1f} minutes",
            )

        st.write(
            "**Observation window:**",
            "10 to 3 minutes remaining",
        )

        st.write(
            "**Direction:**",
            result.get("side") or "UNDETERMINED",
        )

        distance = result.get("distance")
        if distance is not None:
            st.write(
                "**Distance to target:**",
                f"${distance:.2f}",
            )

        candle_age = result.get("candle_age")
        if candle_age is not None:
            st.write(
                "**Last candle age:**",
                f"{candle_age:.0f} seconds",
            )

        st.write(
            "**EMA / momentum trend:**",
            result.get("ema_trend") or "UNAVAILABLE",
        )

        rsi = result.get("rsi")
        if rsi is not None:
            st.write(
                "**RSI (14):**",
                f"{rsi:.1f}",
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

    st.caption(
        "V3 is an experimental observation filter. "
        "A PAPER CANDIDATE is not a validated trade signal "
        "or proof of positive expected value. "
        "Coinbase prices may differ from Kalshi settlement. "
        "No real orders are placed."
    )

    st.caption(
        "Existing Early, Locked, and V2 paper trackers "
        "remain unchanged."
    )
