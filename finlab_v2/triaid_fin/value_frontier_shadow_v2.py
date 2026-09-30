"""Non-production, read-only value-frontier candidate for frozen T0 studies.

V3 removes the old greedy "rank then fill to a fixed cap" allocation as the
default. Admissible strategies remain subject to hard feasibility and an
absolute safety cap, but risky-sleeve weights are now a continuous function of
relative frozen-T0 score separation. A small rank change therefore produces a
small weight change unless the score structure itself changes materially.

No import from the runtime engine, storage, broker or deployment layer. Never
registers, promotes, persists, or places orders. All market inputs are explicit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Any, Iterable, Mapping


VERSION = "value-frontier-shadow@0.3.0"
CASH = "P28_CASH"
EPS = 1e-12


@dataclass(frozen=True)
class ShadowDecision:
    market_id: str
    version: str
    mode: str
    weights: dict[str, float]
    ranked_strategy_ids: list[str]
    exclusions: dict[str, str]
    kept_incumbent: bool
    rationale: str
    risk_budget: float
    position_cap: float
    expected_return_proxy: float
    modeled_turnover: float
    modeled_execution_cost: float
    expected_after_cost_proxy: float
    absolute_cash_gate_applied: bool
    allocation_method: str
    score_scale: float
    allocation_temperature: float
    state_confidence: float
    concentration_hhi: float
    effective_positions: float
    frozen_t0_only: bool = True
    reads_t1_for_allocation: bool = False
    production_mutation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(name: str, value: Any, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name}: nonfinite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name}: below minimum")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name}: above maximum")
    return result


def _admissible(state: object) -> bool:
    return bool(
        getattr(state, "eligible", False)
        and not getattr(state, "hard_failure", True)
        and getattr(state, "liquidity_ok", False)
        and getattr(state, "capacity_ok", False)
        and getattr(state, "risk_ok", False)
        and getattr(state, "concentration_ok", False)
        and getattr(state, "lifecycle", "") in {"active", "reduced"}
    )


def _turnover(previous: Mapping[str, float], current: Mapping[str, float]) -> float:
    return sum(abs(current.get(sid, 0.0) - previous.get(sid, 0.0))
               for sid in set(previous) | set(current))


def _robust_score_scale(scores: list[float]) -> float:
    """Scale score gaps without tying allocation to an absolute return unit."""
    if len(scores) < 2:
        return 1.0
    center = median(scores)
    mad = median(abs(x-center) for x in scores)
    if mad > EPS:
        return max(EPS, 1.4826 * mad)
    mean = sum(scores) / len(scores)
    variance = sum((x-mean)**2 for x in scores) / len(scores)
    stdev = math.sqrt(max(0.0, variance))
    if stdev > EPS:
        return stdev
    spread = max(scores)-min(scores)
    if spread > EPS:
        return max(EPS, spread/2.0)
    return 1.0


def _state_confidence(states: Iterable[object], member_ids: set[str]) -> float:
    """Use only T0 history depth to flatten uncertain allocations.

    This is deliberately modest: full history does not create leverage or relax
    hard constraints. It only allows score separation to express itself more
    clearly. Sparse history raises the softmax temperature and spreads weight.
    """
    completeness=[]
    for state in states:
        sid=str(getattr(state, "strategy_id", ""))
        if sid not in member_ids or sid == CASH:
            continue
        recent=getattr(state, "recent_returns", None) or []
        completeness.append(min(1.0, len(recent)/63.0))
    if not completeness:
        return 0.0
    return max(0.0, min(1.0, float(median(completeness))))


def _capped_softmax(
    ranked: list[tuple[str, float]],
    *,
    budget: float,
    cap: float,
    temperature: float,
    scale: float,
) -> dict[str, float]:
    """Continuous score allocation with a hard safety cap.

    The cap is now only a boundary. It is not the default target weight.
    """
    if budget <= EPS or not ranked:
        return {}
    max_score=max(score for _,score in ranked)
    denom_scale=max(EPS, float(scale)*max(EPS,float(temperature)))
    raw={}
    for sid,score in ranked:
        z=max(-30.0,min(0.0,(float(score)-max_score)/denom_scale))
        raw[sid]=math.exp(z)

    weights={}
    remaining=max(0.0,float(budget))
    active=set(raw)
    while active and remaining > EPS:
        denom=sum(raw[sid] for sid in active)
        if denom <= EPS:
            equal=remaining/len(active)
            if equal <= cap + EPS:
                for sid in active:
                    weights[sid]=weights.get(sid,0.0)+equal
                remaining=0.0
                break
            for sid in list(active):
                weights[sid]=weights.get(sid,0.0)+cap
                remaining=max(0.0,remaining-cap)
                active.remove(sid)
            continue

        proposals={sid:remaining*raw[sid]/denom for sid in active}
        capped=[sid for sid,w in proposals.items() if w > cap + EPS]
        if not capped:
            for sid,w in proposals.items():
                weights[sid]=weights.get(sid,0.0)+w
            remaining=0.0
            break
        for sid in capped:
            room=max(0.0,cap-weights.get(sid,0.0))
            take=min(room,remaining)
            if take > EPS:
                weights[sid]=weights.get(sid,0.0)+take
                remaining=max(0.0,remaining-take)
            active.remove(sid)

    return {sid:w for sid,w in weights.items() if w > EPS}


def _concentration(weights: Mapping[str,float]) -> tuple[float,float]:
    risky=[float(w) for sid,w in weights.items() if sid != CASH and float(w)>EPS]
    total=sum(risky)
    if total <= EPS:
        return 0.0,0.0
    normalized=[w/total for w in risky]
    hhi=sum(w*w for w in normalized)
    effective=(1.0/hhi) if hhi > EPS else 0.0
    return hhi,effective


def allocate_shadow(
    market_id: str,
    states: Iterable[object],
    member_ids: Iterable[str],
    *,
    risk_budget: float = 1.0,
    position_cap: float = 0.28,
    frozen_incumbent: Mapping[str, float] | None = None,
    previous_weights: Mapping[str, float] | None = None,
    modeled_cost_bps: float = 0.0,
    cash_return: float = 0.0,
    absolute_return_calibrated: bool = False,
    min_expected_improvement: float = 0.0,
) -> ShadowDecision:
    """Build a flexible candidate using frozen T0 states only.

    Relative-only scores rank risky policies but cannot change the cash/risk
    split by sign alone. Weight changes are continuous in score gaps, subject to
    hard feasibility, the matched risky sleeve and the absolute safety cap.
    """
    market = str(market_id).strip().upper()
    if not market:
        raise ValueError("market_id required")
    budget = _finite("risk_budget", risk_budget, minimum=0, maximum=1)
    cap = _finite("position_cap", position_cap, minimum=EPS, maximum=1)
    bps = _finite("modeled_cost_bps", modeled_cost_bps, minimum=0)
    cash_yield = _finite("cash_return", cash_return)
    threshold = _finite("min_expected_improvement", min_expected_improvement, minimum=0)
    members = set(str(sid) for sid in member_ids)
    if not members:
        raise ValueError("empty member_ids")

    state_rows=list(states)
    seen: dict[str, object] = {}
    for state in state_rows:
        sid = str(getattr(state, "strategy_id", ""))
        if not sid or sid in seen:
            raise ValueError("missing or duplicate strategy_id")
        seen[sid] = state

    ranked: list[tuple[str, float]] = []
    exclusions: dict[str, str] = {}
    for sid in sorted(members - {CASH}):
        state = seen.get(sid)
        if state is None:
            exclusions[sid] = "MISSING_FROZEN_T0_STATE"
        elif not _admissible(state):
            exclusions[sid] = "INELIGIBLE_OR_HARD_CONSTRAINT"
        else:
            score = _finite(f"{sid}.expected_net_return", getattr(state, "expected_net_return"))
            score -= max(0.0, _finite(f"{sid}.estimated_cost", getattr(state, "estimated_cost", 0.0)))
            if absolute_return_calibrated and score <= cash_yield + EPS:
                exclusions[sid] = "NOT_ABOVE_CASH_AFTER_COST"
            else:
                ranked.append((sid, score))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    score_map = dict(ranked)

    scores=[score for _,score in ranked]
    score_scale=_robust_score_scale(scores)
    confidence=_state_confidence(state_rows,{sid for sid,_ in ranked})
    # Full T0 history -> 1.0. Sparse history -> progressively flatter weights.
    temperature=1.0+0.75*(1.0-confidence)
    weights=_capped_softmax(
        ranked,
        budget=budget,
        cap=cap,
        temperature=temperature,
        scale=score_scale,
    )

    # Explicit cash is recorded when available; otherwise residual weight
    # remains implicit cash, matching the existing evaluation convention.
    if CASH in members:
        residual=max(0.0,1.0-sum(weights.values()))
        if residual > EPS:
            weights[CASH]=residual

    def validated(raw: Mapping[str, float] | None, label: str) -> dict[str, float] | None:
        if raw is None:
            return None
        result = {str(k): _finite(f"{label}.{k}", v, minimum=0)
                  for k, v in raw.items() if float(v) > EPS}
        if sum(result.values()) > 1 + EPS:
            raise ValueError(f"{label}: weights exceed 100%")
        return result

    prev = validated(previous_weights, "previous_weights")
    incumbent = validated(frozen_incumbent, "frozen_incumbent")

    def feasible_incumbent(item: Mapping[str, float]) -> bool:
        if any(sid not in members for sid in item):
            return False
        risky = {sid: w for sid, w in item.items() if sid != CASH}
        if sum(risky.values()) > budget + EPS:
            return False
        return all(sid in score_map and w <= cap + EPS for sid, w in risky.items())

    def expected(weights_for_score: Mapping[str, float]) -> float:
        risky_value = sum(w * score_map[sid] for sid, w in weights_for_score.items()
                          if sid != CASH)
        residual_cash = max(0.0, 1 - sum(w for sid, w in weights_for_score.items() if sid != CASH))
        return risky_value + residual_cash * cash_yield

    def scored(item: Mapping[str, float]) -> tuple[float, float, float, float]:
        raw = expected(item)
        turnover = _turnover(prev, item) if prev is not None else 0.0
        execution_cost = turnover * bps / 10000.0
        return raw, turnover, execution_cost, raw - execution_cost

    candidate_metrics = scored(weights)
    keep = False
    rationale = "CONTINUOUS_SCORE_FRONTIER_SHADOW_ONLY"
    if incumbent is not None and feasible_incumbent(incumbent):
        incumbent_metrics = scored(incumbent)
        if incumbent_metrics[3] + threshold >= candidate_metrics[3] - EPS:
            keep = True
            rationale = "HOLD_FEASIBLE_INCUMBENT_AFTER_MATCHED_COST_COMPARISON"
            weights = incumbent
            candidate_metrics = incumbent_metrics
    if not ranked and not keep:
        rationale = "NO_ADMISSIBLE_OPPORTUNITY_RESIDUAL_CASH"

    raw, turnover, execution_cost, after_cost = candidate_metrics
    hhi,effective=_concentration(weights)
    return ShadowDecision(
        market_id=market,
        version=VERSION,
        mode="SHADOW_ONLY",
        weights=weights,
        ranked_strategy_ids=[sid for sid, _ in ranked],
        exclusions=exclusions,
        kept_incumbent=keep,
        rationale=rationale,
        risk_budget=budget,
        position_cap=cap,
        expected_return_proxy=raw,
        modeled_turnover=turnover,
        modeled_execution_cost=execution_cost,
        expected_after_cost_proxy=after_cost,
        absolute_cash_gate_applied=bool(absolute_return_calibrated),
        allocation_method="CAPPED_ADAPTIVE_SOFTMAX",
        score_scale=score_scale,
        allocation_temperature=temperature,
        state_confidence=confidence,
        concentration_hhi=hhi,
        effective_positions=effective,
    )
