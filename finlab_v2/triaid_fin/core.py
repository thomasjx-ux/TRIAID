from __future__ import annotations

import math
from statistics import median
from typing import Iterable

from .contracts import BilingualText, MarketSnapshot, StrategyGroup, StrategyState, TriaidDecision
from .evolution import CoreParameters


def _normalize_capped(raw: dict[str, float], cap: float = 0.28) -> dict[str, float]:
    if not raw:
        return {}
    weights={k:0.0 for k in raw}
    positive={k:max(0.0,float(v)) for k,v in raw.items()}
    active={k for k,v in positive.items() if v>0 and k!="P28_CASH"}
    remaining=1.0
    while active and remaining>1e-12:
        total=sum(positive[k] for k in active)
        if total<=0:
            break
        tentative={k:remaining*positive[k]/total for k in active}
        capped=[k for k,w in tentative.items() if w>cap]
        if not capped:
            for k,w in tentative.items():
                weights[k]=w
            remaining=0.0
            break
        for k in capped:
            weights[k]=cap
            remaining-=cap
            active.remove(k)
    if "P28_CASH" in raw:
        weights["P28_CASH"]=max(0.0,remaining)
        remaining=0.0
    elif remaining>1e-12 and weights:
        # No explicit cash strategy was supplied. Keep exposure below 100%;
        # the unallocated residual is treated as cash by the evaluation layer.
        pass
    return {k:v for k,v in weights.items() if v>1e-12}


class TriaidCoreModule:
    """Evolvable TRIAID Core boundary.

    Strategy Population decides which strategies are admissible.
    Core decides whether and how strongly to alter the group's baseline weights.
    Parameters are versioned by EvolutionModule and can be replaced without changing
    Market Data, Strategy Population, Evaluation, Review or Audit.
    """

    interface_version = "triaid-core-contract@1"

    def __init__(self, params: CoreParameters) -> None:
        self.params=params
        self.version=params.version

    def decide(
        self,
        market: MarketSnapshot,
        group: StrategyGroup,
        states: Iterable[StrategyState],
    ) -> TriaidDecision:
        before=dict(group.weights)
        state_map={s.strategy_id:s for s in states}
        regime=(market.regime or "").lower()
        severe_risk=any(x in regime for x in ("stress","bear","shock","high_vol"))
        risk_off=("risk_off" in regime) or severe_risk
        risk_on=("risk_on" in regime) and not risk_off

        ranked=[]
        for strategy_id in group.members:
            if strategy_id=="P28_CASH":
                continue
            s=state_map.get(strategy_id)
            if not s:
                continue
            if (
                not s.eligible
                or s.hard_failure
                or not s.liquidity_ok
                or not s.capacity_ok
                or not s.risk_ok
                or not s.concentration_ok
            ):
                continue
            # Primary objective is return. Risk, liquidity, capacity and
            # concentration are admission constraints, not additive penalties
            # that can silently overturn the return ordering.
            score=float(s.expected_net_return)-max(0.0,float(s.estimated_cost))
            ranked.append((strategy_id,score))

        ranked.sort(key=lambda x:(-x[1],x[0]))
        score_values=[score for _,score in ranked]
        selection_median=median(score_values) if score_values else None
        selection_mad=(
            median([abs(score-selection_median) for score in score_values])
            if score_values else None
        )
        if not ranked:
            selection_threshold=None
            kept=[]
            regime_policy="NO_ADMISSIBLE_STRATEGIES"
        elif risk_on:
            selection_threshold=min(score_values)
            kept=list(ranked)
            regime_policy="RISK_ON_FULL_ADMISSIBLE_GROUP"
        else:
            multiplier=1.5 if severe_risk else 1.0 if risk_off else 0.0
            selection_threshold=float(selection_median)+multiplier*float(selection_mad or 0.0)
            kept=[
                row for row in ranked
                if row[1]>=selection_threshold-1e-15
            ]
            if not kept:
                kept=[ranked[0]]
            regime_policy=(
                "SEVERE_RISK_ADAPTIVE_RETURN_THRESHOLD"
                if severe_risk
                else "RISK_OFF_ADAPTIVE_RETURN_THRESHOLD"
                if risk_off
                else "MIXED_ADAPTIVE_RETURN_THRESHOLD"
            )
        raw={}
        score_temperature=None
        if kept:
            scores=[score for _,score in kept]
            hi=max(scores)
            lo=min(scores)
            spread=hi-lo
            if spread<=1e-12:
                raw={sid:1.0 for sid,_ in kept}
                score_temperature=0.0
            else:
                score_temperature=spread/2.0
                raw={
                    sid:math.exp(max(-50.0,min(0.0,(score-hi)/score_temperature)))
                    for sid,score in kept
                }
        if "P28_CASH" in group.members:
            raw["P28_CASH"]=0.0

        target=_normalize_capped(raw,0.28) if raw else ({"P28_CASH":1.0} if "P28_CASH" in group.members else {})
        strength=max(0.0,min(1.0,self.params.intervention_strength))
        keys=set(before)|set(target)
        after={k:(1-strength)*before.get(k,0.0)+strength*target.get(k,0.0) for k in keys}
        after={k:max(0.0,v) for k,v in after.items() if v>1e-12}

        reasons={}
        for strategy_id in keys:
            b=before.get(strategy_id,0.0)
            a=after.get(strategy_id,0.0)
            delta=a-b
            if strategy_id=="P28_CASH":
                if abs(delta)<1e-7:
                    zh="现金只保留未被高质量风险策略合理占用的剩余资金，本轮没有需要调整的剩余风险预算。"
                    en="Cash represents only residual risk budget not justified by higher-return admissible strategies; no material residual change is required in this state."
                elif delta>0:
                    zh="当前状态提高了策略进入门槛，受单策略上限和可交易约束影响后留下剩余风险预算，因此现金被动上升；现金比例不是固定模板。"
                    en="The current state raises the admission threshold; after position caps and tradability constraints a residual risk budget remains, so cash rises mechanically rather than from a fixed cash template."
                else:
                    zh="当前高收益可交易策略足以吸收更多风险预算，因此减少剩余现金。"
                    en="Higher-return admissible strategies can absorb more of the risk budget, so residual cash falls."
            elif abs(delta)<1e-7:
                zh="该策略在当前收益排序和约束下无需调整。"
                en="No material change is required for this strategy under the current return ranking and constraints."
            elif delta>0:
                zh="该策略在当前可交易策略中具有更高的净收益排序，因此获得更多风险预算。"
                en="The strategy ranks higher on the current realizable net-return proxy and receives more risk budget."
            else:
                zh="该策略当前净收益排序落后，或在更严格的状态门槛下未进入优先集合，因此降低配置。"
                en="The strategy ranks lower on the current net-return proxy or falls outside the stricter state-dependent priority set, so its allocation is reduced."
            reasons[strategy_id]=BilingualText(zh=zh,en=en)

        return TriaidDecision(
            core_version=self.version,
            weights_before=before,
            weights_after=after,
            reasons=reasons,
            diagnostics={
                "interface_version":self.interface_version,
                "implementation_version":"triaid-core-return-max@0.3.0",
                "objective":"MAXIMIZE_REALIZABLE_NET_RETURN_PROXY_SUBJECT_TO_HARD_CONSTRAINTS",
                "intervention_strength":self.params.intervention_strength,
                "legacy_co_objective_parameters_ignored":[
                    "risk_penalty",
                    "uncertainty_penalty",
                    "risk_off_multiplier",
                ],
                "risk_off_detected":risk_off,
                "severe_risk_detected":severe_risk,
                "regime_policy":regime_policy,
                "ranked_opportunities":[{"strategy_id":sid,"net_return_proxy":score} for sid,score in ranked],
                "kept_strategy_ids":[sid for sid,_ in kept],
                "selection_median":selection_median,
                "selection_mad":selection_mad,
                "selection_threshold":selection_threshold,
                "selection_count":len(kept),
                "fixed_strategy_count_target":False,
                "score_temperature":score_temperature,
                "cash_target":float(target.get("P28_CASH",0.0)),
                "cash_is_fixed_template":False,
                "risk_role":"HARD_ADMISSION_AND_STATE_THRESHOLD_CONSTRAINT_NOT_CO_EQUAL_OBJECTIVE",
            },
        )

