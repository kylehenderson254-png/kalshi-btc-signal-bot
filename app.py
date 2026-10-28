
"""QUEEN'S EDGE | 254 — BTC + WTI OIL COMMAND CENTER.
Read-only Kalshi dashboard. All trading signals are paper-only.
"""

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
from paper_tracker import (
    from_secrets as paper_store_from_secrets,
    grade_pending as paper_grade_pending,
)
from strong_lock_tracker import (
    from_secrets as strong_store_from_secrets,
    evaluate as strong_evaluate,
    grade_pending as strong_grade_pending,
)

API = "https://api.elections.kalshi.com/trade-api/v2"
CENTRAL = ZoneInfo("America/Chicago")

SERIES = [
    ("Bitcoin", "KXBTC15M", "₿"),
    ("WTI Oil", "KXWTI15M", "🛢️"),
]

st.set_page_config(
    page_title="QUEEN'S EDGE | 254",
    page_icon="👑",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .stApp {
        background: radial-gradient(
            ellipse at top,
            #1e103b 0%,
            #090817 55%,
            #05050c 100%
        );
        color: #eee9ff;
    }
    .block-container {
        max-width: 1500px;
        padding-top: 1rem;
    }
    h1, h2, h3 {
        color: #dfc7ff !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #6540a3 !important;
        border-radius: 16px !important;
    }
    .queen-title {
        text-align: center;
        font-size: clamp(1.8rem, 4vw, 3rem);
        font-weight: 900;
        color: #e0b9ff;
        letter-spacing: .05em;
    }
    .queen-subtitle {
        text-align: center;
        color: #b8a2e7;
        letter-spacing: .12em;
        margin-bottom: 1.3rem;
    }
    .quote-box {
        border-radius: 12px;
        padding: 14px;
        border: 1px solid;
        text-align: center;
        min-height: 110px;
    }
    .above {
        background: rgba(20, 150, 90, .12);
        border-color: #198e60;
        color: #75efb3;
    }
    .below {
        background: rgba(190, 45, 75, .12);
        border-color: #b63d5a;
        color: #ffa3b7;
    }
    .quote-price {
        font-size: 1.25rem;
        font-weight: 800;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

for key, default in {
    "quote_history": {},
    "radar_events": [],
    "last_good_markets": {},
    "last_good_times": {},
    "strong_attempts": {},
    "dual_attempts": {},
    "dual_save_status": {},
    "dual_last_reason": {},
}.items():
    st.session_state.setdefault(key, default)


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
    dollars = number(market.get(f"{side}_{kind}_dollars"))
    if dollars is not None:
        return dollars
    cents = number(market.get(f"{side}_{kind}"))
    return cents / 100 if cents is not None else None


def midpoint(market):
    bid = quote(market, "yes", "bid")
    ask = quote(market, "yes", "ask")
    if bid is not None and ask is not None and 0 <= bid <= ask <= 1:
        return (bid + ask) / 2
    return None


def cents(value):
    return f"{value * 100:.1f}¢" if value is not None else "—"


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
    return str(market.get("yes_sub_title") or "See rules")


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
    return any(re.search(pattern, text) for pattern in patterns)


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
        if str(item.get("result", "")).lower() in ("yes", "no")
    ]
    rows.sort(
        key=lambda item: (
            parse_time(item.get("close_time"))
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    return rows[:10]


def current_market(markets):
    now = datetime.now(timezone.utc)
    eligible = [
        m for m in markets
        if parse_time(m.get("close_time")) is None
        or parse_time(m.get("close_time")) > now
    ]
    if not eligible:
        return None
    active = [
        m for m in eligible
        if parse_time(m.get("open_time")) is None
        or parse_time(m.get("open_time")) <= now
    ]
    return min(
        active or eligible,
        key=lambda m: (
            parse_time(m.get("close_time"))
            or datetime.max.replace(tzinfo=timezone.utc)
        ),
    )


def fetch_series(series):
    try:
        return series, current_market(fetch_markets(series)), None
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
                st.session_state["last_good_markets"][series] = market
                st.session_state["last_good_times"][series] = time.time()
            else:
                results[series] = st.session_state[
                    "last_good_markets"
                ].get(series)
                if error:
                    errors[series] = error
    return results, errors


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
        st.warning(f"Tracker history unavailable: {exc}")
        return []


def market_series(row):
    """Identify BTC/Oil from series, ticker, or historical market name."""
    series = str(row.get("series_ticker") or "").upper()
    ticker = str(row.get("ticker") or "").upper()
    name = str(row.get("market_name") or row.get("market") or "").lower()
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
    return [row for row in rows if market_series(row) is not None]


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
    st.caption("Read-only. No automatic trading.")


call_store = safe_store(store_from_secrets)
early_store = safe_store(early_store_from_secrets)
paper_store = safe_store(paper_store_from_secrets)
strong_store = safe_store(strong_store_from_secrets)
dual_store = safe_store(dual_store_from_secrets)

tracker_markets, tracker_errors = active_markets()

if call_store:
    try:
        process(call_store, current_by_series=tracker_markets)
    except Exception as exc:
        st.sidebar.warning(f"Locked tracker: {exc}")

if early_store:
    try:
        process_early(early_store, current_by_series=tracker_markets)
    except Exception as exc:
        st.sidebar.warning(f"Early tracker: {exc}")

locked_rows = btc_oil_rows(safe_rows(call_store))
early_rows = btc_oil_rows(safe_rows(early_store))
paper_rows = btc_oil_rows(safe_rows(paper_store))
strong_rows = btc_oil_rows(safe_rows(strong_store))
dual_rows = btc_oil_rows(safe_rows(dual_store))

if paper_store:
    try:
        paper_grade_pending(paper_store, safe_rows(paper_store))
        paper_rows = btc_oil_rows(safe_rows(paper_store))
    except Exception as exc:
        st.sidebar.warning(f"WATCH grading: {exc}")

if strong_store:
    try:
        strong_grade_pending(strong_store, safe_rows(strong_store))
        strong_rows = btc_oil_rows(safe_rows(strong_store))
    except Exception as exc:
        st.sidebar.warning(f"Strong LOCK grading: {exc}")

if dual_store:
    try:
        dual_store.grade(safe_rows(dual_store))
        dual_rows = btc_oil_rows(safe_rows(dual_store))
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


banner = Path(__file__).with_name("queens_banner.jpg")
if banner.exists():
    encoded = base64.b64encode(banner.read_bytes()).decode("ascii")
    st.markdown(
        f'<img src="data:image/jpeg;base64,{encoded}" '
        'style="width:100%;max-height:210px;object-fit:cover;'
        'border-radius:15px;">',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="queen-title">👑 QUEEN’S EDGE | 254</div>',
        unsafe_allow_html=True,
    )

st.markdown(
    '<div class="queen-subtitle">'
    '₿ BITCOIN + 🛢️ WTI OIL COMMAND CENTER'
    '</div>',
    unsafe_allow_html=True,
)
st.caption(
    "⚡ Live Kalshi quotes · 🔐 Dual Strong LOCK "
    "· 🐋 Pressure Radar · 📊 Paper Performance"
)


def update_history(ticker, name, mid):
    if mid is None:
        return "No quote"

    now = datetime.now(timezone.utc)
    history = st.session_state["quote_history"].setdefault(ticker, [])

    if not history or (now - history[-1][0]).total_seconds() >= 0.8:
        history.append((now, mid, "YES midpoint"))
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
    if (
        not events
        or events[0]["ticker"] != ticker
        or events[0]["time"] != now
    ):
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


def strong_lock_panel(name, series, ticker, market, locked, verified):
    if not locked:
        return

    history = st.session_state["quote_history"].get(ticker, [])

    try:
        decision, reason, ask, strength = strong_evaluate(
            locked, market, history, verified
        )
        st.markdown(f"**🔐 Strong LOCK: {decision}**")
        st.caption(reason)

        if (
            decision == "ENTER"
            and strong_store is not None
            and ask is not None
        ):
            attempts = st.session_state["strong_attempts"]
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
                        "yes_midpoint": number(
                            locked.get("yes_midpoint")
                        ) or 0,
                        "close_time": market.get("close_time"),
                        "status": "pending",
                        "observed_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }
                )
    except Exception as exc:
        st.caption(f"Strong LOCK unavailable: {exc}")


def dual_panel(name, series, ticker, market, close, verified):
    st.markdown("**🔐 Dual Strong LOCK**")

    if close is None:
        st.caption("Waiting for contract close time")
        return

    seconds_left = (
        close - datetime.now(timezone.utc)
    ).total_seconds()
    history = st.session_state["quote_history"].get(ticker, [])
    analysis = dual_analyze(
        market, history, seconds_left, verified
    )

    stages = (
        ("EARLY_10M", "🌅 10m Early", 540, 660),
        ("FINAL_5M", "🔒 5m Final", 240, 360),
    )

    for stage, label, lower, upper in stages:
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
                f"{label}: SAVED {existing.get('side')} "
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
                    f"{label}: SKIPPED · Last check: {last_reason}"
                )
            else:
                st.caption(
                    f"{label}: Window passed · No saved entry"
                )
            continue

        if analysis.get("decision") != "ENTER":
            reason = str(
                analysis.get("reason")
                or "Conditions not met"
            )
            st.session_state["dual_last_reason"][key] = reason
            st.caption(f"{label}: WAIT · {reason}")
            continue

        st.caption(
            f"{label}: ENTER candidate · "
            f"{analysis.get('reason')}"
        )

        if dual_store is None:
            st.error("Dual Strong LOCK storage unavailable")
            continue

        attempts = st.session_state["dual_attempts"]

        if time.time() - attempts.get(key, 0) >= 20:
            attempts[key] = time.time()
            try:
                payload = {
                    "ticker": ticker,
                    "stage": stage,
                    "market_name": name,
                    "series_ticker": series,
                    "side": analysis["side"],
                    "entry_price": analysis["entry_price"],
                    "yes_midpoint": analysis["yes_midpoint"],
                    "close_time": market.get("close_time"),
                    "observed_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "status": "pending",
                }
                result = dual_store.record_once(payload)
                st.session_state[
                    "dual_save_status"
                ][key] = result
            except Exception as exc:
                st.session_state[
                    "dual_save_status"
                ][key] = f"SAVE ERROR: {exc}"

        st.caption(
            f"{label}: "
            f"{st.session_state['dual_save_status'].get(key, 'Checking…')}"
        )


def market_card(name, series, emoji, markets, errors):
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
                        close - datetime.now(timezone.utc)
                    ).total_seconds()
                ),
            )
            minutes, seconds = divmod(remaining, 60)
            countdown = f"{minutes:02d}:{seconds:02d}"
        else:
            countdown = "—"

        top_left, top_right = st.columns([1.5, 1])
        with top_left:
            st.caption(f"TARGET · {target(market)}")
        with top_right:
            st.markdown(f"**⏱️ {countdown}**")

        if series in errors:
            st.warning(
                "Latest quote request failed; showing cached data"
            )
        else:
            stamp = st.session_state[
                "last_good_times"
            ].get(series)
            if stamp is not None:
                st.caption(
                    f"📡 Quote received "
                    f"{time.time() - stamp:.1f}s ago"
                )

        yes_bid = quote(market, "yes", "bid")
        yes_ask = quote(market, "yes", "ask")
        no_bid = quote(market, "no", "bid")
        no_ask = quote(market, "no", "ask")

        left, right = st.columns(2, gap="small")

        with left:
            st.markdown(
                '<div class="quote-box above">'
                f'<b>{"ABOVE TARGET" if verified else "YES"}</b>'
                f'<p>BID <span class="quote-price">'
                f'{cents(yes_bid)}</span></p>'
                f'<p>ASK <span class="quote-price">'
                f'{cents(yes_ask)}</span></p>'
                '</div>',
                unsafe_allow_html=True,
            )

        with right:
            st.markdown(
                '<div class="quote-box below">'
                f'<b>{"BELOW TARGET" if verified else "NO"}</b>'
                f'<p>BID <span class="quote-price">'
                f'{cents(no_bid)}</span></p>'
                f'<p>ASK <span class="quote-price">'
                f'{cents(no_ask)}</span></p>'
                '</div>',
                unsafe_allow_html=True,
            )

        mid = midpoint(market)
        radar = update_history(ticker, name, mid)

        if not verified or mid is None:
            lean = "⚪ NO VERIFIED CALL"
        elif mid >= 0.60:
            lean = "🟢 ABOVE TARGET"
        elif mid <= 0.40:
            lean = "🔴 BELOW TARGET"
        else:
            lean = "⚪ NO CLEAR CALL"

        st.markdown(f"**Market lean: {lean}**")
        st.caption(
            f"YES midpoint: {cents(mid)} · Radar: {radar}"
        )

        history = st.session_state[
            "quote_history"
        ].get(ticker, [])

        with st.expander("📈 Live chart", expanded=False):
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
                st.caption("Collecting live quote history")

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
            name, series, ticker, market, locked, verified
        )
        dual_panel(
            name, series, ticker, market, close, verified
        )

        with st.expander("🐋 Buying / Selling Pressure"):
            st.caption(
                "Experimental pressure indicators; "
                "no verified whale identities"
            )
            try:
                show_pressure_radar(name, ticker)
            except Exception as exc:
                st.caption(
                    f"Pressure unavailable: {exc}"
                )

        with st.expander("📋 Contract details"):
            st.caption(f"Ticker: {ticker}")
            st.write(
                market.get("rules_primary")
                or "See Kalshi rules"
            )
            if market.get("rules_secondary"):
                st.write(market["rules_secondary"])


@st.fragment(run_every="1s")
def live_grid():
    markets, errors = active_markets()
    st.subheader("⚡ LIVE MARKET COMMAND CENTER")
    left, right = st.columns(2, gap="medium")

    with left:
        market_card(*SERIES[0], markets, errors)

    with right:
        market_card(*SERIES[1], markets, errors)


live_grid()


st.divider()
st.subheader("📊 BTC + OIL PERFORMANCE CENTER")


def counts(rows):
    wins = sum(
        str(r.get("status", "")).lower() == "win"
        for r in rows
    )
    losses = sum(
        str(r.get("status", "")).lower() == "loss"
        for r in rows
    )
    pending = sum(
        str(r.get("status", "")).lower() == "pending"
        for r in rows
    )
    return wins, losses, pending


def paper_pnl(row):
    status = str(row.get("status", "")).lower()
    price = number(row.get("entry_price"))

    if status not in ("win", "loss"):
        return None
    if price is None or not 0 <= price <= 1:
        return None

    payout = 1 if status == "win" else 0
    return payout - price - 0.02


dual_early_rows = [
    r for r in dual_rows
    if r.get("stage") == "EARLY_10M"
]
dual_final_rows = [
    r for r in dual_rows
    if r.get("stage") == "FINAL_5M"
]

performance_groups = [
    ("🔒 Locked", locked_rows),
    ("🌅 Early", early_rows),
    ("💰 WATCH", paper_rows),
    ("🔐 Strong LOCK", strong_rows),
    ("👑 Dual 10m", dual_early_rows),
    ("👑 Dual 5m", dual_final_rows),
]

summary = []

for label, rows in performance_groups:
    w, l, pending = counts(rows)
    pnl = [
        v
        for row in rows
        if (v := paper_pnl(row)) is not None
    ]
    summary.append(
        {
            "Tracker": label,
            "Wins": w,
            "Losses": l,
            "Win rate": (
                f"{w / (w + l):.1%}"
                if w + l
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
    "*Illustrative one-contract P&L assumes 2¢ fees "
    "and available entry prices. Not actual trading profit."
)

with st.expander("📋 Per-market performance"):
    for label, rows in performance_groups:
        values = []

        for name, series, emoji in SERIES:
            subset = [
                r for r in rows
                if market_series(r) == series
            ]
            w, l, p = counts(subset)

            values.append(
                {
                    "Tracker": label,
                    "Market": f"{emoji} {name}",
                    "Wins": w,
                    "Losses": l,
                    "Win rate": (
                        f"{w / (w + l):.1%}"
                        if w + l
                        else "—"
                    ),
                    "Pending": p,
                }
            )

        st.dataframe(
            values,
            hide_index=True,
            use_container_width=True,
        )

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


st.divider()

left_info, right_info = st.columns(
    2, gap="medium"
)

with left_info:
    st.markdown("**📜 Last 10 settled markets**")

    for name, series, emoji in SERIES:
        try:
            recent = fetch_recent(series)
            arrows = [
                (
                    "🟢↑"
                    if str(
                        r.get("result", "")
                    ).lower() == "yes"
                    else "🔴↓"
                )
                for r in reversed(recent)
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
    st.markdown("**🚨 Latest Flip Radar alerts**")

    events = [
        e
        for e in st.session_state["radar_events"]
        if e.get("market") in (
            "Bitcoin", "WTI Oil"
        )
    ]

    for event in events[:3]:
        stamp = event["time"].astimezone(CENTRAL)
        st.caption(
            f"{event['market']} · {event['label']} "
            f"· {event['move']:+.1f}¢ "
            f"· {stamp:%I:%M:%S %p}"
        )

    if not events:
        st.caption("Scanning BTC and Oil")

    if len(events) > 3:
        with st.expander("More radar alerts"):
            for event in events[3:15]:
                stamp = event["time"].astimezone(
                    CENTRAL
                )
                st.caption(
                    f"{event['market']} · "
                    f"{event['label']} "
                    f"· {event['move']:+.1f}¢ "
                    f"· {stamp:%I:%M:%S %p}"
                )


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
