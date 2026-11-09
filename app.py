
def queen_signal_state(
    market, errors, verified, reason, early
):
    """
    Show only CURRENT verified candidate signals.

    Historical EARLY calls remain in the existing
    EARLY / MID / LOCK records. They never become
    a new live signal or paper BUY.
    """

    ticker = str(
        (market or {}).get("ticker") or ""
    )

    reason_text = str(reason or "").strip()
    reason_upper = reason_text.upper()
    reason_lower = reason_text.lower()

    if not ticker.startswith("KXBTC15M-"):
        return (
            "WAIT",
            "Waiting for the next BTC 15-minute contract",
            "#F5C96B",
        )

    close = base.utc(
        (market or {}).get("close_time")
    )

    if close is None:
        return (
            "DATA STALE",
            "Kalshi settlement time unavailable",
            "#FF8295",
        )

    remaining = (
        close - base.now_utc()
    ).total_seconds()

    if remaining <= 0:
        return (
            "WAIT",
            "Contract closing or changing rounds",
            "#F5C96B",
        )

    if errors or not verified:
        return (
            "DATA STALE",
            "Current Kalshi market verification unavailable",
            "#FF8295",
        )

    stale_phrases = (
        "stale",
        "unavailable",
        "needs refreshing",
        "data issue",
        "candle or quote",
    )

    if any(
        phrase in reason_lower
        for phrase in stale_phrases
    ):
        return (
            "DATA STALE",
            "Waiting for fresh market analysis",
            "#FF8295",
        )

    # A fresh candidate must be reported by the
    # existing analysis for this dashboard run.
    # A saved historical EARLY call is not enough.

    if "setup detected" in reason_lower:

        above = "ABOVE" in reason_upper
        below = "BELOW" in reason_upper

        if above and not below:
            return (
                "EARLY UP",
                "Current bullish candidate · V4 BUY not confirmed",
                "#53E7AB",
            )

        if below and not above:
            return (
                "EARLY DOWN",
                "Current bearish candidate · V4 BUY not confirmed",
                "#FF8295",
            )

    # No new qualifying setup.
    # Do not promote early.get("call") into
    # the current signal. It is historical.

    return (
        "WAIT",
        "No fresh confirmed candidate · previous EARLY calls are historical",
        "#F5C96B",
    )
