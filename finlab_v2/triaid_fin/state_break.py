from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import pstdev
from typing import Any, Iterable

from .contracts import StrategyState


VERSION = "state-break-detector@0.1.0"


def _compound(values: list[float]) -> float:
    equity = 1.0
    for value in values:
        equity *= 1.0 + float(value)
    return equity - 1.0


def _daily_sigma(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(pstdev(values))


@dataclass(frozen=True)
class StateBreakAssessment:
    score: float
    level: str
    brake_factor: float
    permission: str
    reasons: tuple[str, ...]
    metrics: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "version": VERSION,
            "score": float(self.score),
            "level": self.level,
            "brake_factor": float(self.brake_factor),
            "permission": self.permission,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
            "semantics": (
                "Fast evidence may reduce current risk exposure when the prior state stops "
                "explaining observed returns. It cannot reverse the long-horizon direction, "
                "promote a new strategy, or use future outcomes."
            ),
        }


def assess_state_break(
    states: Iterable[StrategyState],
    regime: str | None = None,
) -> StateBreakAssessment:
    """Detect an abrupt break in the current state using only contemporaneous strategy evidence.

    The detector intentionally has braking authority only. Long-horizon strategy ranking remains
    responsible for direction. This separation prevents a one-day shock from becoming an
    unvalidated direction reversal while still allowing rapid exposure reduction.
    """

    rows = [
        state
        for state in states
        if state.strategy_id != "P28_CASH"
        and not bool(state.hard_failure)
        and bool(state.eligible)
        and len(list(state.recent_returns or [])) >= 5
    ]
    if not rows:
        return StateBreakAssessment(
            score=0.0,
            level="STABLE",
            brake_factor=1.0,
            permission="NO_FAST_INTERVENTION",
            reasons=("INSUFFICIENT_CONTEMPORANEOUS_EVIDENCE",),
            metrics={
                "state_count": 0,
                "benchmark_latest_return": None,
                "benchmark_downside_z": None,
                "benchmark_3d_z": None,
                "volatility_ratio_5d_20d": None,
                "negative_breadth": None,
                "leader_fracture_ratio": None,
            },
        )

    state_map = {state.strategy_id: state for state in rows}
    benchmark = state_map.get("P00_BUY_HOLD")
    if benchmark is None:
        benchmark = min(
            rows,
            key=lambda state: (
                abs(float(state.expected_net_return)),
                str(state.strategy_id),
            ),
        )

    benchmark_returns = [float(x) for x in list(benchmark.recent_returns or [])]
    latest = float(benchmark_returns[-1])
    prior = benchmark_returns[:-1]
    prior63 = prior[-63:]
    sigma = max(_daily_sigma(prior63), 1e-6)
    downside_z = latest / sigma

    three = benchmark_returns[-3:]
    three_return = _compound(three)
    three_z = three_return / max(sigma * math.sqrt(max(1, len(three))), 1e-6)

    vol5 = _daily_sigma(benchmark_returns[-5:])
    vol20 = _daily_sigma(prior[-20:]) if len(prior) >= 5 else _daily_sigma(benchmark_returns[-20:])
    vol_ratio = vol5 / max(vol20, 1e-6)

    latest_rows = [
        float(state.recent_returns[-1])
        for state in rows
        if state.recent_returns
    ]
    negative_breadth = (
        sum(1 for value in latest_rows if value < 0.0) / len(latest_rows)
        if latest_rows
        else 0.0
    )

    leaders = sorted(
        rows,
        key=lambda state: (-float(state.expected_net_return), str(state.strategy_id)),
    )[: min(4, len(rows))]
    fractured = 0
    for state in leaders:
        recent = [float(x) for x in list(state.recent_returns or [])]
        if len(recent) < 3:
            continue
        recent3 = _compound(recent[-3:])
        daily_sigma = max(float(state.risk) / math.sqrt(252.0), 1e-6)
        if recent3 <= -1.5 * daily_sigma * math.sqrt(3.0):
            fractured += 1
    leader_fracture_ratio = fractured / max(1, len(leaders))

    score = 0.0
    reasons: list[str] = []

    if downside_z <= -2.5:
        score += 0.30
        reasons.append("BENCHMARK_ONE_DAY_EXTREME_DOWNSIDE")
    elif downside_z <= -1.75:
        score += 0.18
        reasons.append("BENCHMARK_ONE_DAY_DOWNSIDE_BREAK")

    if three_z <= -2.2:
        score += 0.25
        reasons.append("BENCHMARK_THREE_DAY_EXTREME_BREAK")
    elif three_z <= -1.5:
        score += 0.15
        reasons.append("BENCHMARK_THREE_DAY_BREAK")

    if vol_ratio >= 1.8:
        score += 0.20
        reasons.append("SHORT_VOLATILITY_EXPANSION_EXTREME")
    elif vol_ratio >= 1.35:
        score += 0.10
        reasons.append("SHORT_VOLATILITY_EXPANSION")

    if negative_breadth >= 0.75:
        score += 0.15
        reasons.append("BROAD_STRATEGY_DOWNSIDE_BREADTH")
    elif negative_breadth >= 0.60:
        score += 0.08
        reasons.append("STRATEGY_DOWNSIDE_BREADTH")

    if leader_fracture_ratio >= 0.75:
        score += 0.10
        reasons.append("LONG_HORIZON_LEADERS_FRACTURING")
    elif leader_fracture_ratio >= 0.50:
        score += 0.05
        reasons.append("LONG_HORIZON_LEADER_WEAKNESS")

    regime_text = str(regime or "").lower()
    if "risk_on" in regime_text and score >= 0.30:
        # A break matters most when the slow state still says risk-on. This does not create
        # a bearish direction; it only raises braking urgency during a state disagreement.
        score += 0.05
        reasons.append("FAST_SLOW_STATE_DISAGREEMENT")

    score = max(0.0, min(1.0, score))
    if score >= 0.72:
        level = "SEVERE_BREAK"
        brake_factor = 0.45
        permission = "RISK_REDUCTION_ONLY"
    elif score >= 0.55:
        level = "BRAKE"
        brake_factor = 0.70
        permission = "RISK_REDUCTION_ONLY"
    elif score >= 0.38:
        level = "WATCH"
        brake_factor = 0.85
        permission = "RISK_REDUCTION_ONLY"
    else:
        level = "STABLE"
        brake_factor = 1.0
        permission = "NO_FAST_INTERVENTION"

    return StateBreakAssessment(
        score=score,
        level=level,
        brake_factor=brake_factor,
        permission=permission,
        reasons=tuple(reasons or ["NO_STRUCTURAL_BREAK_CONFIRMED"]),
        metrics={
            "state_count": len(rows),
            "benchmark_latest_return": latest,
            "benchmark_downside_z": downside_z,
            "benchmark_3d_return": three_return,
            "benchmark_3d_z": three_z,
            "volatility_5d": vol5,
            "volatility_20d_reference": vol20,
            "volatility_ratio_5d_20d": vol_ratio,
            "negative_breadth": negative_breadth,
            "leader_fracture_ratio": leader_fracture_ratio,
            "leader_count": len(leaders),
            "benchmark_strategy_id": benchmark.strategy_id,
        },
    )
