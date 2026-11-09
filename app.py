
"""
QUEEN'S EDGE | 254
SHADOW V1 RECORDING REPAIR

PAPER ONLY — NO REAL ORDERS

Preserves the existing dashboard, chart, Near Miss,
and V4 paper scanner.

Shadow V1:
- Uses the original eligibility checks
- Does not create premature contract locks
- Saves one observation per BTC 15-minute contract
- Preserves existing history
- Excludes historical duplicates from the scoreboard
- Shows observation diagnostics

No real-money trading or order submission.
"""

from pathlib import Path
from collections import Counter
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


def install_shadow_repair(base):

    original_observe = base.shadow_v1_observe

    diagnostics = Counter()

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
                    "Shadow history unavailable"
                )

            for item in batch:
                key = str(
                    item.get("name") or ""
                )

                if not key.endswith(".json"):
                    continue

                event = store.get(
                    "shadow_v1_rejections",
                    key,
                )

                if not isinstance(event, dict):
                    raise RuntimeError(
                        "Unreadable Shadow history record"
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
            ticker = str(
                event.get("ticker") or ""
            )

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

    class ShadowStore:
        """
        Storage adapter used only by the original
        Shadow V1 observation function.

        The original function performs all eligibility
        checks before calling store.once().

        We intercept only that final save operation.
        """

        def __init__(self, actual_store, history):
            self.actual_store = actual_store
            self.existing_tickers = {
                str(event.get("ticker"))
                for _, event in history
                if event.get("ticker")
            }

        def __getattr__(self, name):
            return getattr(
                self.actual_store,
                name,
            )

        def once(self, prefix, key, event):
            if prefix != "shadow_v1_rejections":
                return self.actual_store.once(
                    prefix, key, event
                )

            ticker = str(
                event.get("ticker") or ""
            )

            if not ticker.startswith("KXBTC15M-"):
                diagnostics["invalid_ticker"] += 1
                return None

            if ticker in self.existing_tickers:
                diagnostics["duplicate_blocked"] += 1
                return None

            # The canonical key is shared by ABOVE
            # and BELOW for the same contract.
            #
            # This replaces the old side-specific key.
            # The first successful write wins.
            canonical_key = (
                "contract_" + ticker + ".json"
            )

            saved = self.actual_store.once(
                "shadow_v1_rejections",
                canonical_key,
                event,
            )

            if not isinstance(saved, dict):
                diagnostics["save_unverified"] += 1
                return saved

            if (
                saved.get("ticker") != ticker
                or saved.get("side") != event.get("side")
                or saved.get("observed_at")
                != event.get("observed_at")
            ):
                diagnostics["duplicate_blocked"] += 1
                return saved

            # Verify the observation can be read.
            verified = self.actual_store.get(
                "shadow_v1_rejections",
                canonical_key,
            )

            if not isinstance(verified, dict):
                diagnostics["save_unverified"] += 1
                return saved

            if (
                verified.get("ticker") == ticker
                and verified.get("observed_at")
                == event.get("observed_at")
            ):
                diagnostics["saved"] += 1
                self.existing_tickers.add(ticker)
            else:
                diagnostics["save_unverified"] += 1

            return saved

    def protected_observe(
        store,
        market,
        result,
        reasons,
        quote_at,
        trade_time,
        multiplier,
    ):
        diagnostics["calls"] += 1

        ticker = str(
            (market or {}).get("ticker") or ""
        )

        if not ticker.startswith("KXBTC15M-"):
            diagnostics["invalid_ticker"] += 1
            return

        try:
            history = load_history(store)

            existing = {
                str(event.get("ticker") or "")
                for _, event in history
            }

            if ticker in existing:
                diagnostics["duplicate_blocked"] += 1
                return

            adapter = ShadowStore(
                store,
                history,
            )

            before = diagnostics["saved"]

            # IMPORTANT:
            # The original function handles
            # freshness, RSI/EMA assessment,
            # entry window, bid/ask, spread,
            # depth, size, and rejection reasons.
            #
            # No additional eligibility rules
            # are imposed here.
            original_observe(
                adapter,
                market,
                result,
                reasons,
                quote_at,
                trade_time,
                multiplier,
            )

            if diagnostics["saved"] == before:
                diagnostics["no_new_save"] += 1

        except Exception:
            diagnostics["errors"] += 1
            raise

    def protected_report(store):

        with st.expander(
            "👑 Shadow Tracker V1 · "
            "missed opportunity study",
            expanded=False,
        ):
            st.caption(
                "Forward-only hypothetical observations. "
                "One counted observation per BTC "
                "15-minute contract. No real orders "
                "and no impact on V4 paper P&L."
            )

            with st.expander(
                "Shadow V1 · recording diagnostics",
                expanded=False,
            ):
                st.caption(
                    "Counters reset when the Python "
                    "process restarts. A call without "
                    "a save can mean the original "
                    "eligibility rules were not met."
                )

                st.json({
                    "Observation calls": diagnostics["calls"],
                    "Saved this process": diagnostics["saved"],
                    "Duplicates blocked": diagnostics[
                        "duplicate_blocked"
                    ],
                    "No new save": diagnostics[
                        "no_new_save"
                    ],
                    "Save unverified": diagnostics[
                        "save_unverified"
                    ],
                    "Invalid ticker": diagnostics[
                        "invalid_ticker"
                    ],
                    "Errors": diagnostics["errors"],
                })

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

                result = "PENDING"
                pnl = None

                if isinstance(final, dict):
                    pnl = base.number(
                        final.get(
                            "hypothetical_pnl"
                        )
                    )

                    if (
                        pnl is not None
                        and isinstance(
                            final.get("win"), bool
                        )
                    ):
                        result = (
                            "WIN"
                            if final["win"]
                            else "LOSS"
                        )
                    else:
                        pnl = None

                output.append({
                    "Time CT": (
                        observed.astimezone(
                            base.CT
                        ).strftime(
                            "%m/%d %H:%M:%S"
                        )
                    ),
                    "Ticker": event.get("ticker"),
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
                            event.get("reasons") or [],
                        )
                    ),
                })

            st.markdown(
                "##### 👑 Shadow V1 · "
                "Hypothetical Scoreboard"
            )

            if not output:
                st.info(
                    "No Shadow observations recorded yet."
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
                "Shadow V1 · full observation details",
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

        install_shadow_repair(base)

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
