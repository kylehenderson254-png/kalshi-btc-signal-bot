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



