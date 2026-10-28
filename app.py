
"""QUEEN'S EDGE | 254
Fast public-API preview dashboard.
One-second countdowns, three-second quote polling.
Read-only: never places orders.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import base64
import re

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
    ("Gold", "KXGOLD15M", "🥇"),
    ("Silver", "KXSILVER15M", "🥈"),
]

st.set_page_config(
    page_title="QUEEN'S EDGE | 254",
    page_icon="👑",
    layout="wide",
)

st.markdown("""
<style>
.stApp {
    background: radial-gradient(
        ellipse at top,
        #1c0b37 0%,
        #070714 48%,
        #05050e 100%
    );
    color: #e9e6f7;
}
.block-container {
    max-width: 1550px;
    padding-top: 0.7rem;
}
[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: #6d28d9 !important;
    border-radius: 14px !important;
}
[data-testid="stSidebar"] {
    background: #0d0920;
}
.stApp h1, .stApp h2, .stApp h3 {
    color: #e8d9ff;
}
.qe-title {
    text-align: center;
    font-size: 2.2rem;
    font-weight: 900;
    color: #d8b4fe;
}
.qe-subtitle {
    text-align: center;
    color: #bca2f5;
    letter-spacing: .12em;
    margin-bottom: 20px;
}
.qe-quote {
    padding: 12px;
    border-radius: 12px;
    border: 1px solid;
    min-height: 105px;
    font-size: .85rem;
}
.qe-yes {
    color: #65e6ac;
    border-color: #178d61;
    background: rgba(15,140,84,.12);
}
.qe-no {
    color: #ff92aa;
    border-color: #ad3358;
    background: rgba(180,35,70,.12);
}
.qe-quote strong {
    font-size: 1.15rem;
}
</style>
""", unsafe_allow_html=True)


# ============================================
# SETTINGS
# ============================================

with st.sidebar:
    st.header("⚙️ Dashboard settings")

    refresh_seconds = st.selectbox(
        "Refresh market prices every",
        [3, 5, 10, 15],
        index=0,
        format_func=lambda n: f"{n} seconds",
    )

    sudden_cents = st.slider(
        "Sudden move threshold (¢)",
        2, 15, 4,
    )

    if st.button(
        "Clear radar history",
        width="stretch",
    ):
        st.session_state["quote_history"] = {}
        st.session_state["radar_events"] = []
        st.rerun()

st.session_state.setdefault("quote_history", {})
st.session_state.setdefault("radar_events", [])


# ============================================
# HELPERS
# ============================================

def number(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def quote(market, side, kind):
    dollars = number(
        market.get(f"{side}_{kind}_dollars")
    )

    if dollars is not None:
        return dollars

    cents = number(
        market.get(f"{side}_{kind}")
    )

    return cents / 100 if cents is not None else None


def fmt_cents(value):
    return (
        f"{value * 100:.1f}¢"
        if value is not None
        else "—"
    )


def parse_time(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    except (ValueError, TypeError):
        return None


def market_target(market):
    for field in (
        "floor_strike",
        "functional_strike",
        "custom_strike",
        "cap_strike",
    ):
        value = market.get(field)

        if value not in (None, ""):
            try:
                return f"${float(value):,.3f}"
            except (ValueError, TypeError):
                return str(value)

    return market.get("yes_sub_title") or "See rules"


def verified_direction(market):
    text = " ".join(
        str(market.get(key) or "")
        for key in (
            "rules_primary",
            "rules_secondary",
            "yes_sub_title",
        )
    ).lower()

    text = " ".join(text.split())

    patterns = [
        r"if the close price.*?is at least.*?resolves to yes",
        r"if.*?simple average.*?is at least.*?resolves to yes",
        r"yes\s+(?:if|means).*?(?:above|higher|greater)",
        r"(?:above|higher|greater).*?(?:settles|resolves) to yes",
    ]

    return any(re.search(p, text) for p in patterns)


# ============================================
# PUBLIC KALSHI API
# ============================================

@st.cache_data(ttl=2, show_spinner=False)
def fetch_markets(series):
    response = requests.get(
        f"{API}/markets",
        params={
            "series_ticker": series,
            "status": "open",
            "limit": 200,
        },
        timeout=6,
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

    markets = [
        m for m in response.json().get("markets", [])
        if str(m.get("result", "")).lower()
        in ("yes", "no")
    ]

    markets.sort(
        key=lambda m: (
            parse_time(m.get("close_time"))
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )

    return markets[:10]


def current_market(markets):
    now = datetime.now(timezone.utc)

    eligible = [
        m for m in markets
        if (
            not parse_time(m.get("close_time"))
            or parse_time(m.get("close_time")) > now
        )
    ]

    if not eligible:
        return None

    active = [
        m for m in eligible
        if (
            not parse_time(m.get("open_time"))
            or parse_time(m.get("open_time")) <= now
        )
    ]

    return min(
        active or eligible,
        key=lambda m: (
            parse_time(m.get("close_time"))
            or datetime.max.replace(tzinfo=timezone.utc)
        ),
    )


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


# ============================================
# ONE-SECOND COUNTDOWN FRAGMENT
# ============================================

@st.fragment(run_every="1s")
def live_countdown(close_time):
    """Update countdown without rerunning the full app."""

    close = parse_time(close_time)

    if close is None:
        st.metric("⏱️ Closes in", "—")
        return

    remaining = max(
        0,
        int(
            (close - datetime.now(timezone.utc))
            .total_seconds()
        ),
    )

    minutes, seconds = divmod(remaining, 60)

    st.metric(
        "⏱️ Closes in",
        f"{minutes:02d}:{seconds:02d}",
    )


# ============================================
# FLIP RADAR
# ============================================

def update_radar(ticker, name, value):
    now = datetime.now(timezone.utc)

    history = (
        st.session_state.quote_history
        .setdefault(ticker, [])
    )

    if value is None:
        return "No quote", 0.0

    if (
        not history
        or (
            now - history[-1][0]
        ).total_seconds() >= max(2, refresh_seconds - 1)
    ):
        history.append(
            (now, value, "YES midpoint")
        )
        del history[:-60]

    if len(history) < 3:
        return "Warming up", 0.0

    previous = history[-2][1] - history[-3][1]
    latest = history[-1][1] - history[-2][1]

    magnitude = abs(latest) * 100

    flip_up = previous <= -0.005 and latest >= 0.005
    flip_down = previous >= 0.005 and latest <= -0.005
    sudden = magnitude >= sudden_cents

    if flip_up:
        label = "🔺 Bullish flip"
    elif flip_down:
        label = "🔻 Bearish flip"
    elif sudden:
        label = "⚡ Sudden move"
    else:
        label = "Scanning"

    if label != "Scanning":
        events = st.session_state.radar_events

        if not events or (
            events[0]["ticker"] != ticker
            or events[0]["time"] != history[-1][0]
        ):
            events.insert(0, {
                "ticker": ticker,
                "market": name,
                "label": label,
                "time": history[-1][0],
                "move": latest * 100,
            })
            del events[50:]

    return label, magnitude


# ============================================
# TRACKER HELPERS
# ============================================

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


# ============================================
# INITIALIZE TRACKERS
# ============================================

call_store = safe_store(store_from_secrets)
early_store = safe_store(early_store_from_secrets)
paper_store = safe_store(paper_store_from_secrets)
strong_store = safe_store(strong_store_from_secrets)

active_markets = {}

for name, series, emoji in SERIES:
    try:
        active_markets[series] = current_market(
            fetch_markets(series)
        )
    except (requests.RequestException, ValueError):
        active_markets[series] = None


# ============================================
# RUN EARLY AND LOCKED TRACKERS
# ============================================

if call_store:
    try:
        process(
            call_store,
            current_by_series=active_markets,
        )
    except Exception as exc:
        st.sidebar.warning(f"Locked tracker: {exc}")

if early_store:
    try:
        process_early(
            early_store,
            current_by_series=active_markets,
        )
    except Exception as exc:
        st.sidebar.warning(f"Early tracker: {exc}")


locked_rows = safe_rows(call_store)
early_rows = safe_rows(early_store)
paper_rows = safe_rows(paper_store)
strong_rows = safe_rows(strong_store)

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

if paper_store:
    try:
        paper_grade_pending(paper_store, paper_rows)
        paper_rows = safe_rows(paper_store)
    except Exception as exc:
        st.sidebar.warning(f"Paper grading: {exc}")

if strong_store:
    try:
        strong_grade_pending(strong_store, strong_rows)
        strong_rows = safe_rows(strong_store)
    except Exception as exc:
        st.sidebar.warning(f"Strong grading: {exc}")


# ============================================
# HEADER
# ============================================

banner = Path(__file__).with_name("queens_banner.jpg")

if banner.exists():
    encoded = base64.b64encode(
        banner.read_bytes()
    ).decode("ascii")

    st.markdown(
        f'<img src="data:image/jpeg;base64,{encoded}" '
        'style="width:100%;max-height:245px;'
        'object-fit:cover;border-radius:14px;" />',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="qe-title">'
        '👑 QUEEN’S EDGE | 254'
        '</div>',
        unsafe_allow_html=True,
    )

st.markdown(
    '<div class="qe-subtitle">'
    '⚡ LIVE KALSHI MARKETS · '
    'BUILT BY K HENDO | 254 · READ ONLY'
    '</div>',
    unsafe_allow_html=True,
)

st.caption(
    f"Automatic public-API quotes · "
    f"{refresh_seconds}-second refresh · "
    "One-second countdowns · No automatic trades"
)

st.info(
    "Quotes are polled from Kalshi's public API. "
    "This is not authenticated live streaming. "
    "Prices can lag during rapid market moves."
)


# ============================================
# MARKET CARDS
# ============================================

def market_card(name, series, emoji):
    with st.container(border=True):
        st.subheader(f"{emoji} {name}")

        market = active_markets.get(series)

        if not market:
            st.warning("No active contract available.")
            return

        ticker = market.get("ticker", series)

        close_raw = (
            market.get("close_time")
            or market.get("latest_expiration_time")
        )

        verified = verified_direction(market)

        yes_bid = quote(market, "yes", "bid")
        yes_ask = quote(market, "yes", "ask")
        no_bid = quote(market, "no", "bid")
        no_ask = quote(market, "no", "ask")

        mid = midpoint(market)

        st.caption(
            f"Target: {market_target(market)}"
        )

        # Timer runs separately every second.
        live_countdown(close_raw)

        left, right = st.columns(2, gap="small")

        with left:
            st.markdown(
                '<div class="qe-quote qe-yes">'
                f'<b>{"ABOVE TARGET" if verified else "YES"}</b>'
                f'<p>BID <strong>{fmt_cents(yes_bid)}</strong></p>'
                f'<p>ASK <strong>{fmt_cents(yes_ask)}</strong></p>'
                '</div>',
                unsafe_allow_html=True,
            )

        with right:
            st.markdown(
                '<div class="qe-quote qe-no">'
                f'<b>{"BELOW TARGET" if verified else "NO"}</b>'
                f'<p>BID <strong>{fmt_cents(no_bid)}</strong></p>'
                f'<p>ASK <strong>{fmt_cents(no_ask)}</strong></p>'
                '</div>',
                unsafe_allow_html=True,
            )

        label, magnitude = update_radar(
            ticker, name, mid
        )

        if mid is None or not verified:
            lean = "⚪ UNVERIFIED / NO CALL"
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
            f"YES midpoint: {fmt_cents(mid)} · "
            f"Radar: {label} · "
            f"Move: {magnitude:.1f}¢"
        )

        early = early_map.get(ticker)
        locked = locked_map.get(ticker)

        st.write(
            "**🌅 Early:** "
            + (
                str(early.get("call"))
                if early else "Not recorded"
            )
        )

        st.write(
            "**🔒 Locked:** "
            + (
                f"{locked.get('call')} · "
                f"{locked.get('status')}"
                if locked else "Awaiting lock window"
            )
        )

        with st.expander("📡 Radar, pressure & details"):
            st.caption(f"Contract: {ticker}")

            st.markdown("**🐋 Pressure & Whale Radar**")

            try:
                show_pressure_radar(name, ticker)
            except Exception as exc:
                st.caption(
                    f"Pressure unavailable: {exc}"
                )

            st.markdown(
                "**📜 Last 10 settled markets**"
            )

            try:
                recent = fetch_recent(series)

                arrows = [
                    (
                        "🟢↑"
                        if str(
                            item.get("result")
                        ).lower() == "yes"
                        else "🔴↓"
                    )
                    for item in reversed(recent)
                ]

                st.write(
                    "  ".join(arrows) or "No results"
                )

            except Exception:
                st.caption(
                    "Recent results unavailable."
                )

            st.markdown(
                "**Official contract rules**"
            )

            st.write(
                market.get("rules_primary")
                or "See Kalshi contract details."
            )

            st.write(
                market.get("rules_secondary") or ""
            )

        # Strong LOCK: paper-only evaluation.
        if locked:
            history = (
                st.session_state.quote_history
                .get(ticker, [])
            )

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
                    and strong_store
                    and ask is not None
                ):
                    strong_store.record_once({
                        "ticker": ticker,
                        "market_name": name,
                        "series_ticker": series,
                        "side": locked.get("call"),
                        "decision": "ENTER",
                        "entry_price": ask,
                        "yes_midpoint": float(
                            locked.get("yes_midpoint") or 0
                        ),
                        "close_time": market.get("close_time"),
                        "status": "pending",
                    })

            except Exception as exc:
                st.caption(
                    f"Strong LOCK unavailable: {exc}"
                )


# ============================================
# FOUR-MARKET GRID
# ============================================

st.markdown("### ⚡ LIVE MARKET GRID")

top_left, top_right = st.columns(2, gap="small")

with top_left:
    market_card(*SERIES[0])

with top_right:
    market_card(*SERIES[1])

bottom_left, bottom_right = st.columns(2, gap="small")

with bottom_left:
    market_card(*SERIES[2])

with bottom_right:
    market_card(*SERIES[3])


# ============================================
# STANDARD SCOREBOARDS
# ============================================

def scoreboard(title, rows, label):
    st.subheader(title)

    graded = [
        r for r in rows
        if str(r.get("status")).lower()
        in ("win", "loss")
    ]

    wins = sum(
        str(r.get("status")).lower() == "win"
        for r in graded
    )

    losses = len(graded) - wins

    pending = sum(
        str(r.get("status")).lower() == "pending"
        for r in rows
    )

    a, b, c, d = st.columns(4)

    a.metric("Wins", wins)
    b.metric("Losses", losses)

    c.metric(
        "Win rate",
        f"{wins / len(graded):.1%}"
        if graded else "—",
    )

    d.metric("Pending", pending)

    for name, series, emoji in SERIES:
        subset = [
            r for r in graded
            if r.get("series_ticker") == series
        ]

        w = sum(
            str(r.get("status")).lower() == "win"
            for r in subset
        )

        st.caption(
            f"{emoji} {name}: "
            f"{w} W / {len(subset) - w} L"
        )

    with st.expander(f"📜 {label} history"):
        if rows:
            st.dataframe(
                rows,
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption(
                "No entries recorded yet."
            )


scoreboard(
    "🔒 Locked Call Scoreboard",
    locked_rows,
    "Locked calls",
)

st.caption(
    "Directional accuracy is not trading profitability."
)

scoreboard(
    "🌅 Early Call Scoreboard",
    early_rows,
    "Early calls",
)


# ============================================
# WATCH PAPER P&L
# ============================================

def watch_entry_pnl(row):
    status = str(
        row.get("status", "")
    ).lower()

    if status not in ("win", "loss"):
        return None

    try:
        entry = float(row.get("entry_price"))
    except (ValueError, TypeError):
        return None

    if not 0 <= entry <= 1:
        return None

    payout = 1.0 if status == "win" else 0.0

    return payout - entry - 0.02


def watch_market_matches(row, name, series):
    recorded_name = str(
        row.get("market_name") or ""
    ).strip().lower()

    recorded_series = str(
        row.get("series_ticker") or ""
    ).strip().upper()

    aliases = {
        "WTI Oil": ("wti oil", "oil", "wti")
    }

    names = aliases.get(
        name,
        (name.lower(),),
    )

    return (
        recorded_name in names
        or recorded_series == series
    )


st.subheader(
    "💰 WATCH Paper Trading P&L Scoreboard"
)

st.caption(
    "Hypothetical one-contract results using "
    "stored WATCH entries, a $1 winning payout, "
    "and an assumed 2¢ total fee. No real orders."
)

watch_wins = sum(
    str(r.get("status", "")).lower() == "win"
    for r in paper_rows
)

watch_losses = sum(
    str(r.get("status", "")).lower() == "loss"
    for r in paper_rows
)

watch_pending = sum(
    str(r.get("status", "")).lower() == "pending"
    for r in paper_rows
)

watch_graded = watch_wins + watch_losses

watch_valid_pnl = []

for row in paper_rows:
    value = watch_entry_pnl(row)

    if value is not None:
        watch_valid_pnl.append(value)

a, b, c, d = st.columns(4)

a.metric("Wins", watch_wins)
b.metric("Losses", watch_losses)

c.metric(
    "Win rate",
    (
        f"{watch_wins / watch_graded:.1%}"
        if watch_graded
        else "—"
    ),
)

d.metric("Pending", watch_pending)

st.metric(
    "💵 Estimated Net Paper P&L",
    (
        f"${sum(watch_valid_pnl):+.2f}"
        if watch_valid_pnl
        else "—"
    ),
)

for name, series, emoji in SERIES:
    market_rows = [
        row for row in paper_rows
        if watch_market_matches(
            row, name, series
        )
    ]

    wins = sum(
        str(r.get("status", "")).lower() == "win"
        for r in market_rows
    )

    losses = sum(
        str(r.get("status", "")).lower() == "loss"
        for r in market_rows
    )

    values = []

    for row in market_rows:
        value = watch_entry_pnl(row)

        if value is not None:
            values.append(value)

    pnl_label = (
        f"Est. P&L ${sum(values):+.2f}"
        if values
        else "Est. P&L —"
    )

    st.caption(
        f"{emoji} {name}: "
        f"{wins} W / {losses} L · "
        f"{pnl_label}"
    )

with st.expander(
    "📜 WATCH paper trading history"
):
    if paper_rows:
        st.dataframe(
            paper_rows,
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption(
            "No WATCH paper entries recorded yet."
        )

st.caption(
    "⚠️ WATCH historical results remain provisional "
    "until all entries have been checked against "
    "their official settlements."
)


# ============================================
# STRONG LOCK SCOREBOARD
# ============================================

scoreboard(
    "🔐 Strong LOCK Paper Scoreboard",
    strong_rows,
    "Strong LOCK paper entries",
)

st.caption(
    "Strong LOCK is experimental and paper-only. "
    "No guaranteed fills or profits."
)


# ============================================
# FLIP RADAR
# ============================================

st.subheader("🚨 Flip Radar — All Markets")

events = st.session_state.radar_events

if events:
    for event in events[:10]:
        local = event["time"].astimezone(CENTRAL)

        st.write(
            f"**{event['market']} — {event['label']}** · "
            f"{event['move']:+.1f}¢ · "
            f"{local:%I:%M:%S %p}"
        )
else:
    st.caption(
        "No alerts yet. Keep the dashboard "
        "open for several refreshes."
    )


# ============================================
# FOOTER
# ============================================

st.caption(
    "Last market refresh: "
    + datetime.now(CENTRAL).strftime(
        "%b %d, %Y · %I:%M:%S %p %Z"
    )
)

st.caption(
    "Read-only Kalshi public API dashboard. "
    "No trade execution. Quotes may be delayed."
)

# Refresh market data automatically.
# Countdown fragments independently update
# every second between full app reruns.

st_autorefresh(
    interval=refresh_seconds * 1000,
    key="queens_edge_market_refresh",
)
