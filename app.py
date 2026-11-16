
"""QUEEN'S EDGE | 254 — KALSHI CLEAN COMMAND CENTER.

Replace only Kalshi repository app.py. Paper predictions only; no real orders.
Existing call_tracker.py EARLY/LOCK decision and settlement rules remain intact.
"""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from html import escape
import math

import pandas as pd
import streamlit as st

from call_tracker import (
    fetch_current, candidate, early_candidate, get_official_result,
    store_from_secrets, early_store_from_secrets, timestamp,
    direction_verified,
)
from queens_live_chart import render_live_chart

SERIES = "KXBTC15M"
CENTRAL = ZoneInfo("America/Chicago")
ROOT = Path(__file__).resolve().parent

st.set_page_config(
    page_title="QUEEN'S EDGE | 254 · KALSHI",
    page_icon="👑",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.stApp {
    background:radial-gradient(
        ellipse at top,
        #21113f 0%,
        #0b091a 57%,
        #05050c 100%
    );
    color:#eee9ff;
}
.block-container {
    max-width:1500px;
    padding:.6rem 1rem 1rem;
}
h1,h2,h3 {
    color:#dfc7ff!important;
}
.qe-card {
    background:linear-gradient(145deg,#281638,#140d23);
    border:1px solid #65438f;
    border-radius:13px;
    padding:13px 8px;
    text-align:center;
    min-height:108px;
}
.qe-label {
    font-size:.8rem;
    font-weight:800;
    color:#bfa9dd;
    letter-spacing:.05em;
}
.qe-value {
    font-size:clamp(1.05rem,2.8vw,1.85rem);
    font-weight:900;
    margin:7px 0;
}
.qe-note {
    font-size:.75rem;
    color:#b7a7cc;
    word-break:break-word;
}
.qe-up {color:#47e3a9}
.qe-down {color:#ff7c9c}
.qe-wait {color:#ffd27e}
[data-testid="stMetric"] {
    background:#20132f;
    border:1px solid #4b326b;
    border-radius:12px;
    padding:9px;
}
[data-testid="stMetricValue"] {
    color:#f5eaff;
}
/* Hide optional chart notes and legacy expanders. */
[data-testid="stExpander"] {
    display:none!important;
}
</style>
""", unsafe_allow_html=True)


def num(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def quote(market, side, kind):
    prefix = "yes" if side == "ABOVE" else "no"
    value = num(market.get(f"{prefix}_{kind}_dollars"))
    if value is None:
        cents = num(market.get(f"{prefix}_{kind}"))
        value = cents / 100 if cents is not None else None
    return value if value is not None and 0 <= value <= 1 else None


def target_price(market):
    # Only Kalshi's supplied target, never Coinbase as a substitute.
    for key in (
        "floor_strike",
        "functional_strike",
        "custom_strike",
        "cap_strike",
    ):
        value = num(market.get(key))
        if value is not None and value > 0:
            return value
    return None


def get_market():
    try:
        market = fetch_current(SERIES)
        return market, (
            None if market else "No active BTC 15-minute contract"
        )
    except Exception:
        return None, "Kalshi contract feed unavailable"


def get_store(factory):
    try:
        return factory(st.secrets)
    except Exception:
        return None


def btc_rows(rows):
    return [
        row for row in rows
        if str(row.get("ticker") or "").upper().startswith(
            SERIES + "-"
        )
    ]


def read_rows(store):
    if store is None:
        return [], False
    try:
        return btc_rows(store.all()), True
    except Exception:
        return [], False


def record_and_grade(store, market, builder):
    """Use original prediction rules and official grading, BTC only."""
    row, _reason = builder(market, "Bitcoin", SERIES)
    if row:
        store.insert_once(row)

    for saved in btc_rows(store.all()):
        if str(saved.get("status") or "").lower() != "pending":
            continue

        close = timestamp(saved.get("close_time"))
        if close is None or close > datetime.now(timezone.utc):
            continue

        ticker = str(saved.get("ticker") or "")
        if not ticker:
            continue

        try:
            result = get_official_result(ticker)
            if result not in ("yes", "no"):
                continue

            correct_side = (
                "ABOVE" if result == "yes" else "BELOW"
            )
            status = (
                "win"
                if str(saved.get("call") or "").upper() == correct_side
                else "loss"
            )

            store._request(
                "PATCH",
                params={
                    "ticker": "eq." + ticker,
                    "status": "eq.pending",
                },
                json={
                    "status": status,
                    "settlement_result": result,
                    "graded_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
                headers={"Prefer": "return=minimal"},
            )
        except Exception:
            # Temporary provider errors leave the call pending.
            continue


def stage_card(stage, call, detail):
    direction = str(call or "WAIT").upper()
    direction = (
        direction
        if direction in ("ABOVE", "BELOW")
        else "WAIT"
    )
    color = (
        "qe-up" if direction == "ABOVE"
        else "qe-down" if direction == "BELOW"
        else "qe-wait"
    )

    st.markdown(
        '<div class="qe-card"><div class="qe-label">'
        + escape(stage)
        + '</div><div class="qe-value '
        + color
        + '">'
        + escape(direction)
        + '</div><div class="qe-note">'
        + escape(str(detail))
        + "</div></div>",
        unsafe_allow_html=True,
    )


def when(row):
    dt = timestamp(row.get("locked_at")) if row else None
    return (
        dt.astimezone(CENTRAL).strftime("%I:%M:%S %p CT")
        if dt else "Not recorded"
    )


def score(rows):
    wins = sum(
        str(r.get("status") or "").lower() == "win"
        for r in rows
    )
    losses = sum(
        str(r.get("status") or "").lower() == "loss"
        for r in rows
    )
    pending = sum(
        str(r.get("status") or "").lower() == "pending"
        for r in rows
    )
    total = wins + losses
    return (
        wins,
        losses,
        pending,
        f"{wins / total:.1%}" if total else "—",
    )


def recorded_ask(row):
    if not row:
        return None
    side = str(row.get("call") or "").upper()
    bid = num(row.get("yes_bid"))
    ask = num(row.get("yes_ask"))
    if side == "ABOVE":
        return ask
    if side == "BELOW" and bid is not None:
        return 1 - bid
    return None


# ============================================
# BANNER
# ============================================

banner = next(
    (
        p for p in (
            ROOT / "queens_banner.jpg",
            ROOT / "queens_banner.png",
        )
        if p.is_file()
    ),
    None,
)

if banner:
    st.image(str(banner), use_container_width=True)
else:
    st.title("👑 QUEEN'S EDGE | 254")

st.caption(
    "KALSHI · BITCOIN 15-MINUTE COMMAND CENTER · PAPER ONLY"
)


# ============================================
# EXISTING STREAMING CHART
# ============================================

market, error = get_market()
chart_ticker = (
    str(market.get("ticker") or "")
    if market else ""
)

if market:
    strike = target_price(market)
    close = timestamp(
        market.get("close_time")
        or market.get("latest_expiration_time")
    )

    st.markdown("#### 📈 LIVE BITCOIN")

    if strike is not None and close is not None:
        try:
            render_live_chart(
                strike,
                close_time=close,
                market_ticker=chart_ticker,
            )
        except Exception:
            st.warning(
                "Live chart temporarily unavailable; "
                "prediction records are unchanged."
            )
    else:
        st.warning(
            "Kalshi target or contract end is unavailable."
        )
else:
    st.warning(error)


# ============================================
# LIVE SIGNALS AND ONE RESULTS SECTION
# ============================================

@st.fragment(run_every="15s")
def live_dashboard():
    current, current_error = get_market()
    if current is None:
        st.warning(current_error)
        return

    ticker = str(current.get("ticker") or "")

    if ticker != chart_ticker:
        # Rebuild the chart only on contract rollover.
        st.rerun()

    now = datetime.now(timezone.utc)
    close = timestamp(
        current.get("close_time")
        or current.get("latest_expiration_time")
    )
    remaining = (
        max(0, int((close - now).total_seconds()))
        if close else None
    )

    target = target_price(current)
    above = quote(current, "ABOVE", "ask")
    below = quote(current, "BELOW", "ask")

    st.markdown("#### 👑 CURRENT 15-MINUTE CONTRACT")
    st.caption(ticker or "Contract unavailable")

    a, b, c, d = st.columns(4)

    a.metric(
        "Settlement in",
        (
            f"{remaining // 60:02d}:{remaining % 60:02d}"
            if remaining is not None else "—"
        ),
    )
    b.metric(
        "Kalshi target",
        f"${target:,.2f}" if target is not None else "—",
    )
    c.metric(
        "ABOVE ask",
        f"{above * 100:.1f}¢" if above is not None else "—",
    )
    d.metric(
        "BELOW ask",
        f"{below * 100:.1f}¢" if below is not None else "—",
    )

    early_store = get_store(early_store_from_secrets)
    lock_store = get_store(store_from_secrets)
    failures = []

    # Preserve EARLY and LOCK recording and official grading.
    for name, store, rule in (
        ("EARLY", early_store, early_candidate),
        ("LOCK", lock_store, candidate),
    ):
        if store is None:
            failures.append(name + " journal unavailable")
            continue
        try:
            record_and_grade(store, current, rule)
        except Exception:
            failures.append(name + " scan delayed")

    early_rows, early_ok = read_rows(early_store)
    lock_rows, lock_ok = read_rows(lock_store)

    if not early_ok or not lock_ok:
        failures.append("Results journal unavailable")

    early_row = next(
        (r for r in early_rows if r.get("ticker") == ticker),
        None,
    )
    lock_row = next(
        (r for r in lock_rows if r.get("ticker") == ticker),
        None,
    )

    # MID remains indicative, not a saved prediction.
    mid_call = "WAIT"
    mid_note = "Provisional · no graded history"

    if (
        remaining is not None
        and remaining > 0
        and direction_verified(current)
    ):
        bid = quote(current, "ABOVE", "bid")
        ask = quote(current, "ABOVE", "ask")

        if (
            bid is not None
            and ask is not None
            and bid <= ask
            and ask - bid <= .15
        ):
            midpoint = (bid + ask) / 2

            mid_call = (
                "ABOVE" if midpoint >= .60
                else "BELOW" if midpoint <= .40
                else "WAIT"
            )
            mid_note = (
                "Live quote indication · not a saved call"
            )

    st.markdown("#### 👑 EARLY · MID · LOCK")

    left, center, right = st.columns(3)

    with left:
        stage_card(
            "EARLY · RECORDED",
            early_row.get("call") if early_row else "WAIT",
            (
                when(early_row)
                if early_row else "Waiting for early call"
            ),
        )

    with center:
        stage_card(
            "MID · INDICATIVE",
            mid_call,
            mid_note,
        )

    with right:
        stage_card(
            "LOCK · RECORDED",
            lock_row.get("call") if lock_row else "WAIT",
            (
                when(lock_row)
                if lock_row else "Waiting for final 5m"
            ),
        )

    st.markdown("#### 📊 PREDICTION RESULTS")

    if not early_ok and not lock_ok:
        st.info(
            "Saved results currently unavailable; "
            "historical totals are not guessed."
        )
    else:
        summary = []

        for name, rows, ok in (
            ("EARLY", early_rows, early_ok),
            ("LOCK", lock_rows, lock_ok),
        ):
            wins, losses, pending, rate = score(rows)

            summary.append({
                "Stage": name,
                "Wins": wins if ok else "—",
                "Losses": losses if ok else "—",
                "Pending": pending if ok else "—",
                "Win rate": rate if ok else "—",
            })

        summary.append({
            "Stage": "MID · ungraded",
            "Wins": "—",
            "Losses": "—",
            "Pending": "—",
            "Win rate": "—",
        })

        st.dataframe(
            pd.DataFrame(summary),
            hide_index=True,
            use_container_width=True,
        )

        history = []

        for name, rows, ok in (
            ("EARLY", early_rows, early_ok),
            ("LOCK", lock_rows, lock_ok),
        ):
            if not ok:
                continue

            for row in rows:
                ask = recorded_ask(row)

                history.append({
                    "_sort": (
                        timestamp(row.get("locked_at"))
                        or datetime.min.replace(
                            tzinfo=timezone.utc
                        )
                    ),
                    "Stage": name,
                    "Call time CT": when(row),
                    "Contract": row.get("ticker"),
                    "Direction": row.get("call"),
                    "Ask at call": (
                        f"{ask * 100:.1f}¢"
                        if ask is not None else "—"
                    ),
                    "Result": str(
                        row.get("status") or "pending"
                    ).upper(),
                })

        history.sort(
            key=lambda r: r["_sort"],
            reverse=True,
        )

        st.markdown("**Recent recorded predictions**")

        if history:
            display_rows = [
                {
                    k: v for k, v in row.items()
                    if k != "_sort"
                }
                for row in history[:25]
            ]

            st.dataframe(
                pd.DataFrame(display_rows),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption(
                "No saved BTC predictions available yet."
            )

    st.caption(
        "Accuracy is based on official settlement, "
        "not simulated profit. Quoted asks are indicative, "
        "not guaranteed fills. No orders are placed."
    )

    if failures:
        st.warning(
            " · ".join(dict.fromkeys(failures))
        )
    else:
        st.caption(
            "🟢 Kalshi call journals connected · "
            "official grading enabled"
        )


live_dashboard()
