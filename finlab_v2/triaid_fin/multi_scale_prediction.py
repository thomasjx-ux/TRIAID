"""Horizon-conditioned state, transition, and cross-scale certification.

This is an internal shadow-only prediction layer. It does not mutate production
decisions, persistence, runtime state, or UI projections. The layer is designed
to test whether TRIAID benefits from representing state as State(tau), where
tau is the prediction horizon, before any production promotion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import mean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence

from .contracts import StrategyState


VERSION = "multi-scale-prediction@0.1.0"
DEFAULT_HORIZONS = (1, 5, 20)


@dataclass(frozen=True)
class HorizonState:
    horizon_days: int
    strategy_count: int
    net_return_mean: float
    recent_return_mean: float
    recent_volatility: float
    risk_mean: float
    uncertainty_mean: float
    signal: float
    noise_scale: float
    confidence: float
    state_label: str
    abstraction_ratio: float
    recent_weight: float
    support_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HorizonTransition:
    horizon_days: int
    status: str
    previous_label: str | None
    current_label: str
    delta_signal: float | None
    direction: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrossScaleCertification:
    passed: bool
    score: float
    reversal_count: int
    timescale_separation_score: float
    active_layers: int
    issues: list[str]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MultiScalePredictionBundle:
    market_id: str
    version: str
    horizons: list[int]
    states: list[HorizonState]
    transitions: list[HorizonTransition]
    certification: CrossScaleCertification

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "version": self.version,
            "horizons": list(self.horizons),
            "states": [row.to_dict() for row in self.states],
            "transitions": [row.to_dict() for row in self.transitions],
            "certification": self.certification.to_dict(),
        }


def _finite(value: float, name: str) -> float:
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite")
    return out


def _validate_horizons(horizons: Sequence[int]) -> tuple[int, ...]:
    out = tuple(int(h) for h in horizons)
    if not out or any(h <= 0 for h in out):
        raise ValueError("horizons must contain positive integers")
    if len(set(out)) != len(out):
        raise ValueError("horizons must be unique")
    if tuple(sorted(out)) != out:
        raise ValueError("horizons must be strictly increasing")
    return out


def _recent_window(state: StrategyState, horizon: int) -> list[float]:
    values = [float(x) for x in state.recent_returns if math.isfinite(float(x))]
    if not values:
        return []
    return values[-min(len(values), max(1, int(horizon))):]


def _state_label(signal: float, noise_scale: float) -> str:
    threshold = max(1e-6, 0.25 * max(0.0, noise_scale))
    if signal > threshold:
        return "POSITIVE"
    if signal < -threshold:
        return "NEGATIVE"
    return "NEUTRAL"


def build_horizon_state(states: Iterable[StrategyState], horizon_days: int) -> HorizonState:
    horizon = int(horizon_days)
    if horizon <= 0:
        raise ValueError("horizon_days must be positive")

    rows = list(states)
    admissible = [
        s for s in rows
        if s.eligible
        and not s.hard_failure
        and s.liquidity_ok
        and s.capacity_ok
        and s.risk_ok
        and s.concentration_ok
    ]
    if not admissible:
        return HorizonState(
            horizon_days=horizon,
            strategy_count=0,
            net_return_mean=0.0,
            recent_return_mean=0.0,
            recent_volatility=0.0,
            risk_mean=0.0,
            uncertainty_mean=1.0,
            signal=0.0,
            noise_scale=1.0,
            confidence=0.0,
            state_label="UNOBSERVED",
            abstraction_ratio=1.0 - 1.0 / math.sqrt(float(horizon)),
            recent_weight=1.0 / math.sqrt(float(horizon)),
            support_ratio=0.0,
        )

    net_returns = [
        _finite(s.expected_net_return, "expected_net_return")
        - max(0.0, _finite(s.estimated_cost, "estimated_cost"))
        for s in admissible
    ]
    risks = [max(0.0, _finite(s.risk, "risk")) for s in admissible]
    uncertainties = [max(0.0, _finite(s.uncertainty, "uncertainty")) for s in admissible]

    per_strategy_recent: list[float] = []
    per_strategy_vol: list[float] = []
    supported = 0
    for state in admissible:
        window = _recent_window(state, horizon)
        if not window:
            continue
        supported += 1
        per_strategy_recent.append(mean(window))
        per_strategy_vol.append(pstdev(window) if len(window) > 1 else 0.0)

    net_return_mean = mean(net_returns)
    recent_return_mean = median(per_strategy_recent) if per_strategy_recent else 0.0
    recent_volatility = median(per_strategy_vol) if per_strategy_vol else 0.0
    risk_mean = mean(risks) if risks else 0.0
    uncertainty_mean = mean(uncertainties) if uncertainties else 0.0
    support_ratio = supported / len(admissible)

    # Short horizons may legitimately depend on fast evidence. As tau grows,
    # recent high-frequency evidence is attenuated while structural expected
    # net return gets more weight. This is intentionally simple and auditable.
    recent_weight = 1.0 / math.sqrt(float(horizon))
    structural_weight = 1.0 - recent_weight
    signal = recent_weight * recent_return_mean + structural_weight * net_return_mean

    sampling_noise = recent_volatility / math.sqrt(float(max(1, horizon)))
    support_penalty = 1.0 - support_ratio
    noise_scale = max(
        1e-9,
        sampling_noise + uncertainty_mean + 0.25 * risk_mean + 0.05 * support_penalty,
    )
    confidence = 1.0 / (1.0 + noise_scale)
    confidence = max(0.0, min(1.0, confidence))

    return HorizonState(
        horizon_days=horizon,
        strategy_count=len(admissible),
        net_return_mean=net_return_mean,
        recent_return_mean=recent_return_mean,
        recent_volatility=recent_volatility,
        risk_mean=risk_mean,
        uncertainty_mean=uncertainty_mean,
        signal=signal,
        noise_scale=noise_scale,
        confidence=confidence,
        state_label=_state_label(signal, noise_scale),
        abstraction_ratio=1.0 - recent_weight,
        recent_weight=recent_weight,
        support_ratio=support_ratio,
    )


def _transition(
    current: HorizonState,
    previous: HorizonState | None,
) -> HorizonTransition:
    if previous is None or previous.state_label == "UNOBSERVED":
        return HorizonTransition(
            horizon_days=current.horizon_days,
            status="UNOBSERVED",
            previous_label=None if previous is None else previous.state_label,
            current_label=current.state_label,
            delta_signal=None,
            direction="UNKNOWN",
            confidence=0.0,
        )
    delta = current.signal - previous.signal
    threshold = max(1e-6, 0.25 * max(current.noise_scale, previous.noise_scale))
    if delta > threshold:
        direction = "IMPROVING"
    elif delta < -threshold:
        direction = "DETERIORATING"
    else:
        direction = "STABLE"
    return HorizonTransition(
        horizon_days=current.horizon_days,
        status="OBSERVED",
        previous_label=previous.state_label,
        current_label=current.state_label,
        delta_signal=delta,
        direction=direction,
        confidence=min(current.confidence, previous.confidence),
    )


def _direction_code(label: str) -> int:
    if label == "POSITIVE":
        return 1
    if label == "NEGATIVE":
        return -1
    return 0


def certify_cross_scale(states: Sequence[HorizonState]) -> CrossScaleCertification:
    ordered = sorted(states, key=lambda row: row.horizon_days)
    if not ordered:
        return CrossScaleCertification(
            passed=False,
            score=0.0,
            reversal_count=0,
            timescale_separation_score=0.0,
            active_layers=0,
            issues=["NO_HORIZON_STATE"],
            diagnostics={},
        )

    informative = [_direction_code(row.state_label) for row in ordered]
    informative = [x for x in informative if x != 0]
    reversal_count = sum(
        1 for left, right in zip(informative, informative[1:])
        if left != right
    )

    issues: list[str] = []
    score = 1.0
    # One cross-horizon reversal is allowed: short-term stress can coexist with
    # a healthier medium/long horizon. A double reversal is structurally less
    # plausible and must be explained by stronger evidence.
    if reversal_count > 1:
        score -= min(0.6, 0.35 * (reversal_count - 1))
        issues.append("MULTIPLE_DIRECTION_REVERSALS")

    abstraction = [row.abstraction_ratio for row in ordered]
    if any(right + 1e-12 < left for left, right in zip(abstraction, abstraction[1:])):
        score -= 0.25
        issues.append("ABSTRACTION_NOT_MONOTONIC")

    pairwise: list[float] = []
    for left, right in zip(ordered, ordered[1:]):
        scale = max(1e-6, left.noise_scale, right.noise_scale)
        pairwise.append(abs(right.signal - left.signal) / scale)
    mean_separation = mean(pairwise) if pairwise else 0.0
    timescale_separation_score = 1.0 - math.exp(-max(0.0, mean_separation))
    timescale_separation_score = max(0.0, min(1.0, timescale_separation_score))
    if len(ordered) == 1 or timescale_separation_score < 0.15:
        active_layers = 1
    elif timescale_separation_score < 0.45:
        active_layers = min(2, len(ordered))
    else:
        active_layers = min(3, len(ordered))

    # Long-horizon claims with no historical support should not receive a green
    # certification merely because the deterministic abstraction is smooth.
    unsupported_long = [
        row.horizon_days for row in ordered
        if row.horizon_days > 1 and row.support_ratio <= 0.0
    ]
    if unsupported_long:
        score -= min(0.35, 0.15 * len(unsupported_long))
        issues.append("LONG_HORIZON_WITHOUT_RECENT_SUPPORT")

    score = max(0.0, min(1.0, score))
    passed = score >= 0.65 and all(row.state_label != "UNOBSERVED" for row in ordered)

    return CrossScaleCertification(
        passed=passed,
        score=score,
        reversal_count=reversal_count,
        timescale_separation_score=timescale_separation_score,
        active_layers=active_layers,
        issues=issues,
        diagnostics={
            "one_reversal_allowed": True,
            "certification_threshold": 0.65,
            "horizon_count": len(ordered),
            "state_labels": {
                str(row.horizon_days): row.state_label for row in ordered
            },
            "signals": {
                str(row.horizon_days): row.signal for row in ordered
            },
            "confidences": {
                str(row.horizon_days): row.confidence for row in ordered
            },
        },
    )


def build_multiscale_prediction(
    market_id: str,
    states: Iterable[StrategyState],
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    previous: MultiScalePredictionBundle | Mapping[str, Any] | None = None,
) -> MultiScalePredictionBundle:
    horizon_values = _validate_horizons(horizons)
    frozen_states = list(states)
    current_states = [
        build_horizon_state(frozen_states, horizon)
        for horizon in horizon_values
    ]

    previous_by_horizon: dict[int, HorizonState] = {}
    if isinstance(previous, MultiScalePredictionBundle):
        previous_by_horizon = {
            row.horizon_days: row for row in previous.states
        }
    elif isinstance(previous, Mapping):
        for row in previous.get("states", []) or []:
            try:
                parsed = HorizonState(**dict(row))
            except Exception:
                continue
            previous_by_horizon[parsed.horizon_days] = parsed

    transitions = [
        _transition(row, previous_by_horizon.get(row.horizon_days))
        for row in current_states
    ]
    certification = certify_cross_scale(current_states)
    return MultiScalePredictionBundle(
        market_id=str(market_id).upper(),
        version=VERSION,
        horizons=list(horizon_values),
        states=current_states,
        transitions=transitions,
        certification=certification,
    )
