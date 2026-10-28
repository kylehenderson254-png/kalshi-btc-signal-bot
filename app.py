"""QUEEN'S EDGE | 254 — BTC + WTI OIL COMMAND CENTER.

Read-only Kalshi dashboard. All entries are paper-only.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import base64
import re
import time

import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from call_tracker import (
    store_from_secrets,
    process,
    early_store_from_secrets,
    process_early,
)
from pressure_radar import show_pressure_radar
from dual_strong_lock import (
    from_secrets as dual_store_from_secrets,
    analyze as dual_analyze,
)
from dual_strong_lock_v2 import analyze as dual_v2_analyze
from paper_tracker import (
    from_secrets as paper_store_from_secrets,
    grade_pending as paper_grade_pending,
)
from strong_lock_tracker import (
    from_secrets as strong_store_from_secrets,
    evaluate as strong_evaluate,
    grade_pending as strong_grade_pending,
)


# ============================================================
# CONFIGURATION
# ============================================================

API = "https://api.elections.kalshi.com/trade-api/v2"
CENTRAL = ZoneInfo("America/Chicago")

SERIES = [
    ("Bitcoin", "KXBTC15M", "₿"),
    ("WTI Oil", "KXWTI15M", "🛢️"),
]

STAGES = [
    ("EARLY_10M", "10m Early", 540, 660),
    ("FINAL_5M", "5m Final", 240, 360),
]

st.set_page_config(
    page_title="QUEEN'S EDGE | 254",
    page_icon="👑",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ============================================================
# STYLING — FULL LOGO, NO CROPPING
# ============================================================

st.markdown(
    """
    <style>
    .stApp {
        background: radial-gradient(
            ellipse at top,
            #21113f 0%,
            #0b091a 48%,
            #05050c 100%
        );
        color: #eee9ff;
    }

    .block-container {
        max-width: 1800px;
        padding-top: 0.8rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
        padding-bottom: 2rem;
    }

    h1, h2, h3 {
        color: #dfc7ff !important;
    }

    [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #6540a3 !important;
        border-radius: 15px !important;
    }

    .queen-title {
        text-align: center;
        font-size: clamp(1.7rem, 3vw, 2.7rem);
        font-weight: 900;
        color: #e0b9ff;
        letter-spacing: .05em;
    }

    .queen-subtitle {
        text-align: center;
        color: #b8a2e7;
        letter-spacing: .10em;
        margin-bottom: .5rem;
    }

    .queen-banner {
        width: 100%;
        height: auto;
        max-height: none;
        object-fit: contain;
        object-position: center;
        display: block;
        margin: 0 auto 12px auto;
        border-radius: 15px;
    }

    .quote-box {
        border-radius: 12px;
        padding: 12px;
        border: 1px solid;
        text-align: center;
        min-height: 105px;
    }

    .above {
        background: rgba(20,150,90,.12);
        border-color: #198e60;
        color: #75efb3;
    }

    .below {
        background: rgba(190,45,75,.12);
        border-color: #b63d5a;
        color: #ffa3b7;
    }

    .quote-price {
        font-size: 1.2rem;
        font-weight: 800;
    }

    div[data-testid="stMetric"] {
        background: rgba(111,67,178,.10);
        border: 1px solid rgba(166,119,230,.25);
        border-radius: 12px;
        padding: 12px;
    }

    div[data-testid="stMetricValue"] {
        font-size: 1.45rem;
    }

    @media (max-width: 700px) {
        .block-container {
            padding-left: .65rem;
            padding-right: .65rem;
        }

        .quote-box {
            padding: 8px;
            min-height: 95px;
        }

        .quote-price {
            font-size: 1rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# SESSION STATE
# ============================================================

for key, default in {
    "quote_history": {},
    "radar_events": [],
    "last_good_markets": {},
    "last_good_times": {},
    "strong_attempts": {},
    "dual_attempts": {},
    "dual_save_status": {},
    "dual_last_reason": {},
    "dual_monitor": {},
    "dual_monitor_seen": {},
    "dual_monitor_events": [],
    "dual_v2_shadow_entries": {},
    "dual_v2_monitor_events": [],
    "dual_v2_monitor_seen": {},
}.items():
    st.session_state.setdefault(key, default)


# ============================================================
# HELPERS
# ============================================================

def number(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_time(value):
    if not value:
        return None

    try:
        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result
    except (ValueError, TypeError):
        return None


def quote(market, side, kind):
    dollars = number(
        market.get(f"{side}_{kind}_dollars")
    )

    if dollars is not None:
        return dollars

    value = number(market.get(f"{side}_{kind}"))

    return value / 100 if value is not None else None


def midpoint(market):
    bid = quote(market, "yes", "bid")
    ask = quote(market, "yes", "ask")

    if (
        bid is not None
        and ask is not None
        and 0 <= bid <= ask <= 1
    ):
        return (bid + ask) / 2

    return None


def cents(value):
    return (
        f"{value * 100:.1f}¢"
        if value is not None
        else "—"
    )


def target(market):
    for key in (
        "floor_strike",
        "functional_strike",
        "custom_strike",
        "cap_strike",
    ):
        value = number(market.get(key))

        if value is not None:
            return f"${value:,.3f}"

    return str(
        market.get("yes_sub_title") or "See rules"
    )


def verified_direction(market):
    text = " ".join(
        str(market.get(key) or "")
        for key in (
            "rules_primary",
            "rules_secondary",
            "yes_sub_title",
        )
    ).lower()

    patterns = (
        r"if the close price.*?is at least.*?resolves to yes",
        r"if.*?simple average.*?is at least.*?resolves to yes",
        r"yes\s+(?:if|means).*?(?:above|higher|greater)",
        r"(?:above|higher|greater).*?(?:settles|resolves) to yes",
    )

    return any(
        re.search(pattern, text)
        for pattern in patterns
    )


def market_series(row):
    series = str(
        row.get("series_ticker") or ""
    ).upper()

    ticker = str(
        row.get("ticker") or ""
    ).upper()

    name = str(
        row.get("market_name")
        or row.get("market")
        or ""
    ).lower()

    if (
        series == "KXBTC15M"
        or ticker.startswith("KXBTC15M-")
        or "bitcoin" in name
    ):
        return "KXBTC15M"

    if (
        series == "KXWTI15M"
        or ticker.startswith("KXWTI15M-")
        or "wti" in name
        or "oil" in name
    ):
        return "KXWTI15M"

    return None


def btc_oil_rows(rows):
    return [
        row for row in rows
        if market_series(row) is not None
    ]


# ============================================================
# KALSHI MARKET DATA
# ============================================================

@st.cache_data(ttl=1, show_spinner=False)
def fetch_markets(series):
    response = requests.get(
        f"{API}/markets",
        params={
            "series_ticker": series,
            "status": "open",
            "limit": 200,
        },
        timeout=7,
    )

    response.raise_for_status()
    return response.json().get("markets", [])


@st.cache_data(ttl=60, show_spinner=False)
def fetch_recent(series):
    response = requests.get(
        f"{API}/markets",
        params={
            "series_ticker": series,
            "status": "settled",
            "limit": 100,
        },
        timeout=8,
    )

    response.raise_for_status()

    rows = [
        item
        for item in response.json().get("markets", [])
        if str(item.get("result", "")).lower()
        in ("yes", "no")
    ]

    rows.sort(
        key=lambda item: (
            parse_time(item.get("close_time"))
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True,
    )

    return rows[:10]


def current_market(markets):
    now = datetime.now(timezone.utc)

    eligible = [
        market
        for market in markets
        if parse_time(market.get("close_time")) is None
        or parse_time(market.get("close_time")) > now
    ]

    if not eligible:
        return None

    active = [
        market
        for market in eligible
        if parse_time(market.get("open_time")) is None
        or parse_time(market.get("open_time")) <= now
    ]

    return min(
        active or eligible,
        key=lambda market: (
            parse_time(market.get("close_time"))
            or datetime.max.replace(
                tzinfo=timezone.utc
            )
        ),
    )


def fetch_series(series):
    try:
        return (
            series,
            current_market(fetch_markets(series)),
            None,
        )
    except Exception as exc:
        return series, None, str(exc)


def active_markets():
    results = {}
    errors = {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(fetch_series, series)
            for _, series, _ in SERIES
        ]

        for future in futures:
            series, market, error = future.result()

            if market is not None:
                results[series] = market


                st.session_state[
                    "last_good_markets"
                ][series] = market

                st.session_state[
                    "last_good_times"
                ][series] = time.time()
            else:
                results[series] = st.session_state[
                    "last_good_markets"
                ].get(series)

                if error:
                    errors[series] = error

    return results, errors


# ============================================================
# DATABASE HELPERS
# ============================================================

def safe_store(factory):
    try:
        return factory(st.secrets)
    except Exception:
        return None


def safe_rows(store):
    if store is None:
        return []

    try:
        return store.all()
    except Exception as exc:
        st.warning(
            f"Tracker history unavailable: {exc}"
        )
        return []


# ============================================================
# DUAL SIGNAL MONITOR
# ============================================================

def monitor_bucket(series, stage):
    key = (series, stage)
    monitor = st.session_state["dual_monitor"]

    if key not in monitor:
        monitor[key] = {
            "checks": 0,
            "waits": 0,
            "candidates": 0,
            "saved": 0,
            "save_errors": 0,
            "near_misses": 0,
            "reasons": {},
            "last_reason": "Awaiting evaluation",
            "last_check": None,
        }

    return monitor[key]


def classify_reason(reason):
    text = str(reason or "").lower()

    if "unverified" in text:
        return "Direction unverified"

    if "history" in text or "sample" in text:
        return "History warming up"

    if "midpoint" in text and "aligned ticks" in text:
        if "none |" in text:
            return "Midpoint outside entry zone"

        return "Momentum / aligned ticks"

    if "spread" in text:
        return "Spread too wide"

    if "ask" in text or "entry" in text:
        return "Entry price too high"

    if "pressure" in text or "opposes" in text:
        return "Opposing pressure"

    if "quote" in text:
        return "Quote unavailable"

    return "Other condition"


def is_near_miss(reason):
    text = str(reason or "")

    if "NONE |" in text:
        return False

    ticks_match = re.search(
        r"Aligned ticks\s+(\d+)/3",
        text,
        re.IGNORECASE,
    )

    move_match = re.search(
        r"Move\s+([+-]?\d+(?:\.\d+)?)¢",
        text,
        re.IGNORECASE,
    )

    if not ticks_match or not move_match:
        return False

    ticks = int(ticks_match.group(1))
    movement = float(move_match.group(1))

    if "ABOVE |" in text:
        return ticks == 2 and movement >= 1.2

    if "BELOW |" in text:
        return ticks == 2 and movement <= -1.2

    return False


def monitor_event(
    name,
    series,
    ticker,
    stage,
    decision,
    reason,
):
    now = datetime.now(timezone.utc)

    sample_key = (
        ticker,
        stage,
        int(now.timestamp() // 5),
    )

    seen = st.session_state[
        "dual_monitor_seen"
    ]

    if sample_key in seen:
        return

    seen[sample_key] = True

    if len(seen) > 3000:
        seen.clear()
        seen[sample_key] = True

    bucket = monitor_bucket(series, stage)

    bucket["checks"] += 1
    bucket["last_check"] = now
    bucket["last_reason"] = str(reason)

    if decision == "ENTER":
        bucket["candidates"] += 1
    else:
        bucket["waits"] += 1

        category = classify_reason(reason)

        bucket["reasons"][category] = (
            bucket["reasons"].get(category, 0) + 1
        )

        if is_near_miss(reason):
            bucket["near_misses"] += 1

    events = st.session_state[
        "dual_monitor_events"
    ]

    events.insert(
        0,
        {
            "Time": now.astimezone(
                CENTRAL
            ).strftime("%I:%M:%S %p"),
            "Market": name,
            "Stage": (
                "10m" if stage == "EARLY_10M"
                else "5m"
            ),
            "Decision": decision,
            "Reason": str(reason),
        },
    )

    del events[100:]


def monitor_summary(series, stage):
    bucket = monitor_bucket(series, stage)

    reasons = bucket["reasons"]

    top_reason = (
        max(reasons, key=reasons.get)
        if reasons
        else "—"
    )

    return {
        "Window": (
            "10m Early"
            if stage == "EARLY_10M"
            else "5m Final"
        ),
        "Checks": bucket["checks"],
        "WAIT": bucket["waits"],
        "Near misses": bucket["near_misses"],
        "ENTER": bucket["candidates"],
        "Saved": bucket["saved"],
        "Save errors": bucket["save_errors"],
        "Top blocker": top_reason,
    }


# ============================================================
# DUAL STRONG LOCK 2.0 SHADOW MONITOR
# ============================================================

def v2_monitor_event(
    name,
    series,
    ticker,
    stage,
    decision,
    reason,
    score=None,
):
    now = datetime.now(timezone.utc)

    sample_key = (
        ticker,
        stage,
        int(now.timestamp() // 5),
    )

    seen = st.session_state[
        "dual_v2_monitor_seen"
    ]

    if sample_key in seen:
        return

    seen[sample_key] = True

    if len(seen) > 3000:
        seen.clear()
        seen[sample_key] = True

    events = st.session_state[
        "dual_v2_monitor_events"
    ]

    events.insert(
        0,
        {
            "Time": now.astimezone(
                CENTRAL
            ).strftime("%I:%M:%S %p"),
            "Market": name,
            "Ticker": ticker,
            "Stage": (
                "10m" if stage == "EARLY_10M"
                else "5m"
            ),
            "Decision": decision,
            "Score": score,
            "Reason": str(reason),
        },
    )

    del events[100:]


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("👑 QUEEN'S EDGE")
    st.success("BTC + WTI OIL ONLY")

    st.caption("Market quotes: 1-second target")
    st.caption("Tracker updates: 15 seconds")

    sudden_cents = st.slider(
        "Sudden move threshold (¢)",
        min_value=2,
        max_value=15,
        value=4,
    )

    if st.button("Clear local radar history"):
        st.session_state["quote_history"] = {}
        st.session_state["radar_events"] = []
        st.rerun()

    if st.button("Reset signal monitor"):
        st.session_state["dual_monitor"] = {}
        st.session_state["dual_monitor_seen"] = {}
        st.session_state["dual_monitor_events"] = []
        st.session_state["dual_v2_monitor_events"] = []
        st.session_state["dual_v2_monitor_seen"] = {}
        st.rerun()

    st.caption("Read-only. No automatic trading.")


# ============================================================
# LOAD TRACKERS
# ============================================================

call_store = safe_store(store_from_secrets)
early_store = safe_store(early_store_from_secrets)
paper_store = safe_store(paper_store_from_secrets)
strong_store = safe_store(strong_store_from_secrets)
dual_store = safe_store(dual_store_from_secrets)

tracker_markets, tracker_errors = active_markets()

if call_store:
    try:
        process(
            call_store,
            current_by_series=tracker_markets,
        )
    except Exception as exc:
        st.sidebar.warning(f"Locked tracker: {exc}")

if early_store:
    try:
        process_early(
            early_store,
            current_by_series=tracker_markets,
        )
    except Exception as exc:
        st.sidebar.warning(f"Early tracker: {exc}")


locked_rows = btc_oil_rows(safe_rows(call_store))
early_rows = btc_oil_rows(safe_rows(early_store))
paper_rows = btc_oil_rows(safe_rows(paper_store))
strong_rows = btc_oil_rows(safe_rows(strong_store))
dual_rows = btc_oil_rows(safe_rows(dual_store))


if paper_store:
    try:
        paper_grade_pending(
            paper_store,
            safe_rows(paper_store),
        )
        paper_rows = btc_oil_rows(
            safe_rows(paper_store)
        )
    except Exception as exc:
        st.sidebar.warning(f"WATCH grading: {exc}")

if strong_store:
    try:
        strong_grade_pending(
            strong_store,
            safe_rows(strong_store),
        )
        strong_rows = btc_oil_rows(
            safe_rows(strong_store)
        )
    except Exception as exc:
        st.sidebar.warning(
            f"Strong LOCK grading: {exc}"
        )

if dual_store:
    try:
        dual_store.grade(safe_rows(dual_store))
        dual_rows = btc_oil_rows(
            safe_rows(dual_store)
        )
    except Exception as exc:
        st.sidebar.warning(f"Dual grading: {exc}")


locked_map = {
    row["ticker"]: row
    for row in locked_rows
    if row.get("ticker")
}

early_map = {
    row["ticker"]: row
    for row in early_rows
    if row.get("ticker")
}


# ============================================================
# HEADER — FIXED BANNER
# ============================================================

banner = Path(__file__).with_name(
    "queens_banner.jpg"
)

if banner.exists():
    encoded = base64.b64encode(
        banner.read_bytes()
    ).decode("ascii")

    st.markdown(
        f'<img class="queen-banner" '
        f'src="data:image/jpeg;base64,{encoded}" '
        f'alt="QUEENS EDGE banner">',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="queen-title">'
        '👑 QUEEN’S EDGE | 254'
        '</div>',
        unsafe_allow_html=True,
    )

st.markdown(
    '<div class="queen-subtitle">'
    '₿ BITCOIN + 🛢️ WTI OIL COMMAND CENTER'
    '</div>',
    unsafe_allow_html=True,
)

st.caption(
    "⚡ Live Kalshi quotes · "
    "🔐 Dual Strong LOCK · "
    "🐋 Pressure Radar · "
    "📊 Paper Performance"
)


# ============================================================
# LIVE HISTORY / FLIP RADAR
# ============================================================

def update_history(ticker, name, mid):
    if mid is None:
        return "No quote"

    now = datetime.now(timezone.utc)

    history = st.session_state[
        "quote_history"
    ].setdefault(ticker, [])

    if (
        not history
        or (now - history[-1][0]).total_seconds()
        >= 0.8
    ):
        history.append(
            (now, mid, "YES midpoint")
        )

        del history[:-120]

    if len(history) < 3:
        return "Warming up"

    previous = history[-2][1] - history[-3][1]
    latest = history[-1][1] - history[-2][1]

    if previous <= -0.005 and latest >= 0.005:
        label = "🔺 Bullish flip"
    elif previous >= 0.005 and latest <= -0.005:
        label = "🔻 Bearish flip"
    elif abs(latest) * 100 >= sudden_cents:
        label = "⚡ Sudden move"
    else:
        return "Scanning"

    events = st.session_state["radar_events"]

    events.insert(
        0,
        {
            "ticker": ticker,
            "market": name,
            "label": label,
            "time": now,
            "move": latest * 100,
        },
    )

    del events[50:]
    return label


# ============================================================
# STRONG LOCK
# ============================================================

def strong_lock_panel(
    name,
    series,
    ticker,
    market,
    locked,
    verified,
):
    if not locked:
        return

    history = st.session_state[
        "quote_history"
    ].get(ticker, [])

    try:
        decision, reason, ask, strength = (
            strong_evaluate(
                locked,
                market,
                history,
                verified,
            )
        )

        st.markdown(
            f"**🔐 Strong LOCK: {decision}**"
        )
        st.caption(reason)

        if (
            decision == "ENTER"
            and strong_store is not None
            and ask is not None
        ):
            attempts = st.session_state[
                "strong_attempts"
            ]

            if time.time() - attempts.get(ticker, 0) >= 30:
                attempts[ticker] = time.time()

                strong_store.record_once(
                    {
                        "ticker": ticker,
                        "market_name": name,
                        "series_ticker": series,
                        "side": locked.get("call"),
                        "decision": "ENTER",
                        "entry_price": ask,
                        "yes_midpoint": (
                            number(
                                locked.get("yes_midpoint")
                            ) or 0
                        ),
                        "close_time": market.get(
                            "close_time"
                        ),
                        "status": "pending",
                        "observed_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }
                )

    except Exception as exc:
        st.caption(
            f"Strong LOCK unavailable: {exc}"
        )


# ============================================================
# DUAL STRONG LOCK
# ============================================================

def dual_panel(
    name,
    series,
    ticker,
    market,
    close,
    verified,
):
    st.markdown("**🔐 Dual Strong LOCK**")

    if close is None:
        st.caption("Waiting for contract close time")
        return

    seconds_left = (
        close - datetime.now(timezone.utc)
    ).total_seconds()

    history = st.session_state[
        "quote_history"
    ].get(ticker, [])

    analysis = dual_analyze(
        market,
        history,
        seconds_left,
        verified,
    )

    for stage, label, lower, upper in STAGES:
        key = (ticker, stage)

        existing = next(
            (
                row for row in dual_rows
                if row.get("ticker") == ticker
                and row.get("stage") == stage
            ),
            None,
        )

        if existing:
            st.caption(
                f"{label}: SAVED "
                f"{existing.get('side')} "
                f"· {existing.get('status')}"
            )
            continue

        if seconds_left > upper:
            st.caption(f"{label}: Awaiting window")
            continue

        if seconds_left < lower:
            last_reason = st.session_state[
                "dual_last_reason"
            ].get(key)

            if last_reason:
                st.caption(
                    f"{label}: SKIPPED "
                    f"· Last check: {last_reason}"
                )
            else:
                st.caption(
                    f"{label}: Window passed "
                    f"· No saved entry"
                )
            continue

        decision = analysis.get(
            "decision", "WAIT"
        )

        reason = str(
            analysis.get("reason")
            or "Conditions not met"
        )

        monitor_event(
            name,
            series,
            ticker,
            stage,
            decision,
            reason,
        )

        if decision != "ENTER":
            st.session_state[
                "dual_last_reason"
            ][key] = reason

            st.caption(
                f"{label}: WAIT · {reason}"
            )
            continue

        st.caption(
            f"{label}: ENTER candidate · {reason}"
        )

        if dual_store is None:
            st.error(
                "Dual Strong LOCK storage unavailable"
            )
            continue

        attempts = st.session_state[
            "dual_attempts"
        ]

        if time.time() - attempts.get(key, 0) >= 20:
            attempts[key] = time.time()

            try:
                payload = {
                    "ticker": ticker,
                    "stage": stage,
                    "market_name": name,
                    "series_ticker": series,
                    "side": analysis["side"],
                    "entry_price": analysis[
                        "entry_price"
                    ],
                    "yes_midpoint": analysis[
                        "yes_midpoint"
                    ],
                    "close_time": market.get(
                        "close_time"
                    ),
                    "observed_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "status": "pending",
                }

                result = dual_store.record_once(
                    payload
                )

                st.session_state[
                    "dual_save_status"
                ][key] = result

                updated_rows = dual_store.all()

                confirmed = any(
                    row.get("ticker") == ticker
                    and row.get("stage") == stage
                    for row in updated_rows
                )

                if confirmed:
                    monitor_bucket(
                        series, stage
                    )["saved"] += 1

                    st.session_state[
                        "dual_save_status"
                    ][key] = (
                        "SAVED — verified in Supabase"
                    )
                else:
                    st.session_state[
                        "dual_save_status"
                    ][key] = (
                        "Save attempted; "
                        "record not yet verified"
                    )

            except Exception as exc:
                st.session_state[
                    "dual_save_status"
                ][key] = f"SAVE ERROR: {exc}"

                monitor_bucket(
                    series, stage
                )["save_errors"] += 1

        st.caption(
            f"{label}: "
            f"{st.session_state['dual_save_status'].get(key, 'Checking…')}"
        )



# ============================================================
# DUAL STRONG LOCK 2.0 — SHADOW MODE
# ============================================================

def dual_v2_panel(name, series, ticker, market, close, verified):
    """Evaluate 2.0 independently. Never writes to Supabase."""
    st.markdown("**👑 Dual Strong LOCK 2.0 · SHADOW**")

    if close is None:
        st.caption("Waiting for contract close time")
        return

    seconds_left = (close - datetime.now(timezone.utc)).total_seconds()
    history = st.session_state["quote_history"].get(ticker, [])
    shadow_entries = st.session_state["dual_v2_shadow_entries"]
    seen = st.session_state["dual_v2_monitor_seen"]
    events = st.session_state["dual_v2_monitor_events"]

    for stage, label, lower, upper in STAGES:
        key = (ticker, stage)
        saved = shadow_entries.get(key)
        if saved:
            st.caption(
                f"{label}: SHADOW ENTER {saved['side']} · "
                f"Ask {saved['entry_price'] * 100:.1f}¢ · "
                f"Score {saved['score']:.1f}/100 · "
                f"{saved['time']} Central · session-only"
            )
            continue

        if seconds_left > upper:
            st.caption(f"{label}: Awaiting window")
            continue
        if seconds_left < lower:
            st.caption(f"{label}: Window passed · no shadow entry")
            continue

        try:
            result = dual_v2_analyze(
                market, history, seconds_left, verified
            )
        except Exception as exc:
            st.caption(f"{label}: V2 evaluation unavailable: {exc}")
            continue

        decision = result.get("decision", "WAIT")
        reason = str(result.get("reason", "Conditions not met"))
        now = datetime.now(timezone.utc)
        sample_key = (ticker, stage, int(now.timestamp() // 5))
        if sample_key not in seen:
            seen[sample_key] = True
            events.insert(0, {
                "Time": now.astimezone(CENTRAL).strftime("%I:%M:%S %p"),
                "Market": name,
                "Ticker": ticker,
                "Stage": label,
                "Decision": decision,
                "Reason": reason,
            })
            del events[100:]
            if len(seen) > 3000:
                seen.clear()

        if decision == "ENTER":
            entry_price = number(result.get("entry_price"))
            if entry_price is None:
                st.caption(f"{label}: WAIT · Missing executable entry ask")
                continue
            shadow_entries[key] = {
                "ticker": ticker,
                "stage": stage,
                "market": name,
                "side": result.get("side", "—"),
                "entry_price": entry_price,
                "score": number(result.get("score")) or 0.0,
                "observed_at": now.isoformat(),
                "time": now.astimezone(CENTRAL).strftime("%I:%M:%S %p"),
                "reason": reason,
            }
            st.caption(f"{label}: SHADOW ENTER · {reason}")
        else:
            st.caption(f"{label}: WAIT · {reason}")


# ============================================================
# MARKET CARDS
# ============================================================

def market_card(
    name,
    series,
    emoji,
    markets,
    errors,
):
    with st.container(border=True):
        st.subheader(f"{emoji} {name.upper()}")

        market = markets.get(series)

        if not market:
            st.warning("No active contract available")
            return

        ticker = market.get("ticker", series)
        verified = verified_direction(market)

        close = parse_time(
            market.get("close_time")
            or market.get("latest_expiration_time")
        )

        if close:
            remaining = max(
                0,
                int(
                    (
                        close
                        - datetime.now(timezone.utc)
                    ).total_seconds()
                ),
            )

            minutes, seconds = divmod(
                remaining, 60
            )

            countdown = (
                f"{minutes:02d}:{seconds:02d}"
            )
        else:
            countdown = "—"

        top_left, top_right = st.columns(
            [1.5, 1]
        )

        with top_left:
            st.caption(
                f"TARGET · {target(market)}"
            )

        with top_right:
            st.markdown(
                f"**⏱️ {countdown}**"
            )

        if series in errors:
            st.warning(
                "Latest quote request failed; "
                "showing cached data"
            )
        else:
            stamp = st.session_state[
                "last_good_times"
            ].get(series)

            if stamp is not None:
                st.caption(
                    "📡 Quote received "
                    f"{time.time() - stamp:.1f}s ago"
                )

        yes_bid = quote(market, "yes", "bid")
        yes_ask = quote(market, "yes", "ask")
        no_bid = quote(market, "no", "bid")
        no_ask = quote(market, "no", "ask")

        left, right = st.columns(
            2, gap="small"
        )

        with left:
            above_label = (
                "ABOVE TARGET" if verified else "YES"
            )

            st.markdown(
                '<div class="quote-box above">'
                f"<b>{above_label}</b>"
                '<p>BID <span class="quote-price">'
                f"{cents(yes_bid)}</span></p>"
                '<p>ASK <span class="quote-price">'
                f"{cents(yes_ask)}</span></p>"
                "</div>",
                unsafe_allow_html=True,
            )

        with right:
            below_label = (
                "BELOW TARGET" if verified else "NO"
            )

            st.markdown(
                '<div class="quote-box below">'
                f"<b>{below_label}</b>"
                '<p>BID <span class="quote-price">'
                f"{cents(no_bid)}</span></p>"
                '<p>ASK <span class="quote-price">'
                f"{cents(no_ask)}</span></p>"
                "</div>",
                unsafe_allow_html=True,
            )

        mid = midpoint(market)

        radar = update_history(
            ticker, name, mid
        )

        if not verified or mid is None:
            lean = "⚪ NO VERIFIED CALL"
        elif mid >= 0.60:
            lean = "🟢 ABOVE TARGET"
        elif mid <= 0.40:
            lean = "🔴 BELOW TARGET"
        else:
            lean = "⚪ NO CLEAR CALL"

        st.markdown(
            f"**Market lean: {lean}**"
        )

        st.caption(
            f"YES midpoint: {cents(mid)} "
            f"· Radar: {radar}"
        )

        history = st.session_state[
            "quote_history"
        ].get(ticker, [])

        with st.expander(
            "📈 Live chart",
            expanded=False,
        ):
            if len(history) >= 2:
                st.line_chart(
                    {
                        "YES midpoint (¢)": [
                            value * 100
                            for _, value, _ in history
                        ]
                    },
                    height=160,
                )
            else:
                st.caption(
                    "Collecting live quote history"
                )

        early = early_map.get(ticker)
        locked = locked_map.get(ticker)

        a, b = st.columns(2)

        with a:
            st.markdown("**🌅 Early Call**")
            st.write(
                str(early.get("call"))
                if early
                else "Awaiting signal"
            )

        with b:
            st.markdown("**🔒 Locked Call**")
            st.write(
                (
                    f"{locked.get('call')} "
                    f"· {locked.get('status')}"
                )
                if locked
                else "Awaiting lock"
            )

        strong_lock_panel(
            name,
            series,
            ticker,
            market,
            locked,
            verified,
        )

        dual_panel(
            name,
            series,
            ticker,
            market,
            close,
            verified,
        )

        dual_v2_panel(
            name,
            series,
            ticker,
            market,
            close,
            verified,
        )

        with st.expander(
            "🐋 Buying / Selling Pressure"
        ):
            st.caption(
                "Experimental pressure indicators; "
                "no verified whale identities"
            )

            try:
                show_pressure_radar(
                    name, ticker
                )
            except Exception as exc:
                st.caption(
                    f"Pressure unavailable: {exc}"
                )

        with st.expander(
            "📋 Contract details"
        ):
            st.caption(f"Ticker: {ticker}")

            st.write(
                market.get("rules_primary")
                or "See Kalshi rules"
            )

            if market.get("rules_secondary"):
                st.write(
                    market["rules_secondary"]
                )


# ============================================================
# LIVE MARKET GRID
# ============================================================

@st.fragment(run_every="1s")
def live_grid():
    markets, errors = active_markets()

    st.subheader(
        "⚡ LIVE MARKET COMMAND CENTER"
    )

    left, right = st.columns(
        2, gap="medium"
    )

    with left:
        market_card(
            *SERIES[0],
            markets,
            errors,
        )

    with right:
        market_card(
            *SERIES[1],
            markets,
            errors,
        )


live_grid()


# ============================================================
# PERFORMANCE HELPERS
# ============================================================

def counts(rows):
    wins = sum(
        str(row.get("status", "")).lower()
        == "win"
        for row in rows
    )

    losses = sum(
        str(row.get("status", "")).lower()
        == "loss"
        for row in rows
    )

    pending = sum(
        str(row.get("status", "")).lower()
        == "pending"
        for row in rows
    )

    return wins, losses, pending


def paper_pnl(row):
    status = str(
        row.get("status", "")
    ).lower()

    price = number(
        row.get("entry_price")
    )

    if status not in ("win", "loss"):
        return None

    if price is None or not 0 <= price <= 1:
        return None

    payout = 1 if status == "win" else 0

    return payout - price - 0.02


# ============================================================
# PERFORMANCE CENTER
# ============================================================

st.divider()

st.subheader(
    "📊 BTC + OIL PERFORMANCE CENTER"
)

dual_early_rows = [
    row for row in dual_rows
    if row.get("stage") == "EARLY_10M"
]

dual_final_rows = [
    row for row in dual_rows
    if row.get("stage") == "FINAL_5M"
]

performance_groups = [
    ("🔒 Locked", locked_rows),
    ("🌅 Early", early_rows),
    ("💰 WATCH", paper_rows),
    ("🔐 Strong LOCK", strong_rows),
    ("👑 Dual 10m", dual_early_rows),
    ("👑 Dual 5m", dual_final_rows),
]

all_rows = (
    locked_rows
    + early_rows
    + paper_rows
    + strong_rows
    + dual_rows
)

total_wins, total_losses, total_pending = (
    counts(all_rows)
)

total_settled = total_wins + total_losses

overall_rate = (
    f"{total_wins / total_settled:.1%}"
    if total_settled
    else "—"
)

metric_cols = st.columns(4)

with metric_cols[0]:
    st.metric("🏆 Wins", total_wins)

with metric_cols[1]:
    st.metric("❌ Losses", total_losses)

with metric_cols[2]:
    st.metric("📈 Win Rate", overall_rate)

with metric_cols[3]:
    st.metric("⏳ Pending", total_pending)

st.caption(
    "Combined paper records across tracker types. "
    "The same market may appear in multiple strategies, "
    "so these totals are not independent trades."
)


# ============================================================
# SIGNAL MONITOR
# ============================================================

st.markdown(
    "### 🔎 DUAL STRONG LOCK SIGNAL MONITOR"
)

st.caption(
    "Sampled evaluations during the 10-minute and "
    "5-minute entry windows. Counts are diagnostic "
    "observations, not trades. Monitoring requires "
    "the dashboard session to remain active."
)

btc_col, oil_col = st.columns(
    2, gap="medium"
)

with btc_col:
    with st.container(border=True):
        st.markdown("#### ₿ BITCOIN")

        st.dataframe(
            [
                monitor_summary(
                    "KXBTC15M", stage
                )
                for stage, _, _, _ in STAGES
            ],
            hide_index=True,
            use_container_width=True,
        )

        for stage, label, _, _ in STAGES:
            bucket = monitor_bucket(
                "KXBTC15M", stage
            )

            st.caption(
                f"{label} · Last: "
                f"{bucket['last_reason']}"
            )

with oil_col:
    with st.container(border=True):
        st.markdown("#### 🛢️ WTI OIL")

        st.dataframe(
            [
                monitor_summary(
                    "KXWTI15M", stage
                )
                for stage, _, _, _ in STAGES
            ],
            hide_index=True,
            use_container_width=True,
        )

        for stage, label, _, _ in STAGES:
            bucket = monitor_bucket(
                "KXWTI15M", stage
            )

            st.caption(
                f"{label} · Last: "
                f"{bucket['last_reason']}"
            )

with st.expander(
    "📋 Signal monitor details",
    expanded=False,
):
    st.caption(
        "Near miss: midpoint and 25-second movement "
        "qualified, but only 2 of the 3 required "
        "aligned ticks were observed."
    )

    events = st.session_state[
        "dual_monitor_events"
    ]

    if events:
        st.dataframe(
            events[:50],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info(
            "No monitored entry-window evaluations yet. "
            "Keep the dashboard open through a "
            "10-minute or 5-minute window."
        )


# ============================================================
# V2 SHADOW COMPARISON — SESSION ONLY
# ============================================================

st.markdown("### 👑 DUAL STRONG LOCK 2.0 · SHADOW MONITOR")
st.caption(
    "2.0 is evaluated alongside the original, with no trades or "
    "Supabase writes. Entries and evaluations are session-only; "
    "they reset if the Streamlit session restarts. "
    "A signal score is not a calibrated probability."
)
shadow_entries = list(st.session_state["dual_v2_shadow_entries"].values())
shadow_events = st.session_state["dual_v2_monitor_events"]
sc1, sc2, sc3 = st.columns(3)
sc1.metric("V2 shadow entries", len(shadow_entries))
sc2.metric("V2 observed checks", len(shadow_events))
sc3.metric(
    "V2 ENTER checks",
    sum(event["Decision"] == "ENTER" for event in shadow_events),
)
if shadow_entries:
    st.dataframe(shadow_entries, hide_index=True, use_container_width=True)
else:
    st.caption("No V2 shadow entries yet. Keep preview open during entry windows.")
with st.expander("📋 V2 shadow signal details", expanded=False):
    if shadow_events:
        st.dataframe(shadow_events[:50], hide_index=True, use_container_width=True)
    else:
        st.caption("Waiting for V2 entry-window evaluations.")


# ============================================================
# DATABASE DIAGNOSTICS
# ============================================================

with st.expander(
    "🧪 Dual Strong LOCK database diagnostics",
    expanded=False,
):
    st.caption(
        "Read-only connection test. "
        "No test entries are created."
    )

    if dual_store is None:
        st.error(
            "DATABASE UNAVAILABLE — "
            "Check Supabase configuration."
        )
    else:
        try:
            response = dual_store.request(
                "GET",
                {
                    "select": "ticker,stage,status",
                    "limit": 1,
                },
            )

            st.success(
                "DATABASE READ OK — "
                "Dual Strong LOCK table is accessible."
            )

            st.caption(
                f"Sample rows returned: "
                f"{len(response)} (maximum 1)"
            )

            st.caption(
                "BTC + Oil Dual entries loaded: "
                f"{len(dual_rows)}"
            )

            st.info(
                "Reading works. A qualifying ENTER "
                "signal is still needed to verify saving."
            )

        except Exception as exc:
            st.error(
                f"DATABASE READ FAILED — {exc}"
            )


# ============================================================
# STRATEGY PERFORMANCE
# ============================================================

st.markdown(
    "### 📈 STRATEGY PERFORMANCE"
)

summary = []

for label, rows in performance_groups:
    wins, losses, pending = counts(rows)

    pnl = [
        value
        for row in rows
        if (
            value := paper_pnl(row)
        ) is not None
    ]

    summary.append(
        {
            "Tracker": label,
            "Wins": wins,
            "Losses": losses,
            "Win rate": (
                f"{wins / (wins + losses):.1%}"
                if wins + losses
                else "—"
            ),
            "Pending": pending,
            "Paper P&L*": (
                f"${sum(pnl):+.2f}"
                if pnl
                else "—"
            ),
        }
    )

st.dataframe(
    summary,
    hide_index=True,
    use_container_width=True,
)

st.caption(
    "*Illustrative one-contract P&L assumes "
    "2¢ fees and available entry prices. "
    "Not actual trading profit."
)


# ============================================================
# PER-MARKET PERFORMANCE
# ============================================================

with st.expander("📋 Per-market performance"):
    for label, rows in performance_groups:
        values = []

        for name, series, emoji in SERIES:
            subset = [
                row for row in rows
                if market_series(row) == series
            ]

            wins, losses, pending = counts(
                subset
            )

            values.append(
                {
                    "Tracker": label,
                    "Market": f"{emoji} {name}",
                    "Wins": wins,
                    "Losses": losses,
                    "Win rate": (
                        f"{wins / (wins + losses):.1%}"
                        if wins + losses
                        else "—"
                    ),
                    "Pending": pending,
                }
            )

        st.dataframe(
            values,
            hide_index=True,
            use_container_width=True,
        )


# ============================================================
# FULL TRACKER HISTORY
# ============================================================

with st.expander("📜 Full tracker history"):
    tabs = st.tabs(
        [name for name, _ in performance_groups]
    )

    for tab, (label, rows) in zip(
        tabs, performance_groups
    ):
        with tab:
            if rows:
                st.dataframe(
                    rows,
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption(
                    "No entries recorded for BTC or Oil"
                )


# ============================================================
# SETTLEMENTS AND FLIP RADAR
# ============================================================

st.divider()

left_info, right_info = st.columns(
    2, gap="medium"
)

with left_info:
    st.markdown(
        "**📜 Last 10 settled markets**"
    )

    for name, series, emoji in SERIES:
        try:
            recent = fetch_recent(series)

            arrows = [
                (
                    "🟢↑"
                    if str(
                        row.get("result", "")
                    ).lower() == "yes"
                    else "🔴↓"
                )
                for row in reversed(recent)
            ]

            st.caption(
                f"{emoji} {name}: "
                + (
                    " ".join(arrows)
                    if arrows
                    else "No results"
                )
            )

        except Exception:
            st.caption(
                f"{emoji} {name}: Results unavailable"
            )

with right_info:
    st.markdown(
        "**🚨 Latest Flip Radar alerts**"
    )

    events = [
        event
        for event in st.session_state[
            "radar_events"
        ]
        if event.get("market") in (
            "Bitcoin", "WTI Oil"
        )
    ]

    for event in events[:3]:
        stamp = event[
            "time"
        ].astimezone(CENTRAL)

        st.caption(
            f"{event['market']} · "
            f"{event['label']} · "
            f"{event['move']:+.1f}¢ · "
            f"{stamp:%I:%M:%S %p}"
        )

    if not events:
        st.caption("Scanning BTC and Oil")

    if len(events) > 3:
        with st.expander("More radar alerts"):
            for event in events[3:15]:
                stamp = event[
                    "time"
                ].astimezone(CENTRAL)

                st.caption(
                    f"{event['market']} · "
                    f"{event['label']} · "
                    f"{event['move']:+.1f}¢ · "
                    f"{stamp:%I:%M:%S %p}"
                )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "Last tracker refresh: "
    + datetime.now(CENTRAL).strftime(
        "%b %d, %Y · %I:%M:%S %p %Z"
    )
)

st.caption(
    "QUEEN'S EDGE | 254 · "
    "Read-only public Kalshi API · "
    "No real orders · "
    "Quotes may lag or be rate-limited"
)

st_autorefresh(
    interval=15000,
    key="queens_edge_tracker_refresh",
)



