
"""
QUEEN'S EDGE | 254
SHADOW V1 CONTRACT PROTECTION

PAPER ONLY — NO REAL ORDERS

Preserves the existing Queen's Edge dashboard.
Prevents new opposite-side Shadow V1 observations
for the same BTC 15-minute contract.

Historical reporting uses only the earliest
valid observation per contract.

Historical records are never deleted.
V4 paper entries remain untouched.
"""

from pathlib import Path
from datetime import datetime, timezone
import traceback

import streamlit as st

ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = (
    "queens_preview_base.py",
    "queens_original.py",
    "queens_live_chart.py",
    "queens_v3_guard.py",
)


def check_files():
    missing = [
        name for name in REQUIRED_FILES
        if not (ROOT / name).is_file()
    ]

    if missing:
        st.error(
            "Missing Queen's Edge files: "
            + ", ".join(missing)
        )
        st.stop()


def install_shadow_fix(base):
    """
    Patch Shadow V1 observation and reporting only.

    Existing historical records remain stored.
    Other Queen's Edge functions are unchanged.
    """

    original_observe = base.shadow_v1_observe

    def load_history(store):
        records = []
        offset = 0
        limit = 100

        while True:
            batch = store.request(
                "POST",
                "/object/list/" + base.BUCKET,
                {
                    "prefix": "shadow_v1_rejections",
                    "limit": limit,
                    "offset": offset,
                    "sortBy": {
                        "column": "name",
                        "order": "desc",
                    },
                },
            )

            if not isinstance(batch, list):
                raise RuntimeError(
                    "Shadow history could not be verified"
                )

            for item in batch:
                key = str(item.get("name") or "")

                if not key.endswith(".json"):
                    continue

                event = store.get(
                    "shadow_v1_rejections",
                    key,
                )

                if not isinstance(event, dict):
                    raise RuntimeError(
                        "Stored Shadow observation unreadable"
                    )

                records.append((key, event))

            if len(batch) < limit:
                break

            offset += len(batch)

            if offset > 10000:
                raise RuntimeError(
                    "Shadow history safety limit reached"
                )

        return records

    def earliest_per_contract(records):
        selected = {}

        for key, event in records:
            ticker = str(event.get("ticker") or "")
            observed = base.utc(
                event.get("observed_at")
            )

            if not ticker or observed is None:
                continue

            previous = selected.get(ticker)

            if (
                previous is None
                or observed < previous[2]
                or (
                    observed == previous[2]
                    and key < previous[0]
                )
            ):
                selected[ticker] = (
                    key,
                    event,
                    observed,
                )

        return sorted(
            selected.values(),
            key=lambda row: row[2],
            reverse=True,
        )

    def protected_observe(
        store,
        market,
        result,
        reasons,
        quote_at,
        trade_time,
        multiplier,
    ):
        ticker = str(
            (market or {}).get("ticker") or ""
        )

        if not ticker.startswith("KXBTC15M-"):
            return

        # Never create an opposite-side observation
        # when historical evidence already exists.
        records = load_history(store)

        if any(
            event.get("ticker") == ticker
            for _, event in records
        ):
            return

        # Immutable contract claim protects against
        # concurrent dashboard sessions.
        claim_key = ticker + ".json"

        existing = store.get(
            "shadow_v1_contract_claims",
            claim_key,
        )

        if existing is not None:
            return

        # Check all eligibility conditions before
        # claiming the contract.
        if not base.snapshot_current(
            quote_at, trade_time
        ):
            return

        if result.get("status") not in (
            "FILTERED", "CANDIDATE"
        ):
            return

        side = result.get("side")

        if side not in ("ABOVE", "BELOW"):
            return

        age = result.get("candle_age")

        if age is None or not -3 <= age <= 90:
            return

        close = base.utc(
            market.get("close_time")
        )

        if close is None:
            return

        seconds = (
            close - base.now_utc()
        ).total_seconds()

        if not 180 <= seconds <= 600:
            return

        ask = base.quote(market, side, "ask")
        bid = base.quote(market, side, "bid")

        if (
            ask is None
            or bid is None
            or not base.SHADOW_MIN_ASK
            <= ask <= base.SHADOW_MAX_ASK
            or not 0 < bid <= ask < 1
            or ask - bid > 0.030001
        ):
            return

        price = base.cents_round(
            ask + base.SLIPPAGE
        )

        qty, _, _ = base.size_for(
            price, multiplier
        )

        depth = base.available(
            market, side, "ask"
        )

        if (
            qty < 1
            or depth is None
            or depth < qty
        ):
            return

        why = list(
            reasons
            or result.get("reasons")
            or []
        )

        if not why:
            return

        # Claim is written only after eligibility.
        claim = {
            "ticker": ticker,
            "side": side,
            "created_at": (
                base.now_utc().isoformat()
            ),
            "version": "shadow-v1-protection",
        }

        saved = store.once(
            "shadow_v1_contract_claims",
            claim_key,
            claim,
        )

        # If another session claimed first,
        # do not write the competing observation.
        if (
            saved.get("side") != side
            or saved.get("created_at")
            != claim["created_at"]
        ):
            return

        original_observe(
            store,
            market,
            result,
            reasons,
            quote_at,
            trade_time,
            multiplier,
        )

    def protected_report(store):
        with st.expander(
            "👑 Shadow Tracker V1 · "
            "missed opportunity study",
            expanded=False,
        ):
            st.caption(
                "One counted observation per BTC "
                "15-minute contract. Historical "
                "duplicates remain archived but "
                "are excluded from performance."
            )

            records = load_history(store)
            selected = earliest_per_contract(
                records
            )

            duplicates = (
                len(records) - len(selected)
            )

            output = []
            checks = 0

            for key, event, observed in selected:
                final = store.get(
                    "shadow_v1_finals",
                    key,
                )

                close_ts = base.number(
                    event.get("close_ts")
                )

                eligible = (
                    close_ts is not None
                    and base.now_utc().timestamp()
                    > close_ts + 60
                )

                if (
                    final is None
                    and eligible
                    and checks < 3
                ):
                    checks += 1

                    try:
                        market = base.public_get(
                            "/markets/"
                            + event["ticker"]
                        )["market"]

                        outcome = market.get("result")

                        if (
                            market.get("status")
                            in ("settled", "finalized")
                            and outcome in ("yes", "no")
                        ):
                            won = (
                                (
                                    event["side"]
                                    == "ABOVE"
                                )
                                == (outcome == "yes")
                            )

                            qty = int(
                                event.get("qty") or 0
                            )

                            risk = float(
                                event.get("risk") or 0
                            )

                            pnl = round(
                                (qty if won else 0)
                                - risk,
                                6,
                            )

                            final = store.once(
                                "shadow_v1_finals",
                                key,
                                {
                                    "result": outcome,
                                    "win": won,
                                    "hypothetical_pnl": pnl,
                                    "graded_at": (
                                        base.now_utc()
                                        .isoformat()
                                    ),
                                },
                            )

                    except (
                        KeyError,
                        ValueError,
                        TypeError,
                        base.requests.RequestException,
                    ):
                        pass

                risk = float(
                    event.get("risk") or 0
                )

                if final is not None:
                    result = (
                        "WIN"
                        if final.get("win")
                        else "LOSS"
                    )

                    pnl = base.number(
                        final.get(
                            "hypothetical_pnl"
                        )
                    )

                    # Do not count malformed grades.
                    if pnl is None:
                        result = "PENDING"

                else:
                    result = "PENDING"
                    pnl = None

                output.append({
                    "Time CT": (
                        observed.astimezone(
                            base.CT
                        ).strftime(
                            "%m/%d %H:%M:%S"
                        )
                    ),
                    "Ticker": event.get(
                        "ticker"
                    ),
                    "Side": event.get("side"),
                    "Ask ¢": round(
                        float(
                            event.get("ask") or 0
                        ) * 100,
                        1,
                    ),
                    "Contracts": int(
                        event.get("qty") or 0
                    ),
                    "Risk $": round(risk, 2),
                    "Result": result,
                    "P&L $": pnl,
                    "Why V4 skipped": "; ".join(
                        map(
                            str,
                            event.get("reasons")
                            or [],
                        )
                    ),
                })

            if not output:
                st.info(
                    "No Shadow V1 observations yet."
                )
                return

            graded = [
                row for row in output
                if row["Result"] in (
                    "WIN", "LOSS"
                )
            ]

            wins = sum(
                row["Result"] == "WIN"
                for row in graded
            )

            losses = len(graded) - wins

            net = sum(
                row["P&L $"]
                for row in graded
            )

            graded_risk = sum(
                row["Risk $"]
                for row in graded
            )

            pending_risk = sum(
                row["Risk $"]
                for row in output
                if row["Result"] == "PENDING"
            )

            roi = (
                net / graded_risk
                if graded_risk > 0
                else None
            )

            st.markdown(
                "##### 👑 Shadow V1 · "
                "Hypothetical Scoreboard"
            )

            a, b, c = st.columns(3)
            a.metric(
                "Observations", len(output)
            )
            b.metric("Wins", wins)
            c.metric("Losses", losses)

            a, b, c = st.columns(3)

            a.metric(
                "Win rate",
                f"{wins / len(graded):.1%}"
                if graded else "—",
            )

            b.metric(
                "Hypothetical P&L",
                f"${net:+.2f}",
            )

            c.metric(
                "Risk on graded",
                f"${graded_risk:.2f}",
            )

            a, b, c = st.columns(3)

            a.metric(
                "Return on graded risk",
                f"{roi:+.1%}"
                if roi is not None
                else "—",
            )

            b.metric(
                "Pending risk",
                f"${pending_risk:.2f}",
            )

            c.metric(
                "All modeled risk",
                f"${graded_risk + pending_risk:.2f}",
            )

            st.caption(
                f"{len(graded)} officially graded · "
                f"{len(output) - len(graded)} pending. "
                "P&L uses graded observations only. "
                "All results are hypothetical."
            )

            if duplicates:
                st.info(
                    f"{duplicates} historical duplicate "
                    "observation(s) excluded from "
                    "the scoreboard. Stored records "
                    "were not deleted."
                )

            st.markdown(
                "**Per-observation results**"
            )

            st.dataframe(
                [
                    {
                        "Time CT": r["Time CT"],
                        "Side": r["Side"],
                        "Risk $": r["Risk $"],
                        "Result": r["Result"],
                        "P&L $": r["P&L $"],
                    }
                    for r in output
                ],
                hide_index=True,
                use_container_width=True,
            )

            with st.expander(
                "Shadow V1 · full "
                "observation details",
                expanded=False,
            ):
                st.dataframe(
                    output,
                    hide_index=True,
                    use_container_width=True,
                )

    base.shadow_v1_observe = protected_observe
    base.shadow_v1_report = protected_report


def main():
    check_files()

    try:
        import queens_preview_base as base

        install_shadow_fix(base)

        base.launch()

    except Exception as exc:
        st.error(
            "Queen's Edge encountered an error."
        )

        st.write(
            "Error type:",
            type(exc).__name__,
        )

        st.write(
            "Error message:",
            str(exc),
        )

        with st.expander(
            "Full Python diagnostics",
            expanded=False,
        ):
            st.code(
                traceback.format_exc(),
                language="text",
            )

        st.caption(
            "Paper only. No real orders."
        )


if __name__ == "__main__":
    main()
