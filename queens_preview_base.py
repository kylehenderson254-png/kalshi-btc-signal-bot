
"""
QUEEN'S EDGE | 254
SHADOW V1 CONTRACT PROTECTION

PAPER ONLY — NO REAL ORDERS

Complete app.py replacement.

Keeps the existing dashboard and chart engine.
Prevents new opposing Shadow V1 observations
for the same BTC 15-minute contract.

Historical observations remain stored.
Only the earliest observation per contract
counts in the Shadow V1 scoreboard.

V4 paper entries remain unchanged.
"""

from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import traceback

import streamlit as st


ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = (
    "queens_preview_base.py",
    "queens_original.py",
    "queens_live_chart.py",
    "queens_v3_guard.py",
)

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


def install_shadow_protection(base):
    """
    Patch only the Shadow V1 observation
    and reporting functions.

    No chart, layout, HTML, or V4 modifications.
    """

    original_observe = base.shadow_v1_observe

    def list_shadow_records(store):
        """
        Retrieve existing Shadow V1 records.

        Fail closed if storage is unavailable.
        """
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

            records.extend(batch)

            if len(batch) < limit:
                break

            offset += len(batch)

            if offset >= 10000:
                raise RuntimeError(
                    "Shadow history exceeds safe scan limit"
                )

        return records

    def load_shadow_events(store):
        events = []

        for item in list_shadow_records(store):
            key = str(item.get("name") or "")

            if not key.endswith(".json"):
                continue

            event = store.get(
                "shadow_v1_rejections",
                key,
            )

            if not isinstance(event, dict):
                raise RuntimeError(
                    "Shadow observation could not be read"
                )

            events.append((key, event))

        return events

    def protected_observe(
        store,
        market,
        result,
        reasons,
        quote_at,
        trade_time,
        multiplier,
    ):
        """
        Allow only one Shadow observation
        per contract across both directions.
        """

        ticker = str(
            (market or {}).get("ticker") or ""
        )

        if not ticker.startswith("KXBTC15M-"):
            return

        # Check all legacy observations first.
        # A prior ABOVE or BELOW observation
        # blocks another one for this ticker.
        events = load_shadow_events(store)

        for _, event in events:
            if event.get("ticker") == ticker:
                return

        # Only create a contract lock if the
        # original Shadow entry checks qualify.
        #
        # Validate using the same eligibility
        # conditions as the existing engine.

        if not base.snapshot_current(
            quote_at,
            trade_time,
        ):
            return

        if result.get("status") not in (
            "FILTERED",
            "CANDIDATE",
        ):
            return

        candle_age = result.get("candle_age")

        if (
            candle_age is None
            or not -3 <= candle_age <= 90
        ):
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

        side = result.get("side")

        if side not in ("ABOVE", "BELOW"):
            return

        ask = base.quote(
            market, side, "ask"
        )

        bid = base.quote(
            market, side, "bid"
        )

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

        qty, estimated_fee, risk = (
            base.size_for(
                price,
                multiplier,
            )
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

        # Shared lock for ABOVE and BELOW.
        # The unique token distinguishes
        # the creator from another session.

        token = uuid4().hex

        lock_key = ticker + ".json"

        lock = {
            "ticker": ticker,
            "side": side,
            "token": token,
            "created_at": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
            "paper_only": True,
        }

        saved_lock = store.once(
            "shadow_v1_contract_locks",
            lock_key,
            lock,
        )

        if not isinstance(saved_lock, dict):
            raise RuntimeError(
                "Shadow contract lock unavailable"
            )

        if saved_lock.get("token") != token:
            return

        # Preserve the original engine's
        # calculations and immutable event
        # storage behavior.

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
        """
        Historical records are preserved.

        Scoreboard counts the earliest
        observation per ticker only.
        """

        with st.expander(
            "👑 Shadow Tracker V1 · missed opportunity study",
            expanded=False,
        ):
            st.caption(
                "One counted observation per BTC "
                "15-minute contract. Historical "
                "duplicates remain stored but are "
                "excluded from performance totals. "
                "Paper only · no actual fills."
            )

            events = load_shadow_events(store)

            if not events:
                st.caption(
                    "No qualifying Shadow V1 "
                    "observations recorded yet."
                )
                return

            # Old records may have both ABOVE
            # and BELOW observations.
            # Choose the earliest recorded
            # observation for each ticker.

            events.sort(
                key=lambda pair: (
                    base.utc(
                        pair[1].get("observed_at")
                    )
                    or datetime.max.replace(
                        tzinfo=timezone.utc
                    ),
                    pair[0],
                )
            )

            selected = {}
            duplicates = []

            for key, event in events:
                ticker = str(
                    event.get("ticker") or ""
                )

                if not ticker:
                    raise RuntimeError(
                        "Shadow record missing ticker"
                    )

                if ticker in selected:
                    duplicates.append(
                        (key, event)
                    )
                else:
                    selected[ticker] = (
                        key,
                        event,
                    )

            output = []
            checks = 0

            for key, event in selected.values():
                final = store.get(
                    "shadow_v1_finals",
                    key,
                )

                close_ts = float(
                    event.get("close_ts") or 0
                )

                if (
                    not final
                    and checks < 3
                    and base.now_utc().timestamp()
                    > close_ts + 60
                ):
                    checks += 1

                    try:
                        settled = base.public_get(
                            "/markets/"
                            + event["ticker"]
                        )["market"]

                        outcome = settled.get(
                            "result"
                        )

                        if (
                            settled.get("status")
                            in (
                                "settled",
                                "finalized",
                            )
                            and outcome
                            in ("yes", "no")
                        ):
                            won = (
                                (
                                    event["side"]
                                    == "ABOVE"
                                )
                                == (
                                    outcome
                                    == "yes"
                                )
                            )

                            pnl = round(
                                (
                                    int(event["qty"])
                                    if won else 0
                                )
                                - float(
                                    event["risk"]
                                ),
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

                observed = base.utc(
                    event.get("observed_at")
                )

                time_ct = (
                    observed.astimezone(
                        base.CT
                    ).strftime(
                        "%m/%d %H:%M:%S"
                    )
                    if observed
                    else "—"
                )

                output.append({
                    "Observed CT": time_ct,
                    "Ticker": event["ticker"],
                    "Side": event["side"],
                    "Ask ¢": round(
                        float(
                            event.get("ask") or 0
                        ) * 100,
                        1,
                    ),
                    "Contracts": int(
                        event.get("qty") or 0
                    ),
                    "Modeled risk $": round(
                        float(
                            event.get("risk") or 0
                        ),
                        2,
                    ),
                    "Why V4 skipped": "; ".join(
                        map(
                            str,
                            event.get("reasons")
                            or [],
                        )
                    ),
                    "Official grade": (
                        "WIN"
                        if final.get("win")
                        else "LOSS"
                    ) if final else "PENDING",
                    "Hypothetical net $": (
                        final.get(
                            "hypothetical_pnl"
                        )
                        if final
                        else None
                    ),
                })

            output.sort(
                key=lambda row:
                row["Observed CT"],
                reverse=True,
            )

            graded = [
                row for row in output
                if row["Official grade"]
                != "PENDING"
            ]

            wins = sum(
                row["Official grade"] == "WIN"
                for row in graded
            )

            losses = len(graded) - wins

            total_net = sum(
                float(
                    row["Hypothetical net $"]
                    or 0
                )
                for row in graded
            )

            graded_risk = sum(
                row["Modeled risk $"]
                for row in graded
            )

            pending_risk = sum(
                row["Modeled risk $"]
                for row in output
                if row["Official grade"]
                == "PENDING"
            )

            total_risk = (
                graded_risk + pending_risk
            )

            roi = (
                total_net / graded_risk
                if graded_risk
                else None
            )

            st.markdown(
                "##### 👑 Shadow V1 · "
                "Hypothetical Scoreboard"
            )

            a, b, c = st.columns(3)

            a.metric(
                "Observations",
                len(output),
            )
            b.metric("Wins", wins)
            c.metric("Losses", losses)

            a, b, c = st.columns(3)

            a.metric(
                "Win rate",
                (
                    f"{wins / len(graded):.1%}"
                    if graded else "—"
                ),
            )

            b.metric(
                "Hypothetical P&L",
                f"${total_net:+.2f}",
            )

            c.metric(
                "Risk on graded",
                f"${graded_risk:.2f}",
            )

            a, b, c = st.columns(3)

            a.metric(
                "Return on graded risk",
                (
                    f"{roi:+.1%}"
                    if roi is not None
                    else "—"
                ),
            )

            b.metric(
                "Pending risk",
                f"${pending_risk:.2f}",
            )

            c.metric(
                "All modeled risk",
                f"${total_risk:.2f}",
            )

            st.caption(
                f"{len(graded)} officially graded · "
                f"{len(output) - len(graded)} pending · "
                f"{len(duplicates)} historical "
                "duplicate observations excluded. "
                "P&L and return use graded "
                "observations only. Risk includes "
                "estimated entry fees."
            )

            st.markdown(
                "**Per-observation results**"
            )

            st.dataframe(
                [
                    {
                        "Time CT":
                            row["Observed CT"],
                        "Side":
                            row["Side"],
                        "Risk $":
                            row["Modeled risk $"],
                        "Result":
                            row["Official grade"],
                        "P&L $":
                            row["Hypothetical net $"],
                    }
                    for row in output
                ],
                hide_index=True,
                use_container_width=True,
            )

            with st.expander(
                "Shadow V1 · full observation details",
                expanded=False,
            ):
                st.dataframe(
                    output,
                    hide_index=True,
                    use_container_width=True,
                )

            if duplicates:
                with st.expander(
                    "Historical duplicate observations",
                    expanded=False,
                ):
                    st.caption(
                        "Preserved for audit only. "
                        "Not included in scoreboard "
                        "risk, wins, losses, or P&L."
                    )

                    st.dataframe(
                        [
                            {
                                "Ticker":
                                    event.get("ticker"),
                                "Side":
                                    event.get("side"),
                                "Observed":
                                    event.get("observed_at"),
                                "Risk $":
                                    event.get("risk"),
                            }
                            for _, event in duplicates
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )

    # Install before the dashboard launches.
    base.shadow_v1_observe = protected_observe
    base.shadow_v1_report = protected_report


def main():
    try:
        import queens_preview_base as base

        install_shadow_protection(base)

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
            "Paper-only dashboard. "
            "No real orders are sent."
        )


if __name__ == "__main__":
    main()
