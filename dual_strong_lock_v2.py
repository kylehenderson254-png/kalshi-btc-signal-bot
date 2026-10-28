
"""QUEEN'S EDGE | 254 — Dual Strong LOCK 2.0.

Shadow-mode signal engine for Bitcoin and WTI Oil.

- Public Kalshi quotes only
- No automatic trades
- No database writes
- No guaranteed predictions or profits

Designed to run alongside the original Dual Strong LOCK.
"""

from datetime import datetime
from statistics import median
import math


# ============================================================
# STRATEGY SETTINGS
# ============================================================

STRATEGY_NAME = "Dual Strong LOCK 2.0"
STRATEGY_VERSION = "2.0.0-shadow"

MOMENTUM_SECONDS = 25
CONFIRMATION_SECONDS = 45

MIN_HISTORY_SECONDS = 30
MIN_OBSERVATIONS = 8

MIN_MIDPOINT_ABOVE = 0.62
MAX_MIDPOINT_BELOW = 0.38

MIN_NET_MOVE = 0.012
MIN_CONFIRMATION_MOVE = 0.008

MAX_SPREAD = 0.04
MAX_ENTRY_ASK = 0.82

MAX_REVERSAL_RATIO = 0.55
MIN_DIRECTIONAL_EFFICIENCY = 0.35

MIN_SIGNAL_SCORE = 70

# Existing 10m and 5m windows remain controlled by app.py.
# This module only evaluates market data supplied to it.


# ============================================================
# HELPERS
# ============================================================

def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def quote(market, side, kind):
    value = number(market.get(f"{side}_{kind}_dollars"))

    if value is not None:
        return value

    value = number(market.get(f"{side}_{kind}"))

    return value / 100 if value is not None else None


def prepare_history(history):
    """Clean, sort and deduplicate midpoint observations."""

    points = []

    for item in history:
        if len(item) < 2:
            continue

        stamp = item[0]
        value = number(item[1])

        if not isinstance(stamp, datetime):
            continue

        if stamp.tzinfo is None:
            continue

        if value is None or not 0 <= value <= 1:
            continue

        points.append((stamp, value))

    points.sort(key=lambda item: item[0])

    unique = []

    for stamp, value in points:
        if unique and unique[-1][0] == stamp:
            unique[-1] = (stamp, value)
        else:
            unique.append((stamp, value))

    return unique


def older_value(points, seconds):
    """Find the nearest observation at least N seconds old."""

    if not points:
        return None

    latest_time = points[-1][0]

    for stamp, value in reversed(points):
        if (latest_time - stamp).total_seconds() >= seconds:
            return value

    return None


def window_points(points, seconds):
    if not points:
        return []

    latest_time = points[-1][0]

    return [
        (stamp, value)
        for stamp, value in points
        if 0 <= (latest_time - stamp).total_seconds() <= seconds
    ]


def momentum_metrics(points, side):
    """Measure directional persistence without counting flat ticks."""

    recent = window_points(points, CONFIRMATION_SECONDS)

    if len(recent) < 2:
        return None

    values = [value for _, value in recent]

    differences = [
        current - previous
        for previous, current in zip(values, values[1:])
    ]

    if side == "ABOVE":
        aligned_moves = [
            change for change in differences if change > 0
        ]
        opposing_moves = [
            -change for change in differences if change < 0
        ]
        directional_change = values[-1] - values[0]
    else:
        aligned_moves = [
            -change for change in differences if change < 0
        ]
        opposing_moves = [
            change for change in differences if change > 0
        ]
        directional_change = values[0] - values[-1]

    aligned_total = sum(aligned_moves)
    opposing_total = sum(opposing_moves)

    total_movement = aligned_total + opposing_total

    efficiency = (
        max(0.0, directional_change) / total_movement
        if total_movement > 0
        else 0.0
    )

    reversal_ratio = (
        opposing_total / aligned_total
        if aligned_total > 0
        else float("inf")
    )

    recent_values = values[-min(10, len(values)):]

    return {
        "aligned_moves": len(aligned_moves),
        "opposing_moves": len(opposing_moves),
        "flat_moves": sum(
            abs(change) < 0.000001
            for change in differences
        ),
        "aligned_cents": aligned_total * 100,
        "opposing_cents": opposing_total * 100,
        "net_cents": directional_change * 100,
        "efficiency": efficiency,
        "reversal_ratio": reversal_ratio,
        "median_midpoint": median(recent_values),
        "observations": len(recent),
    }


def signal_score(
    midpoint,
    side,
    momentum_cents,
    metrics,
    spread_cents,
    ask,
):
    """Diagnostic score, NOT a calibrated win probability."""

    score = 0

    # Directional conviction: maximum 20 points.
    if side == "ABOVE":
        conviction = max(0, midpoint - 0.50)
    else:
        conviction = max(0, 0.50 - midpoint)

    score += min(20, conviction * 100)

    # 25-second momentum: maximum 25 points.
    score += min(25, max(0, momentum_cents) * 5)

    # Movement persistence: maximum 25 points.
    score += min(25, metrics["efficiency"] * 25)

    # Quote quality: maximum 15 points.
    score += max(0, 15 - spread_cents * 3)

    # Entry affordability: maximum 15 points.
    score += max(0, min(15, (0.85 - ask) * 100))

    return round(score, 1)


def wait(reason, **details):
    return {
        "decision": "WAIT",
        "reason": reason,
        "strategy": STRATEGY_NAME,
        "version": STRATEGY_VERSION,
        **details,
    }


# ============================================================
# MAIN SIGNAL ENGINE
# ============================================================

def analyze(market, history, seconds_left, verified, pressure=None):
    """Evaluate a paper-only directional entry candidate."""

    if not verified:
        return wait("Contract direction unverified")

    if seconds_left is None or seconds_left <= 0:
        return wait("Contract closed")

    if seconds_left > 660:
        return wait("Collecting market history")

    points = prepare_history(history)

    if len(points) < MIN_OBSERVATIONS:
        return wait(
            f"Collecting observations "
            f"{len(points)}/{MIN_OBSERVATIONS}"
        )

    span = (
        points[-1][0] - points[0][0]
    ).total_seconds()

    if span < MIN_HISTORY_SECONDS:
        return wait(
            f"Collecting history "
            f"{span:.0f}/{MIN_HISTORY_SECONDS}s"
        )

    midpoint = points[-1][1]

    if midpoint >= MIN_MIDPOINT_ABOVE:
        side = "ABOVE"
        key = "yes"
        direction = 1

    elif midpoint <= MAX_MIDPOINT_BELOW:
        side = "BELOW"
        key = "no"
        direction = -1

    else:
        return wait(
            f"NO DIRECTION | Midpoint "
            f"{midpoint * 100:.1f}¢"
        )

    older = older_value(points, MOMENTUM_SECONDS)

    if older is None:
        return wait("Need 25 seconds of quote history")

    momentum_cents = (
        (midpoint - older) * direction * 100
    )

    if momentum_cents < MIN_NET_MOVE * 100:
        return wait(
            f"{side} | Momentum {momentum_cents:+.1f}¢ "
            f"(needs +{MIN_NET_MOVE * 100:.1f}¢)"
        )

    metrics = momentum_metrics(points, side)

    if metrics is None:
        return wait("Insufficient confirmation observations")

    if metrics["net_cents"] < MIN_CONFIRMATION_MOVE * 100:
        return wait(
            f"{side} | Confirmation movement "
            f"{metrics['net_cents']:+.1f}¢ "
            f"(needs +{MIN_CONFIRMATION_MOVE * 100:.1f}¢)"
        )

    if metrics["efficiency"] < MIN_DIRECTIONAL_EFFICIENCY:
        return wait(
            f"{side} | Directional efficiency "
            f"{metrics['efficiency']:.0%} "
            f"(needs {MIN_DIRECTIONAL_EFFICIENCY:.0%})"
        )

    if metrics["reversal_ratio"] > MAX_REVERSAL_RATIO:
        return wait(
            f"{side} | Reversal ratio "
            f"{metrics['reversal_ratio']:.2f} "
            f"(maximum {MAX_REVERSAL_RATIO:.2f})"
        )

    bid = quote(market, key, "bid")
    ask = quote(market, key, "ask")

    if (
        bid is None
        or ask is None
        or not 0 <= bid <= ask <= 1
    ):
        return wait("Invalid bid/ask quotes")

    spread = ask - bid
    spread_cents = spread * 100

    if spread > MAX_SPREAD:
        return wait(
            f"{side} | Spread {spread_cents:.1f}¢ "
            f"exceeds {MAX_SPREAD * 100:.1f}¢"
        )

    if ask > MAX_ENTRY_ASK:
        return wait(
            f"{side} | Entry ask {ask * 100:.1f}¢ "
            f"exceeds {MAX_ENTRY_ASK * 100:.0f}¢"
        )

    flow = number(pressure)

    if flow is not None and -1 <= flow <= 1:
        if side == "ABOVE" and flow < -0.15:
            return wait("Selling pressure opposes ABOVE")

        if side == "BELOW" and flow > 0.15:
            return wait("Buying pressure opposes BELOW")

    score = signal_score(
        midpoint,
        side,
        momentum_cents,
        metrics,
        spread_cents,
        ask,
    )

    if score < MIN_SIGNAL_SCORE:
        return wait(
            f"{side} | Signal score {score:.1f}/100 "
            f"(needs {MIN_SIGNAL_SCORE})",
            score=score,
        )

    return {
        "decision": "ENTER",
        "strategy": STRATEGY_NAME,
        "version": STRATEGY_VERSION,
        "side": side,
        "entry_price": ask,
        "yes_midpoint": midpoint,
        "momentum_cents": round(
            momentum_cents, 3
        ),
        "confirmation_cents": round(
            metrics["net_cents"], 3
        ),
        "efficiency": round(
            metrics["efficiency"], 4
        ),
        "reversal_ratio": round(
            metrics["reversal_ratio"], 4
        ),
        "spread_cents": round(
            spread_cents, 3
        ),
        "flow_score": flow,
        "score": score,
        "reason": (
            f"{side} | Score {score:.1f}/100 | "
            f"25s move {momentum_cents:+.1f}¢ | "
            f"45s confirmation "
            f"{metrics['net_cents']:+.1f}¢ | "
            f"Efficiency {metrics['efficiency']:.0%} | "
            f"Ask {ask * 100:.1f}¢"
        ),
    }
