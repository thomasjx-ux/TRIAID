"""Non-production, read-only value-frontier candidate for frozen T0 studies.

V5 separates State from Transition:
1. Frozen-T0 strategy evidence defines a capped sparse frontier target.
2. The actual pre-decision portfolio is the transition origin.
3. Evidence completeness, current score separation and modeled transaction cost
   determine a continuous transition strength toward that frontier.

There is no fixed top-K allocation gate and no binary incumbent hold. The
per-strategy cap remains an absolute safety boundary only.

No import from the runtime engine, storage, broker or deployment layer. Never
registers, promotes, persists, or places orders. All market inputs are explicit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Any, Iterable, Mapping


VERSION = "value-frontier-shadow@0.5.0"
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
    frontier_target_weights: dict[str, float]
    frontier_target_position_count: int
    score_scale: float
    state_confidence: float
    dominance: float
    cost_factor: float
    transition_strength: float
    concentration_hhi: float
    effective_positions: float
    frozen_t0_only: bool = True
    reads_t1_for_allocation: bool = False
    production_mutation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(name: str, value: Any, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    result=float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name}: nonfinite")
    if minimum is not None and result<minimum:
        raise ValueError(f"{name}: below minimum")
    if maximum is not None and result>maximum:
        raise ValueError(f"{name}: above maximum")
    return result


def _admissible(state: object) -> bool:
    return bool(
        getattr(state,"eligible",False)
        and not getattr(state,"hard_failure",True)
        and getattr(state,"liquidity_ok",False)
        and getattr(state,"capacity_ok",False)
        and getattr(state,"risk_ok",False)
        and getattr(state,"concentration_ok",False)
        and getattr(state,"lifecycle","") in {"active","reduced"}
    )


def _turnover(previous: Mapping[str,float], current: Mapping[str,float]) -> float:
    return sum(
        abs(float(current.get(sid,0.0))-float(previous.get(sid,0.0)))
        for sid in set(previous)|set(current)
    )


def _robust_score_scale(scores: list[float]) -> float:
    if len(scores)<2:
        return 1.0
    center=median(scores)
    mad=median(abs(x-center) for x in scores)
    if mad>EPS:
        return max(EPS,1.4826*mad)
    mean=sum(scores)/len(scores)
    variance=sum((x-mean)**2 for x in scores)/len(scores)
    stdev=math.sqrt(max(0.0,variance))
    if stdev>EPS:
        return stdev
    spread=max(scores)-min(scores)
    if spread>EPS:
        return max(EPS,spread/2.0)
    return 1.0


def _state_confidence(states: Iterable[object], member_ids: set[str]) -> float:
    completeness=[]
    for state in states:
        sid=str(getattr(state,"strategy_id",""))
        if sid not in member_ids or sid==CASH:
            continue
        recent=getattr(state,"recent_returns",None) or []
        completeness.append(min(1.0,len(recent)/63.0))
    if not completeness:
        return 0.0
    return max(0.0,min(1.0,float(median(completeness))))


def _capped_sparse_projection(
    latent: Mapping[str,float],
    *,
    budget: float,
    cap: float,
) -> dict[str,float]:
    """Continuous capped-simplex projection with naturally sparse support."""
    if budget<=EPS or not latent:
        return {}
    feasible_budget=min(float(budget),float(cap)*len(latent))
    if feasible_budget<=EPS:
        return {}

    lo=min(float(v) for v in latent.values())-feasible_budget-2.0
    hi=max(float(v) for v in latent.values())+1.0
    for _ in range(180):
        tau=(lo+hi)/2.0
        total=sum(min(cap,max(0.0,float(v)-tau)) for v in latent.values())
        if total>feasible_budget:
            lo=tau
        else:
            hi=tau
    tau=hi
    result={
        sid:min(cap,max(0.0,float(value)-tau))
        for sid,value in latent.items()
    }
    result={sid:w for sid,w in result.items() if w>EPS}

    total=sum(result.values())
    if result and abs(total-feasible_budget)>1e-9:
        sid=max(result,key=result.get)
        corrected=result[sid]+(feasible_budget-total)
        if corrected<-EPS or corrected>cap+1e-9:
            raise ValueError("projection numerical correction violates cap")
        result[sid]=max(0.0,min(cap,corrected))
    return result


def _concentration(weights: Mapping[str,float]) -> tuple[float,float]:
    risky=[
        float(w) for sid,w in weights.items()
        if sid!=CASH and float(w)>EPS
    ]
    total=sum(risky)
    if total<=EPS:
        return 0.0,0.0
    normalized=[w/total for w in risky]
    hhi=sum(w*w for w in normalized)
    return hhi,(1.0/hhi if hhi>EPS else 0.0)


def allocate_shadow(
    market_id: str,
    states: Iterable[object],
    member_ids: Iterable[str],
    *,
    risk_budget: float = 1.0,
    position_cap: float = 0.28,
    frozen_incumbent: Mapping[str,float] | None = None,
    previous_weights: Mapping[str,float] | None = None,
    modeled_cost_bps: float = 0.0,
    cash_return: float = 0.0,
    absolute_return_calibrated: bool = False,
    min_expected_improvement: float = 0.0,
) -> ShadowDecision:
    """Build a sparse frontier and transition toward it using frozen T0 only.

    frozen_incumbent is retained for compatibility and diagnostics only. It
    never gates the allocation. previous_weights is the true pre-decision state.
    """
    market=str(market_id).strip().upper()
    if not market:
        raise ValueError("market_id required")
    budget=_finite("risk_budget",risk_budget,minimum=0,maximum=1)
    cap=_finite("position_cap",position_cap,minimum=EPS,maximum=1)
    bps=_finite("modeled_cost_bps",modeled_cost_bps,minimum=0)
    cash_yield=_finite("cash_return",cash_return)
    _finite("min_expected_improvement",min_expected_improvement,minimum=0)
    members=set(str(sid) for sid in member_ids)
    if not members:
        raise ValueError("empty member_ids")

    state_rows=list(states)
    seen: dict[str,object]={}
    for state in state_rows:
        sid=str(getattr(state,"strategy_id",""))
        if not sid or sid in seen:
            raise ValueError("missing or duplicate strategy_id")
        seen[sid]=state

    ranked: list[tuple[str,float]]=[]
    exclusions: dict[str,str]={}
    for sid in sorted(members-{CASH}):
        state=seen.get(sid)
        if state is None:
            exclusions[sid]="MISSING_FROZEN_T0_STATE"
        elif not _admissible(state):
            exclusions[sid]="INELIGIBLE_OR_HARD_CONSTRAINT"
        else:
            score=_finite(
                f"{sid}.expected_net_return",
                getattr(state,"expected_net_return"),
            )
            score-=max(
                0.0,
                _finite(
                    f"{sid}.estimated_cost",
                    getattr(state,"estimated_cost",0.0),
                ),
            )
            if absolute_return_calibrated and score<=cash_yield+EPS:
                exclusions[sid]="NOT_ABOVE_CASH_AFTER_COST"
            else:
                ranked.append((sid,score))
    ranked.sort(key=lambda item:(-item[1],item[0]))
    score_map=dict(ranked)

    def validated(raw: Mapping[str,float] | None,label:str)->dict[str,float] | None:
        if raw is None:
            return None
        result={
            str(k):_finite(f"{label}.{k}",v,minimum=0)
            for k,v in raw.items() if float(v)>EPS
        }
        if sum(result.values())>1+EPS:
            raise ValueError(f"{label}: weights exceed 100%")
        return result

    previous=validated(previous_weights,"previous_weights") or {}
    incumbent=validated(frozen_incumbent,"frozen_incumbent")

    scores=[score for _,score in ranked]
    score_scale=_robust_score_scale(scores)
    center=float(median(scores)) if scores else 0.0
    confidence=_state_confidence(state_rows,set(score_map))
    dominance=(
        max(0.0,(max(scores)-center)/max(score_scale,EPS))
        if scores else 0.0
    )
    cost_rate=bps/10000.0
    cost_factor=(
        score_scale/(score_scale+cost_rate)
        if score_scale>EPS else 0.0
    )
    transition_strength=(
        confidence
        * (dominance/(1.0+dominance))
        * cost_factor
    )

    utility={
        sid:(score-center)/max(score_scale,EPS)
        for sid,score in ranked
    }
    target=_capped_sparse_projection(
        utility,
        budget=budget,
        cap=cap,
    )

    # Keep only currently admissible pre-decision exposure. Any exposure released
    # by a newly failed hard constraint remains cash unless the continuous
    # transition toward the current frontier reallocates it.
    prior={
        sid:min(cap,float(previous.get(sid,0.0)))
        for sid in score_map
        if float(previous.get(sid,0.0))>EPS
    }

    if previous_weights is None:
        transition_strength=1.0 if target else 0.0

    weights={
        sid:
            (1.0-transition_strength)*float(prior.get(sid,0.0))
            + transition_strength*float(target.get(sid,0.0))
        for sid in score_map
    }
    weights={sid:w for sid,w in weights.items() if w>EPS}

    risky_total=sum(weights.values())
    if risky_total>budget+1e-9:
        raise ValueError("transition exceeds risk budget")
    if CASH in members:
        residual=max(0.0,1.0-risky_total)
        if residual>EPS:
            weights[CASH]=residual

    def expected(item: Mapping[str,float])->float:
        risky_value=sum(
            float(w)*score_map[sid]
            for sid,w in item.items()
            if sid!=CASH and sid in score_map
        )
        residual_cash=max(
            0.0,
            1.0-sum(float(w) for sid,w in item.items() if sid!=CASH),
        )
        return risky_value+residual_cash*cash_yield

    raw=expected(weights)
    turnover=_turnover(previous,weights) if previous_weights is not None else 0.0
    execution_cost=turnover*cost_rate
    after_cost=raw-execution_cost

    kept=False
    if incumbent is not None:
        ids=set(incumbent)|set(weights)
        kept=all(
            abs(float(incumbent.get(sid,0.0))-float(weights.get(sid,0.0)))<=1e-12
            for sid in ids
        )

    hhi,effective=_concentration(weights)
    return ShadowDecision(
        market_id=market,
        version=VERSION,
        mode="SHADOW_ONLY",
        weights=weights,
        ranked_strategy_ids=[sid for sid,_ in ranked],
        exclusions=exclusions,
        kept_incumbent=kept,
        rationale="SPARSE_FRONTIER_CONTINUOUS_STATE_TRANSITION",
        risk_budget=budget,
        position_cap=cap,
        expected_return_proxy=raw,
        modeled_turnover=turnover,
        modeled_execution_cost=execution_cost,
        expected_after_cost_proxy=after_cost,
        absolute_cash_gate_applied=bool(absolute_return_calibrated),
        allocation_method="SPARSE_FRONTIER_CONTINUOUS_TRANSITION",
        frontier_target_weights=target,
        frontier_target_position_count=len(target),
        score_scale=score_scale,
        state_confidence=confidence,
        dominance=dominance,
        cost_factor=cost_factor,
        transition_strength=transition_strength,
        concentration_hhi=hhi,
        effective_positions=effective,
    )
