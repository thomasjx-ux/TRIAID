from __future__ import annotations

import math
from statistics import median
from typing import Iterable

from .objective import PRIMARY_OBJECTIVE, OBJECTIVE_CONSTITUTION
from .contracts import BilingualText, MarketSnapshot, StrategyGroup, StrategyState, TriaidDecision
from .evolution import CoreParameters
from .prediction_adjustment import build_market_prediction_adjustment


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
        pass
    return {k:v for k,v in weights.items() if v>1e-12}


class TriaidCoreModule:
    """Evolvable TRIAID Core boundary with all-market prediction gating."""

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
            kept=[row for row in ranked if row[1]>=selection_threshold-1e-15]
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

        position_cap=float((group.diagnostics or {}).get("max_strategy_weight_constraint",0.28) or 0.28)
        position_cap=max(1e-9,min(1.0,position_cap))
        legacy_target=_normalize_capped(raw,position_cap) if raw else ({"P28_CASH":1.0} if "P28_CASH" in group.members else {})

        market_key=str(market.market_id or "").upper()
        prediction_adjustment=None
        prediction_ready=False
        if market_key in {"US","CN","HK"}:
            candidate_states=[
                state_map[sid]
                for sid in group.members
                if sid in state_map and sid!="P28_CASH"
            ]
            prediction_ready=any(len(s.recent_returns)>=20 for s in candidate_states)
            if prediction_ready:
                phase=str((market.metadata or {}).get("session_phase") or (market.metadata or {}).get("input_phase") or "") or None
                zero_cost_rows=[
                    {"strategy_id":sid,"meta_switch_cost_fraction":0.0}
                    for sid in group.members
                    if sid in state_map
                ]
                prediction_adjustment=build_market_prediction_adjustment(
                    market_key,
                    list(state_map.values()),
                    zero_cost_rows,
                    position_cap,
                    phase,
                )

        target=dict(legacy_target)
        selection_source="LEGACY_RETURN_MAX_CORE"
        if prediction_adjustment is not None:
            target=dict(prediction_adjustment.get("target_strategy_weights") or {})
            if "P28_CASH" not in group.members:
                target.pop("P28_CASH",None)
            selection_source="MARKET_PREDICTION_ADJUSTMENT"

        strength=max(0.0,min(1.0,self.params.intervention_strength))
        effective_strength=(
            1.0
            if prediction_adjustment is not None
            and prediction_adjustment.get("decision_state")=="NO_TRADE"
            else strength
        )
        keys=set(before)|set(target)
        after={
            k:(1-effective_strength)*before.get(k,0.0)+effective_strength*target.get(k,0.0)
            for k in keys
        }
        after={k:max(0.0,v) for k,v in after.items() if v>1e-12}

        risk_budget=float((market.metadata or {}).get("account_risk_budget",1.0) or 1.0)
        risk_budget=max(0.0,min(1.0,risk_budget))
        risky_keys=[k for k in after if k!="P28_CASH"]
        risky_total=sum(after.get(k,0.0) for k in risky_keys)
        risk_budget_scaled=False
        if risky_total>risk_budget+1e-12 and risky_total>0:
            scale=risk_budget/risky_total
            for key in risky_keys:
                after[key]=after[key]*scale
            residual=max(0.0,1.0-sum(after.values()))
            if "P28_CASH" in set(group.members)|set(after):
                after["P28_CASH"]=after.get("P28_CASH",0.0)+residual
            risk_budget_scaled=True
        after={k:max(0.0,v) for k,v in after.items() if v>1e-12}

        reasons={}
        for strategy_id in keys:
            b=before.get(strategy_id,0.0)
            a=after.get(strategy_id,0.0)
            delta=a-b
            no_trade=bool(
                prediction_adjustment is not None
                and prediction_adjustment.get("decision_state")=="NO_TRADE"
            )
            if strategy_id=="P28_CASH":
                if no_trade and a>0:
                    zh="当前市场机会密度不足，No Trade 门已触发，风险预算转为现金等待新的有效状态。"
                    en="Market opportunity density is insufficient. The No Trade gate is active and risk budget is held in cash until a valid state reappears."
                elif abs(delta)<1e-7:
                    zh="现金只保留未被高质量风险策略合理占用的剩余资金，本轮没有需要调整的剩余风险预算。"
                    en="Cash represents only residual risk budget not justified by higher-return admissible strategies; no material residual change is required in this state."
                elif delta>0:
                    zh="当前状态提高了策略进入门槛，受机会密度、单策略上限和可交易约束影响后留下剩余风险预算，因此现金上升。"
                    en="The current state raises the admission threshold; opportunity density, position caps and tradability leave residual risk budget, so cash rises."
                else:
                    zh="当前高质量可交易机会足以吸收更多风险预算，因此减少剩余现金。"
                    en="Higher-quality admissible opportunities can absorb more risk budget, so residual cash falls."
            elif no_trade:
                zh="当前市场机会密度低于可交易门槛，系统主动进入 No Trade 状态并撤回该策略风险预算。"
                en="Market opportunity density is below the tradable threshold, so the system enters No Trade and withdraws risk budget from this strategy."
            elif abs(delta)<1e-7:
                zh="该策略在当前收益排序、持续性和机会密度约束下无需调整。"
                en="No material change is required under the current return ranking, persistence and opportunity-density constraints."
            elif delta>0:
                zh="该策略在当前 H20 状态、持续性和可交易约束下具有更高优先级，因此获得更多风险预算。"
                en="The strategy has higher priority under the current H20 state, persistence and tradability constraints, so it receives more risk budget."
            else:
                zh="该策略当前 H20 优先级落后、持续性不足，或市场机会密度下降，因此降低配置。"
                en="The strategy has lower H20 priority, insufficient persistence, or faces lower market opportunity density, so its allocation is reduced."
            reasons[strategy_id]=BilingualText(zh=zh,en=en)

        opportunity_state=(prediction_adjustment or {}).get("market_opportunity_state")
        return TriaidDecision(
            core_version=self.version,
            weights_before=before,
            weights_after=after,
            reasons=reasons,
            diagnostics={
                "interface_version":self.interface_version,
                "implementation_version":"triaid-core-return-max@0.5.0",
                "objective":PRIMARY_OBJECTIVE,
                "objective_constitution":OBJECTIVE_CONSTITUTION,
                "intervention_strength":self.params.intervention_strength,
                "effective_intervention_strength":effective_strength,
                "selection_source":selection_source,
                "prediction_adjustment_ready":prediction_ready,
                "prediction_adjustment_version":(
                    prediction_adjustment.get("version") if prediction_adjustment else None
                ),
                "prediction_framework_version":(
                    prediction_adjustment.get("framework_version") if prediction_adjustment else None
                ),
                "prediction_decision_state":(
                    prediction_adjustment.get("decision_state") if prediction_adjustment else None
                ),
                "market_opportunity_state":opportunity_state,
                "prediction_selected_strategy_ids":(
                    prediction_adjustment.get("selected_strategy_ids") if prediction_adjustment else []
                ),
                "prediction_invalidation_contract":(
                    prediction_adjustment.get("invalidation_contract") if prediction_adjustment else None
                ),
                "legacy_target_weights":legacy_target,
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
                "max_strategy_weight_constraint":position_cap,
                "account_risk_budget":risk_budget,
                "risk_budget_scaled":risk_budget_scaled,
                "risk_role":"HARD_ADMISSION_AND_STATE_THRESHOLD_CONSTRAINT_NOT_CO_EQUAL_OBJECTIVE",
            },
        )
