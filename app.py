
"""QUEEN'S EDGE | 254 — BITCOIN COMMAND CENTER.

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
import pandas as pd
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
from dual_v2_tracker import (
    from_secrets as v2_store_from_secrets,
    grade_pending as v2_grade_pending,
)
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
        padding-top: 0.35rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
        padding-bottom: 1rem;
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

    .queen-banner-wrap {position:relative; width:100%;}
    .queen-live-clock {
        position:absolute; box-sizing:border-box; left:1.8%; top:49%; width:13.5%; height:46%;
        background:#0a0b1c; border:1px solid #8058d4; border-radius:12px;
        display:flex; flex-direction:column; align-items:center; justify-content:center;
        gap:3px; color:#e9ddff; font-size:clamp(8px,1.05vw,17px);
        text-align:center; box-shadow:0 0 12px #231145;
    }
    .queen-live-clock strong {font-size:clamp(10px,1.35vw,23px);}
    .queen-live-indicator {color:#41e8bb; font-weight:800;}
    @media(max-width:700px){
        .queen-live-clock {top:49%;height:46%;width:13.5%;left:1.8%;gap:0;font-size:clamp(5px,1vw,9px);border-radius:5px;}
        .queen-live-clock strong {font-size:9px;}
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
    /* Compact layout; all trading logic unchanged. */
    [data-testid="stVerticalBlock"] {gap:.48rem;}
    [data-testid="stHorizontalBlock"] {gap:.65rem;}
    div[data-testid="stMetric"] {padding:8px 10px; min-height:70px;}
    div[data-testid="stMetricValue"] {font-size:1.25rem;}
    .quote-box {min-height:76px; padding:7px;}
    .quote-box p {display:inline-block; margin:6px 10px 1px;}
    .quote-price {font-size:1.08rem;}
    h1, h2, h3 {margin-top:.25rem !important; margin-bottom:.25rem !important;}
    .queen-banner {margin-bottom:3px;}
    @media(max-width:700px) {
      .quote-box {min-height:70px; padding:5px;}
      .quote-box p {margin:4px 6px 0;}
      div[data-testid="stMetric"] {padding:6px;}
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
    "dual_v2_save_errors": {},
    "dual_v2_save_attempts": {},
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


    return None


def btc_rows(rows):
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

    with ThreadPoolExecutor(max_workers=len(SERIES)) as executor:
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
# DUAL STRONG LOCK SIGNAL MONITOR
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

    seen = st.session_state["dual_monitor_seen"]

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

    events = st.session_state["dual_monitor_events"]

    events.insert(
        0,
        {
            "Time": now.astimezone(CENTRAL).strftime(
                "%I:%M:%S %p"
            ),
            "Market": name,
            "Stage": (
                "10m" if stage == "EARLY_10M" else "5m"
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
        if reasons else "—"
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
# DUAL STRONG LOCK 2.0 SIGNAL MONITOR
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

    seen = st.session_state["dual_v2_monitor_seen"]
    events = st.session_state["dual_v2_monitor_events"]

    # Always record ENTER, even if a WAIT was sampled
    # earlier within the same five-second bucket.
    if decision != "ENTER" and sample_key in seen:
        return

    if decision != "ENTER":
        seen[sample_key] = True

    events.insert(
        0,
        {
            "Time": now.astimezone(CENTRAL).strftime(
                "%I:%M:%S %p"
            ),
            "Market": name,
            "Ticker": ticker,
            "Stage": (
                "10m" if stage == "EARLY_10M" else "5m"
            ),
            "Decision": decision,
            "Score": score,
            "Reason": str(reason),
        },
    )

    del events[100:]

    if len(seen) > 3000:
        seen.clear()


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("👑 QUEEN'S EDGE")
    st.success("OIL PRIMARY · BITCOIN TRACKING")

    st.caption("Market quotes: 1-second target")
    st.caption("Tracker updates: automatic every 15 seconds")

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

# Refresh the full page periodically for database history and grading.
# The live Bitcoin quote card refreshes independently every second.
st_autorefresh(interval=15_000, limit=None, key="btc_full_refresh")


# ============================================================
# LOAD TRACKERS
# ============================================================

call_store = safe_store(store_from_secrets)
early_store = safe_store(early_store_from_secrets)
paper_store = safe_store(paper_store_from_secrets)
strong_store = safe_store(strong_store_from_secrets)
dual_store = safe_store(dual_store_from_secrets)
v2_store = safe_store(v2_store_from_secrets)

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


# Keep Bitcoin performance exactly as before. Oil uses the existing
# original Early/Locked trackers, with a separate paper-only report.
all_locked_records = safe_rows(call_store)
all_early_records = safe_rows(early_store)
locked_rows = btc_rows(all_locked_records)
early_rows = btc_rows(all_early_records)
oil_locked_rows = [r for r in all_locked_records if str(r.get("series_ticker") or "").upper() == "KXWTI15M" or str(r.get("ticker") or "").upper().startswith("KXWTI15M-")]
oil_early_rows = [r for r in all_early_records if str(r.get("series_ticker") or "").upper() == "KXWTI15M" or str(r.get("ticker") or "").upper().startswith("KXWTI15M-")]
paper_rows = btc_rows(safe_rows(paper_store))
strong_rows = btc_rows(safe_rows(strong_store))
dual_rows = btc_rows(safe_rows(dual_store))
v2_rows = btc_rows(safe_rows(v2_store))


# ============================================================
# OFFICIAL SETTLEMENT GRADING
# ============================================================

if paper_store:
    try:
        paper_grade_pending(
            paper_store,
            paper_rows,
        )
        paper_rows = btc_rows(
            safe_rows(paper_store)
        )
    except Exception as exc:
        st.sidebar.warning(f"WATCH grading: {exc}")


if strong_store:
    try:
        strong_grade_pending(
            strong_store,
            strong_rows,
        )
        strong_rows = btc_rows(
            safe_rows(strong_store)
        )
    except Exception as exc:
        st.sidebar.warning(
            f"Strong LOCK grading: {exc}"
        )


if dual_store:
    try:
        dual_store.grade(dual_rows)
        dual_rows = btc_rows(
            safe_rows(dual_store)
        )
    except Exception as exc:
        st.sidebar.warning(f"Dual grading: {exc}")


if v2_store:
    try:
        pending_v2 = [
            row for row in v2_rows
            if str(row.get("status") or "").lower()
            == "pending"
        ]

        if pending_v2:
            v2_grade_pending(v2_store, pending_v2)

        v2_rows = btc_rows(
            safe_rows(v2_store)
        )
    except Exception as exc:
        st.sidebar.warning(
            f"Dual V2 grading: {exc}"
        )


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
oil_locked_map = {r["ticker"]: r for r in oil_locked_rows if r.get("ticker")}
oil_early_map = {r["ticker"]: r for r in oil_early_rows if r.get("ticker")}



# ============================================================
# HEADER — FULL QUEEN'S EDGE BANNER
# ============================================================

banner = Path(__file__).with_name(
    "queens_banner.jpg"
)

if banner.exists():
    encoded = base64.b64encode(
        banner.read_bytes()
    ).decode("ascii")

    now_ct = datetime.now(CENTRAL)
    date_ct = now_ct.strftime("%a, %b %d, %Y")
    time_ct = now_ct.strftime("%I:%M %p CT").lstrip("0")
    st.markdown(
        '<div class="queen-banner-wrap">'
        f'<img class="queen-banner" '
        f'src="data:image/jpeg;base64,{encoded}" '
        f'alt="QUEENS EDGE banner">'
        '<div class="queen-live-clock">'
        '<span class="queen-live-indicator">◉ LIVE · CENTRAL</span>'
        f'<span>{date_ct}</span>'
        f'<strong>{time_ct}</strong>'
        '</div></div>',
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
    '🛢️ OIL MARKET INTELLIGENCE · ₿ BITCOIN TRACKING'
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
# DUAL STRONG LOCK — ORIGINAL STRATEGY
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
# DUAL STRONG LOCK 2.0 — PERMANENT SHADOW TRACKER
# ============================================================

def dual_v2_panel(
    name,
    series,
    ticker,
    market,
    close,
    verified,
):
    """
    Paper-only V2 strategy.

    Records qualifying signals to Supabase.
    Does not place real orders.
    """

    st.markdown(
        "**👑 Dual Strong LOCK 2.0 · SHADOW**"
    )

    if close is None:
        st.caption(
            "Waiting for contract close time"
        )
        return

    seconds_left = (
        close - datetime.now(timezone.utc)
    ).total_seconds()

    history = st.session_state[
        "quote_history"
    ].get(ticker, [])

    shadow_entries = st.session_state[
        "dual_v2_shadow_entries"
    ]

    for stage, label, lower, upper in STAGES:
        key = (ticker, stage)

        existing = next(
            (
                row for row in v2_rows
                if row.get("ticker") == ticker
                and row.get("stage") == stage
            ),
            None,
        )

        if existing:
            side = existing.get("side", "—")
            status = existing.get(
                "status", "pending"
            )

            st.caption(
                f"{label}: SAVED {side} "
                f"· {status.upper()} "
                f"· permanent record"
            )
            continue

        saved = shadow_entries.get(key)

        if saved:
            if v2_store is not None:
                attempts = st.session_state[
                    "dual_v2_save_attempts"
                ]

                if (
                    time.time() - attempts.get(key, 0)
                    >= 20
                ):
                    attempts[key] = time.time()

                    try:
                        v2_store.record_once(
                            {
                                "ticker": ticker,
                                "stage": stage,
                                "market_name": name,
                                "series_ticker": series,
                                "side": saved["side"],
                                "entry_price": saved[
                                    "entry_price"
                                ],
                                "score": saved["score"],
                                "reason": saved["reason"],
                                "observed_at": saved[
                                    "observed_at"
                                ],
                                "close_time": (
                                    market.get("close_time")
                                    or close.isoformat()
                                ),
                            }
                        )

                        st.session_state[
                            "dual_v2_save_errors"
                        ].pop(key, None)

                        st.caption(
                            f"{label}: SAVED to Supabase "
                            f"· {saved['side']}"
                        )

                    except Exception as exc:
                        st.session_state[
                            "dual_v2_save_errors"
                        ][key] = str(exc)

                        st.caption(
                            f"{label}: SAVE ERROR · {exc}"
                        )
                else:
                    st.caption(
                        f"{label}: SHADOW ENTER "
                        f"{saved['side']} "
                        f"· save retry pending"
                    )
            else:
                st.caption(
                    f"{label}: SHADOW ENTER "
                    f"{saved['side']} "
                    f"· database unavailable"
                )

            continue

        if seconds_left > upper:
            st.caption(
                f"{label}: Awaiting window"
            )
            continue

        if seconds_left < lower:
            st.caption(
                f"{label}: Window passed "
                f"· no V2 entry"
            )
            continue

        try:
            result = dual_v2_analyze(
                market,
                history,
                seconds_left,
                verified,
            )
        except Exception as exc:
            st.caption(
                f"{label}: V2 evaluation unavailable: "
                f"{exc}"
            )
            continue

        decision = result.get(
            "decision", "WAIT"
        )

        reason = str(
            result.get("reason")
            or "Conditions not met"
        )

        score = number(
            result.get("score")
        )

        v2_monitor_event(
            name,
            series,
            ticker,
            stage,
            decision,
            reason,
            score,
        )

        if decision != "ENTER":
            st.caption(
                f"{label}: WAIT · {reason}"
            )
            continue

        entry_price = number(
            result.get("entry_price")
        )

        side = result.get("side")

        if (
            entry_price is None
            or not 0 <= entry_price <= 1
            or str(side or "").upper()
            not in ("ABOVE", "BELOW", "YES", "NO")
        ):
            st.caption(
                f"{label}: WAIT "
                f"· Invalid entry price or side"
            )
            continue

        now = datetime.now(timezone.utc)

        entry = {
            "ticker": ticker,
            "stage": stage,
            "market": name,
            "side": side,
            "entry_price": entry_price,
            "score": score or 0.0,
            "observed_at": now.isoformat(),
            "time": now.astimezone(
                CENTRAL
            ).strftime("%I:%M:%S %p"),
            "reason": reason,
        }

        shadow_entries[key] = entry

        st.caption(
            f"{label}: SHADOW ENTER "
            f"{side} · "
            f"Ask {entry_price * 100:.1f}¢ · "
            f"Score {entry['score']:.1f}/100"
        )

        if v2_store is None:
            st.caption(
                "V2 database unavailable "
                "· entry held in session"
            )
            continue

        try:
            v2_store.record_once(
                {
                    "ticker": ticker,
                    "stage": stage,
                    "market_name": name,
                    "series_ticker": series,
                    "side": side,
                    "entry_price": entry_price,
                    "score": entry["score"],
                    "reason": reason,
                    "observed_at": entry[
                        "observed_at"
                    ],
                    "close_time": (
                        market.get("close_time")
                        or close.isoformat()
                    ),
                }
            )

            st.session_state[
                "dual_v2_save_errors"
            ].pop(key, None)

            st.caption(
                "💾 V2 paper entry saved "
                "and verified in Supabase"
            )

        except Exception as exc:
            st.session_state[
                "dual_v2_save_errors"
            ][key] = str(exc)

            st.caption(
                f"V2 save failed: {exc}"
            )


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
        st.markdown(f"**{emoji} {name.upper()} · LIVE 15-MINUTE MARKET**")

        market = markets.get(series)

        if not market:
            st.warning(
                "No active contract available"
            )
            return

        ticker = market.get(
            "ticker", series
        )

        verified = verified_direction(
            market
        )

        close = parse_time(
            market.get("close_time")
            or market.get(
                "latest_expiration_time"
            )
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

        yes_bid = quote(
            market, "yes", "bid"
        )
        yes_ask = quote(
            market, "yes", "ask"
        )
        no_bid = quote(
            market, "no", "bid"
        )
        no_ask = quote(
            market, "no", "ask"
        )

        left, right = st.columns(
            2, gap="small"
        )

        with left:
            above_label = (
                "ABOVE TARGET"
                if verified
                else "YES"
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
                "BELOW TARGET"
                if verified
                else "NO"
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

        quote_age = None
        stamp = st.session_state["last_good_times"].get(series)
        if stamp is not None:
            quote_age = time.time() - stamp
        history = st.session_state["quote_history"].get(ticker, [])
        locked = (locked_map if series == "KXBTC15M" else oil_locked_map).get(ticker)
        qe_trade_console(ticker, verified, yes_bid, yes_ask, no_bid, no_ask,
                         remaining if close else None, quote_age, history, locked)

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

        early = (early_map if series == "KXBTC15M" else oil_early_map).get(ticker)
        locked = (locked_map if series == "KXBTC15M" else oil_locked_map).get(ticker)

        a, b = st.columns(2)

        with a:
            st.markdown(
                "**🌅 Early Call**"
            )
            st.write(
                str(early.get("call"))
                if early
                else "Awaiting signal"
            )

        with b:
            st.markdown(
                "**🔒 Locked Call**"
            )
            st.write(
                (
                    f"{locked.get('call')} "
                    f"· {locked.get('status')}"
                )
                if locked
                else "Awaiting lock"
            )

        if series == "KXBTC15M":
            with st.expander("🔐 Detailed strategy checks · Strong LOCK / Dual / V2", expanded=False):
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
            st.caption(
                f"Ticker: {ticker}"
            )

            st.write(
                market.get("rules_primary")
                or "See Kalshi rules"
            )

            if market.get(
                "rules_secondary"
            ):
                st.write(
                    market["rules_secondary"]
                )



# ============================================================
# QUEEN'S EDGE — SCALP INTELLIGENCE (OBSERVATIONAL / PAPER ONLY)
# Uses Kalshi YES quote history, NOT an underlying Oil/BTC feed.
# This module deliberately does not issue unvalidated BUY NOW signals.
# ============================================================

def qe_sma(values, length):
    if len(values) < length:
        return None
    return sum(values[-length:]) / length


def qe_ema_series(values, length):
    if not values:
        return []
    alpha = 2 / (length + 1)
    output = [float(values[0])]
    for v in values[1:]:
        output.append(alpha * float(v) + (1 - alpha) * output[-1])
    return output


def qe_rsi(values, length=14):
    if len(values) <= length:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0) for d in deltas[-length:]]
    losses = [max(-d, 0) for d in deltas[-length:]]
    ag, al = sum(gains) / length, sum(losses) / length
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - 100 / (1 + ag / al)


def qe_indicators(history):
    # Observations are quote snapshots, not exchange candles or trade volume.
    values = [float(item[1]) for item in history if item[1] is not None]
    if len(values) < 20:
        return {"ready": False, "samples": len(values)}
    short = qe_ema_series(values, 8)
    long = qe_ema_series(values, 17)
    macd = [a - b for a, b in zip(short, long)]
    signal = qe_ema_series(macd, 6)
    rsi = qe_rsi(values)
    recent = values[-10:]
    trend = "RISING" if short[-1] > long[-1] and values[-1] > values[-6] else (
        "FALLING" if short[-1] < long[-1] and values[-1] < values[-6] else "MIXED")
    momentum = "UP" if macd[-1] > signal[-1] else "DOWN"
    return {"ready": True, "samples": len(values), "trend": trend,
            "momentum": momentum, "rsi": rsi,
            "support": min(recent), "resistance": max(recent),
            "last": values[-1]}


def qe_trade_console(ticker, verified, yes_bid, yes_ask, no_bid, no_ask,
                     remaining, quote_age, history, locked):
    """Conservative, paper-only settlement intelligence; never an execution signal."""
    stats = qe_indicators(history)
    fresh = quote_age is not None and quote_age <= 15
    valid = all(v is not None for v in (yes_bid, yes_ask, no_bid, no_ask))
    valid = valid and yes_ask >= yes_bid and no_ask >= no_bid
    direction = "UNCERTAIN"
    if verified and stats.get("ready"):
        if stats["trend"] == "RISING" and stats["momentum"] == "UP":
            direction = "ABOVE"
        elif stats["trend"] == "FALLING" and stats["momentum"] == "DOWN":
            direction = "BELOW"
    ask = yes_ask if direction == "ABOVE" else no_ask if direction == "BELOW" else None
    bid = yes_bid if direction == "ABOVE" else no_bid if direction == "BELOW" else None
    spread = ask - bid if ask is not None and bid is not None else None
    issues = []
    if not verified: issues.append("Direction/settlement terms unverified")
    if not fresh: issues.append("Quotes missing or stale")
    if not valid: issues.append("Invalid or incomplete bid/ask")
    if not stats.get("ready"): issues.append("Gathering observations")
    if direction == "UNCERTAIN": issues.append("Trend and momentum disagree")
    if spread is not None and spread > .05: issues.append("Wide spread")
    if ask is not None and ask >= .80: issues.append("Expensive entry")
    if remaining is None or remaining <= 120: issues.append("Near settlement")
    if issues:
        decision = "WAIT / NO ENTRY"
    else:
        decision = "PAPER WATCH ONLY"
    st.markdown("### 👑 MARKET INTELLIGENCE · SETTLEMENT WATCH")
    a,b,c,d = st.columns(4, gap="small")
    a.metric("Market outlook", direction)
    b.metric("Entry assessment", decision)
    c.metric("Current entry ask", cents(ask))
    d.metric("Time remaining", f"{remaining//60:02d}:{remaining%60:02d}" if remaining is not None else "—")
    e,f,g,h = st.columns(4, gap="small")
    e.metric("Quote trend", stats.get("trend","WARMING"))
    f.metric("Momentum", stats.get("momentum","—"))
    g.metric("Quote RSI", f"{stats['rsi']:.0f}" if stats.get("rsi") is not None else "—")
    h.metric("Bid/ask spread", cents(spread))
    st.markdown("**🔒 STRONG LOCK: NOT VALIDATED** · A high-confidence settlement prediction requires independent forward testing and underlying-price data.")
    if locked:
        st.caption(f"Existing historical/strategy LOCK: {locked.get('call','—')} · {locked.get('status','—')}. Not a validated new STRONG LOCK.")
    if issues:
        st.caption("⚠️ " + " · ".join(issues))
    st.caption("Indicators use Kalshi YES quote midpoints, not underlying Oil/Bitcoin candles. This is not a calibrated win probability, a definite win, or a recommendation to buy.")
    with st.expander("📈 Quote history & experimental trend lines", expanded=True):
        if len(history) >= 3:
            df = pd.DataFrame({"Time":[v[0] for v in history],
                               "YES midpoint (¢)":[v[1]*100 for v in history]})
            st.line_chart(df.set_index("Time"), height=225)
            if stats.get("ready"):
                st.caption(f"Observed quote range: {stats['support']*100:.1f}¢ to {stats['resistance']*100:.1f}¢. These are not underlying-price support/resistance levels.")
        else:
            st.info("Collecting quote observations. A TradingView-style underlying candlestick chart requires a separate verified data feed.")


# ============================================================
# LIVE MARKET GRID
# ============================================================

@st.fragment(run_every="1s")
def live_grid():
    markets, errors = active_markets()

    st.markdown("### 👑 LIVE MARKET INTELLIGENCE · PAPER ONLY")
    st.caption("Oil first · Bitcoin available via selector. Experimental observations are not validated trading recommendations.")
    selected = st.radio(
        "Market", ["🛢️ WTI Oil", "₿ Bitcoin"],
        horizontal=True, label_visibility="collapsed", key="qe_market_selector"
    )
    chosen = SERIES[1] if selected == "🛢️ WTI Oil" else SERIES[0]
    market_card(*chosen, markets, errors)


live_grid()



# ============================================================
# PERFORMANCE HELPERS
# ============================================================

def performance_status(row):
    status = str(
        row.get("status") or ""
    ).strip().lower()

    if status in ("win", "won", "correct"):
        return "WIN"

    if status in ("loss", "lost", "incorrect"):
        return "LOSS"

    return "PENDING"


def performance_side(row):
    side = str(
        row.get("side")
        or row.get("call")
        or ""
    ).strip().upper()

    if side in ("YES", "ABOVE", "UP"):
        return "ABOVE"

    if side in ("NO", "BELOW", "DOWN"):
        return "BELOW"

    return side or "—"


def counts(rows):
    wins = sum(
        performance_status(row) == "WIN"
        for row in rows
    )

    losses = sum(
        performance_status(row) == "LOSS"
        for row in rows
    )

    pending = sum(
        performance_status(row) == "PENDING"
        for row in rows
    )

    graded = wins + losses

    rate = (
        100.0 * wins / graded
        if graded
        else 0.0
    )

    return wins, losses, pending, rate


def performance_entry_price(row):
    """Estimated one-contract entry cost from recorded entry quotes.

    Early/Locked trackers historically saved YES bid/ask instead of
    entry_price. ABOVE buys YES at YES ask; BELOW buys NO at the
    complementary NO ask (1 - YES bid). These are quote-based paper
    estimates, not executed fills.
    """
    for key in (
        "entry_price",
        "ask_price",
        "entry_ask",
        "price",
    ):
        value = number(row.get(key))
        if value is None:
            continue
        if 1 < value <= 100:
            value /= 100
        if 0 <= value <= 1:
            return value

    side = performance_side(row)
    yes_bid = number(row.get("yes_bid"))
    yes_ask = number(row.get("yes_ask"))

    if yes_bid is not None and 1 < yes_bid <= 100:
        yes_bid /= 100
    if yes_ask is not None and 1 < yes_ask <= 100:
        yes_ask /= 100

    if side == "ABOVE" and yes_ask is not None and 0 <= yes_ask <= 1:
        return yes_ask
    if side == "BELOW" and yes_bid is not None and 0 <= yes_bid <= 1:
        return 1.0 - yes_bid

    return None


def paper_pnl(rows, fee=0.02):
    """
    Estimated paper P&L per one contract.

    Fee is a configurable approximation, not
    a calculation of actual Kalshi fees.
    """

    total = 0.0
    priced = 0

    for row in rows:
        status = performance_status(row)

        if status not in ("WIN", "LOSS"):
            continue

        price = performance_entry_price(row)

        if price is None:
            continue

        if status == "WIN":
            total += 1.0 - price - fee
        else:
            total -= price + fee

        priced += 1

    return total, priced


def display_market(row):
    series = market_series(row)

    if series == "KXBTC15M":
        return "₿ Bitcoin"

    return str(
        row.get("market_name")
        or row.get("market")
        or "Unknown"
    )


def display_stage(row):
    stage = str(
        row.get("stage") or ""
    ).upper()

    if stage == "EARLY_10M":
        return "10m Early"

    if stage == "FINAL_5M":
        return "5m Final"

    return stage or "—"


def display_time(value):
    parsed = parse_time(value)

    if parsed is None:
        return "—"

    return parsed.astimezone(
        CENTRAL
    ).strftime("%m/%d %I:%M %p")


def history_table(rows, strategy):
    output = []

    for row in rows:
        price = performance_entry_price(row)

        score = number(
            row.get("score")
        )

        output.append(
            {
                "Strategy": strategy,
                "Market": display_market(row),
                "Ticker": row.get(
                    "ticker", "—"
                ),
                "Stage": display_stage(row),
                "Call": performance_side(row),
                "Entry": (
                    cents(price)
                    if price is not None
                    else "—"
                ),
                "Score": (
                    f"{score:.1f}"
                    if score is not None
                    else "—"
                ),
                "Result": str(
                    row.get("official_result")
                    or row.get("result")
                    or "—"
                ).upper(),
                "Status": performance_status(row),
                "Time": display_time(
                    row.get("observed_at")
                    or row.get("locked_at")
                    or row.get("created_at")
                ),
            }
        )

    return output


# ============================================================
# PERFORMANCE GROUPS
# ============================================================

dual_early_rows = [
    row for row in dual_rows
    if row.get("stage") == "EARLY_10M"
]

dual_final_rows = [
    row for row in dual_rows
    if row.get("stage") == "FINAL_5M"
]

v2_early_rows = [
    row for row in v2_rows
    if row.get("stage") == "EARLY_10M"
]

v2_final_rows = [
    row for row in v2_rows
    if row.get("stage") == "FINAL_5M"
]

performance_groups = [
    ("🔒 Locked", locked_rows),
    ("🌅 Early", early_rows),
    ("💰 WATCH", paper_rows),
    ("🔐 Strong LOCK", strong_rows),
    ("👑 Dual 10m", dual_early_rows),
    ("👑 Dual 5m", dual_final_rows),
    ("👑 V2 10m SHADOW", v2_early_rows),
    ("👑 V2 5m SHADOW", v2_final_rows),
]

all_rows = (
    locked_rows
    + early_rows
    + paper_rows
    + strong_rows
    + dual_rows
    + v2_rows
)


# Compact Bitcoin-only workspace. Strategies above remain unchanged.
overview_tab, history_tab, diagnostics_tab = st.tabs(
    ["📊 Overview", "📚 History", "🔎 Diagnostics"]
)

with overview_tab:
    # ============================================================
    # DUAL STRONG LOCK V2 SHADOW PERFORMANCE
    # ============================================================

    st.subheader("👑 V2 Shadow · Bitcoin Performance")

    st.caption(
        "V2 is a separate experimental paper-only "
        "strategy. Entries are stored in Supabase "
        "when the database is connected. "
        "Official Kalshi results determine "
        "WIN or LOSS."
    )

    v2_wins, v2_losses, v2_pending, v2_rate = (
        counts(v2_rows)
    )

    v2_pnl, v2_priced = paper_pnl(
        v2_rows
    )

    v2_columns = st.columns(5)

    v2_columns[0].metric(
        "V2 Entries",
        len(v2_rows),
    )

    v2_columns[1].metric(
        "V2 Wins",
        v2_wins,
    )

    v2_columns[2].metric(
        "V2 Losses",
        v2_losses,
    )

    v2_columns[3].metric(
        "V2 Win Rate",
        f"{v2_rate:.1f}%",
    )

    v2_columns[4].metric(
        "V2 Paper P&L",
        f"${v2_pnl:+.2f}",
    )

    st.caption(
        f"V2 pending settlement: {v2_pending} "
        f"· Priced graded entries: {v2_priced}"
    )

    if v2_store is None:
        st.warning(
            "V2 Supabase storage is unavailable. "
            "Session-only signals will not survive "
            "an application restart unless saved."
        )
    else:
        st.caption("🟢 V2 Supabase tracker connected")



    # ============================================================
    # V2 STAGE BREAKDOWN
    # ============================================================

    st.markdown("**⏱️ V2 10-Minute vs 5-Minute**")

    v2_stage_table = []

    for stage, label in (
        ("EARLY_10M", "10-Minute Early"),
        ("FINAL_5M", "5-Minute Final"),
    ):
        stage_rows = [
            row for row in v2_rows
            if row.get("stage") == stage
        ]

        wins, losses, pending, rate = counts(
            stage_rows
        )

        pnl, priced = paper_pnl(
            stage_rows
        )

        v2_stage_table.append(
            {
                "Stage": label,
                "Entries": len(stage_rows),
                "Wins": wins,
                "Losses": losses,
                "Pending": pending,
                "Win Rate": f"{rate:.1f}%",
                "Paper P&L": f"${pnl:+.2f}",
            }
        )

    st.dataframe(
        v2_stage_table,
        use_container_width=True,
        hide_index=True,
    )



    with st.expander("📊 All strategies · full scoreboard", expanded=False):
        # ============================================================
        # PERFORMANCE CENTER
        # ============================================================

        st.subheader("📊 All-Strategy Performance")

        st.caption(
            "Read-only strategy tracking. "
            "Each strategy entry is counted separately. "
            "Paper P&L is hypothetical, assumes one "
            "contract per priced entry, and uses an "
            "estimated 2¢ fee per contract."
        )

        total_wins, total_losses, total_pending, total_rate = (
            counts(all_rows)
        )

        total_pnl, total_priced = paper_pnl(
            all_rows
        )

        overall_columns = st.columns(5)

        overall_columns[0].metric(
            "Wins",
            total_wins,
        )

        overall_columns[1].metric(
            "Losses",
            total_losses,
        )

        overall_columns[2].metric(
            "Win Rate",
            f"{total_rate:.1f}%",
        )

        overall_columns[3].metric(
            "Pending",
            total_pending,
        )

        overall_columns[4].metric(
            "Paper P&L",
            f"${total_pnl:+.2f}",
        )

        st.caption(
            f"Paper P&L includes {total_priced} "
            "graded entries with usable entry prices."
        )



        # ============================================================
        # STRATEGY SCOREBOARD
        # ============================================================

        st.markdown("**🏆 Strategy Scoreboard**")

        scoreboard = []

        for strategy_name, strategy_rows in performance_groups:
            wins, losses, pending, rate = counts(
                strategy_rows
            )

            pnl, priced = paper_pnl(
                strategy_rows
            )

            scoreboard.append(
                {
                    "Strategy": strategy_name,
                    "Entries": len(strategy_rows),
                    "Wins": wins,
                    "Losses": losses,
                    "Pending": pending,
                    "Win Rate": f"{rate:.1f}%",
                    "Paper P&L": f"${pnl:+.2f}",
                    "Priced": priced,
                }
            )

        st.dataframe(
            scoreboard,
            use_container_width=True,
            hide_index=True,
        )




    st.divider()
    st.subheader("🛢️ WTI Oil · Original Signal Shadow")
    st.caption("Separate from Bitcoin. Original Early and Locked calls, graded against official settlement. Quote-based estimated entry prices, one hypothetical contract per signal and an approximate 2¢ fee. No real trades.")
    oil_groups = [("🛢️ Oil Locked", oil_locked_rows), ("🛢️ Oil Early", oil_early_rows)]
    oil_scoreboard = []
    for oil_name, oil_data in oil_groups:
        ow, ol, op, orate = counts(oil_data)
        opnl, opriced = paper_pnl(oil_data)
        oil_scoreboard.append({"Strategy": oil_name, "Entries": len(oil_data), "Wins": ow, "Losses": ol, "Pending": op, "Win Rate": f"{orate:.1f}%" if ow + ol else "—", "Est. P&L": f"${opnl:+.2f}" if opriced else "N/A", "Priced": opriced})
    st.dataframe(oil_scoreboard, use_container_width=True, hide_index=True)
    oil_all = oil_locked_rows + oil_early_rows
    oil_wins, oil_losses, oil_pending, oil_rate = counts(oil_all)
    oil_total, oil_priced = paper_pnl(oil_all)
    st.caption(f"Oil totals: {oil_wins} wins · {oil_losses} losses · {oil_pending} pending · {oil_priced} priced graded signals · estimated P&L: {f'${oil_total:+.2f}' if oil_priced else 'N/A'} · First target: 50 graded signals")



with history_tab:
    st.subheader("🛢️ WTI Oil · Paper Signal History")
    oil_history = history_table(oil_locked_rows, "Oil Locked") + history_table(oil_early_rows, "Oil Early")
    if oil_history:
        st.dataframe(oil_history, use_container_width=True, hide_index=True)
    else:
        st.caption("Waiting for qualifying WTI Oil Early/Locked signals.")
    st.divider()
    # ============================================================
    # PERMANENT V2 ENTRY HISTORY
    # ============================================================

    st.subheader("💾 Permanent V2 Entry History")

    if v2_rows:
        v2_history = history_table(
            v2_rows,
            "Dual Strong LOCK 2.0",
        )

        st.dataframe(
            v2_history,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(
            "No permanent V2 entries recorded yet. "
            "A qualifying signal must occur while "
            "the app is evaluating its entry window."
        )



    # ============================================================
    # FULL TRACKER HISTORY
    # ============================================================

    st.divider()

    st.header("📚 Complete Strategy History")

    all_history = []

    for strategy_name, strategy_rows in performance_groups:
        all_history.extend(
            history_table(
                strategy_rows,
                strategy_name,
            )
        )

    if all_history:
        st.dataframe(
            all_history,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "📥 Export Tracker History (CSV)",
            data=pd.DataFrame(
                all_history
            ).to_csv(index=False),
            file_name=(
                "queens_edge_254_history.csv"
            ),
            mime="text/csv",
        )
    else:
        st.info(
            "No strategy history is available yet."
        )



    # ============================================================
    # OFFICIAL SETTLEMENT SUMMARY
    # ============================================================

    st.divider()

    st.header("🏁 Official Settlement Summary")

    settlement_rows = []

    for series, label in (
        ("KXBTC15M", "₿ Bitcoin"),
        ("KXWTI15M", "🛢️ WTI Oil"),
    ):
        try:
            recent = fetch_recent(series)

            for market in recent:
                result = str(
                    market.get("result")
                    or ""
                ).upper()

                settlement_rows.append(
                    {
                        "Market": label,
                        "Ticker": market.get(
                            "ticker", "—"
                        ),
                        "Target": target(market),
                        "Result": result,
                        "Closed": display_time(
                            market.get("close_time")
                        ),
                    }
                )

        except Exception as exc:
            st.caption(
                f"{label} settlements unavailable: {exc}"
            )

    if settlement_rows:
        st.dataframe(
            settlement_rows,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption(
            "Official settlement history "
            "is temporarily unavailable."
        )



with diagnostics_tab:
    # ============================================================
    # DUAL STRONG LOCK MONITOR
    # ============================================================

    st.divider()

    st.header("🔐 Dual Strong LOCK Signal Monitor")

    st.caption(
        "Diagnostic sampling of strategy evaluations. "
        "Checks are not the same as unique contracts."
    )

    dual_monitor_rows = []

    for market_name, series, emoji in SERIES:
        for stage in (
            "EARLY_10M",
            "FINAL_5M",
        ):
            summary = monitor_summary(
                series, stage
            )

            dual_monitor_rows.append(
                {
                    "Market": (
                        f"{emoji} {market_name}"
                    ),
                    **summary,
                }
            )

    st.dataframe(
        dual_monitor_rows,
        use_container_width=True,
        hide_index=True,
    )

    dual_events = [
        e for e in st.session_state.get("dual_monitor_events", [])
        if e.get("Market") == "Bitcoin"
    ]

    with st.expander(
        "📋 Recent Dual Strong LOCK evaluations"
    ):
        if dual_events:
            st.dataframe(
                dual_events[:50],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(
                "No Dual Strong LOCK evaluations "
                "recorded in this session yet."
            )



    # ============================================================
    # V2 SIGNAL MONITOR
    # ============================================================

    st.subheader("📡 V2 Signal Monitor")

    v2_events = [
        e for e in st.session_state.get("dual_v2_monitor_events", [])
        if str(e.get("Ticker", "")).upper().startswith("KXBTC15M-")
    ]

    v2_enter_events = [
        event for event in v2_events
        if event.get("Decision") == "ENTER"
    ]

    v2_wait_events = [
        event for event in v2_events
        if event.get("Decision") == "WAIT"
    ]

    session_entries = st.session_state.get(
        "dual_v2_shadow_entries", {}
    )

    unique_v2_enters = set()

    for event in v2_enter_events:
        unique_v2_enters.add(
            (
                event.get("Ticker"),
                event.get("Stage"),
            )
        )

    for key in session_entries:
        unique_v2_enters.add(key)

    for row in v2_rows:
        unique_v2_enters.add(
            (
                row.get("ticker"),
                row.get("stage"),
            )
        )

    monitor_columns = st.columns(4)

    monitor_columns[0].metric(
        "V2 Evaluations",
        len(v2_events),
    )

    monitor_columns[1].metric(
        "V2 WAIT",
        len(v2_wait_events),
    )

    monitor_columns[2].metric(
        "Unique V2 ENTER",
        len(unique_v2_enters),
    )

    monitor_columns[3].metric(
        "Persisted V2 Entries",
        len(v2_rows),
    )

    st.caption(
        "Evaluations are session samples. "
        "Unique ENTER includes session observations "
        "and persisted entries; it is not an "
        "all-time evaluation count."
    )

    with st.expander(
        "🔎 Recent V2 evaluations",
        expanded=False,
    ):
        if v2_events:
            st.dataframe(
                v2_events[:100],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(
                "Waiting for V2 evaluation windows."
            )



    # ============================================================
    # V2 SUPABASE SAVE ERRORS
    # ============================================================

    v2_save_errors = st.session_state.get(
        "dual_v2_save_errors", {}
    )

    if v2_save_errors:
        with st.expander(
            "⚠️ V2 save issues",
            expanded=True,
        ):
            for key, error in v2_save_errors.items():
                st.warning(
                    f"{key[0]} · {key[1]}: {error}"
                )



    # ============================================================
    # FLIP RADAR HISTORY
    # ============================================================

    st.divider()

    st.header("⚡ Flip Radar History")

    radar_events = [
        e for e in st.session_state.get("radar_events", [])
        if str(e.get("ticker", "")).upper().startswith("KXBTC15M-")
    ]

    if radar_events:
        radar_table = []

        for event in radar_events[:50]:
            event_time = event.get("time")

            radar_table.append(
                {
                    "Market": event.get(
                        "market", "—"
                    ),
                    "Signal": event.get(
                        "label", "—"
                    ),
                    "Move": (
                        f"{event.get('move', 0):+.2f}¢"
                    ),
                    "Time": (
                        event_time.astimezone(
                            CENTRAL
                        ).strftime("%I:%M:%S %p")
                        if isinstance(
                            event_time, datetime
                        )
                        else "—"
                    ),
                }
            )

        st.dataframe(
            radar_table,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption(
            "Monitoring live price reversals "
            "and sudden momentum changes."
        )


    # ============================================================
    # FOOTER
    # ============================================================

    st.divider()

    st.markdown(
        """
        <div style="
            text-align:center;
            padding:18px 10px;
            opacity:0.85;
        ">
            <div style="
                font-size:20px;
                font-weight:800;
                color:#b78cff;
            ">
                👑 QUEEN'S EDGE | 254
            </div>
            <div style="
                font-size:12px;
                margin-top:7px;
            ">
                Bitcoin ·
                Strong LOCK · Dual LOCK ·
                V2 Shadow
            </div>
            <div style="
                font-size:11px;
                margin-top:8px;
            ">
                READ-ONLY · PAPER SIGNALS ONLY ·
                NO AUTOMATIC TRADING
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# QUEEN'S EDGE | 254 — COMPACT V3 VISUAL FIX
# Banner clock alignment + dashboard readability
# No changes to strategy logic or Supabase tracking
# ============================================================

st.markdown(
    """
    <style>
    /* Cover the original printed banner clock */
    .queen-live-clock {
        left: 0% !important;
        top: 43% !important;
        width: 16% !important;
        height: 57% !important;
        z-index: 10 !important;
        background: #0a0b1c !important;
        color: #f5edff !important;
    }

    /* Improve dashboard text visibility */
    .stApp {
        color: #f5edff !important;
    }

    .stApp p,
    .stApp label,
    .stApp [data-testid="stCaptionContainer"] {
        color: #d8c8ed !important;
    }

    .stApp h1,
    .stApp h2,
    .stApp h3 {
        color: #ead6ff !important;
    }

    .quote-box.above {
        color: #8affc8 !important;
    }

    .quote-box.below {
        color: #ffb0c1 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
# Compact screen spacing (visual only)
st.markdown("""<style>
[data-testid="stMetric"] {background:rgba(65,35,112,.22);border:1px solid rgba(165,117,230,.2);border-radius:12px;padding:8px 10px}
[data-testid="stMetricLabel"] {font-size:.8rem}
[data-testid="stMetricValue"] {font-size:clamp(1rem,1.6vw,1.5rem)}
[data-testid="stVerticalBlock"] {gap:.55rem}
</style>""", unsafe_allow_html=True)
