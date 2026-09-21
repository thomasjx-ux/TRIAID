from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Dict, Iterable, List

from .contracts import BilingualText, StrategyDefinition, StrategyGroup, StrategyState
from .strategy_registry import FAMILIES, build_definitions


@dataclass(frozen=True)
class PopulationConfig:
    config_version: str
    market_id: str
    review_windows: tuple[int, ...]
    entry_confirm_days: int
    exit_confirm_days: int
    cooldown_days: int
    max_weight: float
    near_duplicate_corr: float = 0.995
    family_cap: int = 3
    redundancy_penalty: float = 0.35
    uncertainty_penalty: float = 0.50
    switch_hurdle_bps: float = 5.0
    switch_uncertainty_fraction: float = 0.25
    switch_guard_enabled: bool = True
    scan_frequency: str = "hourly"
    allocation_review_frequency: str = "daily"
    population_review_frequency: str = "weekly"
    downgrade_min_days: int = 20
    downgrade_min_horizons: float = 3.0
    downgrade_min_decisions: int = 20
    retirement_min_days: int = 60
    retirement_min_horizons: float = 6.0
    retirement_min_decisions: int = 30
    retirement_target_decisions: int = 50


US_CONFIG = PopulationConfig(
    config_version="population-us@0.5.0",
    market_id="US",
    review_windows=(21, 63, 126, 252),
    entry_confirm_days=3,
    exit_confirm_days=3,
    cooldown_days=5,
    max_weight=0.28,
    near_duplicate_corr=0.995,
    family_cap=3,
    redundancy_penalty=0.35,
    uncertainty_penalty=0.50,
    switch_hurdle_bps=5.0,
    switch_uncertainty_fraction=0.0,
    switch_guard_enabled=False,
)

CN_CONFIG = PopulationConfig(
    config_version="population-cn@0.5.0",
    market_id="CN",
    review_windows=(21, 63, 126, 252),
    entry_confirm_days=5,
    exit_confirm_days=3,
    cooldown_days=10,
    max_weight=0.28,
    near_duplicate_corr=0.990,
    family_cap=3,
    redundancy_penalty=0.30,
    uncertainty_penalty=0.60,
    switch_hurdle_bps=8.0,
    switch_uncertainty_fraction=0.0,
    switch_guard_enabled=True,
)


class StrategyPopulationModule:
    version = "strategy-population@0.5.5"

    def __init__(self) -> None:
        self._registry: Dict[str, StrategyDefinition] = {}
        self._config_overrides: Dict[str, PopulationConfig] = {}
        for definition in build_definitions():
            self.register(definition)

    def configure_market(self, profile) -> None:
        key=profile.market_id.upper()
        self._config_overrides[key]=PopulationConfig(
            config_version=profile.version,
            market_id=key,
            review_windows=(21,63,126,252),
            entry_confirm_days=int(profile.entry_confirm_days),
            exit_confirm_days=int(profile.exit_confirm_days),
            cooldown_days=int(profile.cooldown_days),
            max_weight=float(profile.max_weight),
            near_duplicate_corr=float(getattr(profile,"near_duplicate_corr",0.995 if key=="US" else 0.990)),
            family_cap=int(getattr(profile,"family_cap",3)),
            redundancy_penalty=float(getattr(profile,"redundancy_penalty",0.35 if key=="US" else 0.30)),
            uncertainty_penalty=float(getattr(profile,"uncertainty_penalty",0.50 if key=="US" else 0.60)),
            switch_hurdle_bps=float(getattr(profile,"switch_hurdle_bps",5.0 if key=="US" else 8.0)),
            switch_uncertainty_fraction=float(getattr(profile,"switch_uncertainty_fraction",0.25)),
            switch_guard_enabled=bool(getattr(profile,"switch_guard_enabled",True)),
        )

    def config_for(self, market_id: str) -> PopulationConfig:
        key = market_id.upper()
        if key in {"A", "A_SHARE", "ASHARE"}:
            key="CN"
        if key in self._config_overrides:
            return self._config_overrides[key]
        return CN_CONFIG if key=="CN" else US_CONFIG

    def rules(self, market_id: str) -> dict:
        cfg = self.config_for(market_id)
        result = asdict(cfg)
        result["selection_objective"] = "maximize robust multi-window annualized state-return score subject to lifecycle, liquidity, capacity, concentration and redundancy constraints"
        result["group_optimizer"] = "return-first group construction with versioned marginal-value, redundancy and switching constraints; unvalidated diversity penalties remain disabled"
        result["switch_rule"] = "replace the current group only when improvement in the robust state-return score exceeds annualized switching cost, uncertainty guard and switching hurdle"
        result["exposure_rule"] = "only ACTIVE or REDUCED strategies can receive experimental weight; SHADOW receives no exposure"
        result["cash_rule"] = "unallocated weight is explicit cash when P28_CASH is available"
        result["empty_group_allowed"] = True
        if cfg.market_id=="CN":
            result["active_research_experiment"]="CN_WORST_POOL_RESCUE"
            result["market_route"]="CN_RECOVERY_CAPACITY"
            result["research_experiment_objective"]="deliberately start from an equal-weight pool of the currently worst eligible risky strategies, then measure how much loss TRIAID can reduce without using future outcomes"
        else:
            result["active_research_experiment"]="US_RETURN_MAX_CAPACITY"
            result["market_route"]="US_RETURN_MAXIMIZATION"
            result["research_experiment_objective"]="strictly maximize the current multi-window annualized state-return estimate across ACTIVE strategies; break exact score ties by lower estimated cost, risk, uncertainty and deterministic strategy ID, then validate posterior theoretical and simulated-execution return separately under four USD capital sleeves"
            result["primary_route_selector"]="STRICT_MAX_EXPECTED_NET_RETURN_WITH_DETERMINISTIC_TIE_BREAK"
            result["generic_population_role"]="CONTROL_AND_INFRASTRUCTURE_ONLY_FOR_US_RETURN_MAX_ROUTE"
        return result

    def register(self, definition: StrategyDefinition) -> None:
        self._registry[definition.strategy_id] = definition

    def definitions(self) -> List[StrategyDefinition]:
        return [self._registry[k] for k in sorted(self._registry)]

    def definition(self, strategy_id: str) -> StrategyDefinition | None:
        return self._registry.get(strategy_id)

    def strategy_cards(self, lang: str = "zh", market_id: str | None = None) -> List[dict]:
        cards = []
        market_key=market_id.upper() if market_id else None
        for item in self.definitions():
            if market_key and market_key not in {m.upper() for m in item.market_support}:
                continue
            cards.append(
                {
                    "strategy_id": item.strategy_id,
                    "version": item.version,
                    "name": getattr(item.name, lang),
                    "summary": getattr(item.summary, lang),
                    "logic": getattr(item.logic, lang),
                    "best_conditions": getattr(item.best_conditions, lang),
                    "main_risks": getattr(item.main_risks, lang),
                }
            )
        return cards

    def recommend_lifecycle(self, state: StrategyState, market_id: str) -> str:
        cfg = self.config_for(market_id)
        if state.hard_failure:
            return "frozen"
        positive_oos = state.oos_marginal_value is not None and state.oos_marginal_value > 0

        if state.lifecycle == "research":
            return "candidate" if state.eligible and positive_oos else "research"
        if state.lifecycle == "candidate":
            return "shadow" if state.eligible and positive_oos else "candidate"
        if state.lifecycle == "shadow":
            return "active" if state.eligible and positive_oos and state.shadow_evidence_pass else "shadow"

        downgrade_evidence = (
            state.evidence_days >= cfg.downgrade_min_days
            and state.horizon_multiples >= cfg.downgrade_min_horizons
            and state.independent_decisions >= cfg.downgrade_min_decisions
        )
        retirement_evidence = (
            state.evidence_days >= cfg.retirement_min_days
            and state.horizon_multiples >= cfg.retirement_min_horizons
            and state.independent_decisions >= cfg.retirement_min_decisions
        )
        underperforming = state.expected_net_return <= 0 or (
            state.oos_marginal_value is not None and state.oos_marginal_value <= 0
        )

        if state.lifecycle == "active" and downgrade_evidence and underperforming:
            return "reduced"
        if state.lifecycle == "reduced" and downgrade_evidence and underperforming:
            return "frozen"
        if state.lifecycle == "frozen":
            if state.new_evidence_pass and positive_oos:
                return "candidate"
            if retirement_evidence and underperforming:
                return "retired"
            return "frozen"
        if state.lifecycle == "retired" and state.new_evidence_pass and positive_oos:
            return "candidate"
        return state.lifecycle

    @staticmethod
    def _corr(a: List[float], b: List[float], lookback: int = 126) -> float:
        n=min(len(a),len(b),lookback)
        if n<20:
            return 0.0
        x=a[-n:];y=b[-n:]
        mx=mean(x);my=mean(y)
        dx=[v-mx for v in x];dy=[v-my for v in y]
        vx=sum(v*v for v in dx);vy=sum(v*v for v in dy)
        if vx<=1e-18 or vy<=1e-18:
            return 0.0
        return max(-1.0,min(1.0,sum(i*j for i,j in zip(dx,dy))/math.sqrt(vx*vy)))

    @staticmethod
    def _robust_return(state: StrategyState, cfg: PopulationConfig) -> float:
        return (
            float(state.expected_net_return)
            - cfg.uncertainty_penalty*max(0.0,float(state.uncertainty))
            - max(0.0,float(state.estimated_cost))
        )

    @staticmethod
    def _allocate_scores_with_cap(scores: Dict[str,float], cap: float) -> tuple[Dict[str,float],float]:
        positive={k:max(0.0,float(v)) for k,v in scores.items() if k!="P28_CASH" and v>0}
        active=set(positive)
        weights:Dict[str,float]={}
        remaining=1.0
        while active and remaining>1e-12:
            total=sum(positive[k] for k in active)
            if total<=0:
                break
            tentative={k:remaining*positive[k]/total for k in active}
            capped=[k for k,w in tentative.items() if w>cap]
            if not capped:
                weights.update(tentative)
                remaining=0.0
                break
            for k in capped:
                weights[k]=cap
                remaining-=cap
                active.remove(k)
        return weights,max(0.0,remaining)

    @staticmethod
    def should_switch(
        current_expected_net_return: float,
        candidate_expected_net_return: float,
        switching_cost: float,
        uncertainty_guard: float,
        hurdle: float = 0.0,
    ) -> bool:
        return candidate_expected_net_return-current_expected_net_return > switching_cost+uncertainty_guard+hurdle

    def _candidate_group(
        self,
        cfg: PopulationConfig,
        states: List[StrategyState],
        max_members: int,
    ) -> tuple[Dict[str,float],dict]:
        feasible=[
            s for s in states
            if s.strategy_id!="P28_CASH"
            and s.eligible
            and s.lifecycle in {"active","reduced"}
            and not s.hard_failure
            and s.liquidity_ok and s.capacity_ok and s.risk_ok and s.concentration_ok
            and self._robust_return(s,cfg)>0
        ]
        feasible=sorted(feasible,key=lambda s:self._robust_return(s,cfg),reverse=True)

        selected:List[StrategyState]=[]
        marginal_scores:Dict[str,float]={}
        duplicate_rejections=[]
        family_rejections=[]
        nonpositive_marginal=[]

        for state in feasible:
            if len(selected)>=max_members:
                break
            family=FAMILIES.get(state.strategy_id,"unknown")
            if sum(1 for s in selected if FAMILIES.get(s.strategy_id,"unknown")==family)>=cfg.family_cap:
                family_rejections.append(state.strategy_id)
                continue

            if cfg.redundancy_penalty<=0 and cfg.near_duplicate_corr>1.0:
                max_corr=0.0
            else:
                correlations=[abs(self._corr(state.recent_returns,s.recent_returns)) for s in selected]
                max_corr=max(correlations) if correlations else 0.0
            if max_corr>=cfg.near_duplicate_corr:
                duplicate_rejections.append({"strategy_id":state.strategy_id,"max_corr":max_corr})
                continue

            robust=self._robust_return(state,cfg)
            marginal=robust-cfg.redundancy_penalty*max_corr*max(0.0,state.risk)
            if marginal<=0:
                nonpositive_marginal.append({"strategy_id":state.strategy_id,"marginal":marginal,"max_corr":max_corr})
                continue
            selected.append(state)
            marginal_scores[state.strategy_id]=marginal

        weights,remaining=self._allocate_scores_with_cap(marginal_scores,cfg.max_weight)
        cash=next((s for s in states if s.strategy_id=="P28_CASH" and s.eligible and s.lifecycle in {"active","reduced"}),None)
        if cash is not None and (remaining>1e-12 or not weights):
            weights["P28_CASH"]=remaining if weights else 1.0

        diagnostics={
            "optimizer":"marginal-group-value-v1",
            "feasible_count":len(feasible),
            "selected_risky_count":len([k for k in weights if k!="P28_CASH"]),
            "duplicate_rejections":duplicate_rejections,
            "family_rejections":family_rejections,
            "nonpositive_marginal":nonpositive_marginal,
            "marginal_scores":marginal_scores,
        }
        return weights,diagnostics

    def _adversarial_loss_group(
        self,
        cfg:PopulationConfig,
        states:List[StrategyState],
        max_members:int,
    )->StrategyGroup:
        state_map={s.strategy_id:s for s in states}
        feasible=[
            s for s in states
            if s.strategy_id!="P28_CASH"
            and s.eligible
            and s.lifecycle in {"active","reduced"}
            and not s.hard_failure
            and s.liquidity_ok and s.capacity_ok and s.risk_ok and s.concentration_ok
        ]
        ranked=sorted(feasible,key=lambda s:self._robust_return(s,cfg))
        risky_slots=max(1,min(10,max_members-1 if max_members>1 else 1))
        selected=ranked[:risky_slots]
        cash=next(
            (
                s for s in states
                if s.strategy_id=="P28_CASH"
                and s.eligible
                and s.lifecycle in {"active","reduced"}
            ),
            None,
        )

        if not selected:
            weights={"P28_CASH":1.0} if cash is not None else {}
            members=list(weights)
            reasons={
                "P28_CASH":BilingualText(
                    zh="当前没有满足基础可交易约束的风险策略，逆向压力实验无法构造风险池，因此退回现金并标记实验不可用。",
                    en="No risky strategy satisfies the basic tradability constraints, so the adversarial loss-pool experiment cannot be constructed and falls back to cash.",
                )
            } if cash is not None else {}
            diagnostics={
                "optimizer":"adversarial-worst-pool-v1",
                "experiment_mode":"CN_WORST_POOL_RESCUE",
                "experiment_available":False,
                "feasible_count":0,
                "stress_pool_size":0,
                "baseline_cash_weight":1.0 if cash is not None else 0.0,
                "selection_mode":"ADVERSARIAL_FALLBACK_CASH",
            }
            return StrategyGroup(
                group_version=self.version,
                config_version=cfg.config_version,
                market_id="CN",
                members=members,
                weights=weights,
                reasons=reasons,
                diagnostics=diagnostics,
            )

        equal_weight=1.0/len(selected)
        weights={s.strategy_id:equal_weight for s in selected}
        members=[s.strategy_id for s in selected]
        if cash is not None:
            weights["P28_CASH"]=0.0
            members.append("P28_CASH")

        reasons:Dict[str,BilingualText]={}
        for rank,state in enumerate(selected,1):
            definition=self.definition(state.strategy_id)
            robust=self._robust_return(state,cfg)
            reasons[state.strategy_id]=BilingualText(
                zh=f"逆向压力实验第 {rank} 位：{definition.name.zh if definition else state.strategy_id} 在当前可见数据下的稳健状态收益分数为 {robust:.2%}，位于可交易策略的最差端。本次入选不是推荐，而是故意构造不利起点，用于检验 TRIAID 能挽回多少损失。",
                en=f"Adversarial stress rank {rank}: {definition.name.en if definition else state.strategy_id} has robust state-return score {robust:.2%}, placing it among the weakest currently tradable strategies. Selection is intentionally adverse, not a recommendation, so TRIAID loss-reduction can be measured.",
            )
        if cash is not None:
            reasons["P28_CASH"]=BilingualText(
                zh="现金在压力池基线中的权重固定为 0，仅作为 TRIAID 介入后可以转移风险敞口的避险出口。",
                en="Cash starts at zero weight in the stress baseline and exists only as a defensive destination available to TRIAID after intervention.",
            )

        baseline_projected=sum(equal_weight*self._robust_return(s,cfg) for s in selected)
        diagnostics={
            "optimizer":"adversarial-worst-pool-v1",
            "experiment_mode":"CN_WORST_POOL_RESCUE",
            "experiment_available":True,
            "selection_mode":"ADVERSARIAL_WORST_POOL",
            "feasible_count":len(feasible),
            "stress_pool_size":len(selected),
            "baseline_cash_weight":0.0,
            "baseline_projected_robust_return":baseline_projected,
            "ranking_metric":"robust_expected_net_return_ascending",
            "weighting_rule":"equal_weight_no_future_outcome",
            "ranked_worst":[
                {
                    "rank":i+1,
                    "strategy_id":s.strategy_id,
                    "robust_expected_net_return":self._robust_return(s,cfg),
                    "expected_net_return":float(s.expected_net_return),
                    "risk":float(s.risk),
                    "uncertainty":float(s.uncertainty),
                }
                for i,s in enumerate(selected)
            ],
        }
        return StrategyGroup(
            group_version=self.version,
            config_version=cfg.config_version,
            market_id="CN",
            members=members,
            weights=weights,
            reasons=reasons,
            diagnostics=diagnostics,
        )

    def _projected_robust_return(
        self,
        weights:Dict[str,float],
        state_map:Dict[str,StrategyState],
        cfg:PopulationConfig,
    )->float:
        return sum(
            float(w)*(0.0 if sid=="P28_CASH" else self._robust_return(state_map[sid],cfg))
            for sid,w in weights.items()
            if sid=="P28_CASH" or sid in state_map
        )

    def select(
        self,
        market_id: str,
        states: Iterable[StrategyState],
        max_members: int,
        previous_group: StrategyGroup | None = None,
        base_cost_bps: float = 2.0,
        experiment_mode: str | None = None,
    ) -> StrategyGroup:
        cfg=self.config_for(market_id)
        states=list(states)
        mode=str(experiment_mode or "").upper()
        if market_id.upper()=="CN" and mode=="CN_WORST_POOL_RESCUE":
            return self._adversarial_loss_group(cfg,states,max_members)
        state_map={s.strategy_id:s for s in states}
        candidate_weights,diagnostics=self._candidate_group(cfg,states,max_members)

        use_previous=False
        if not cfg.switch_guard_enabled:
            diagnostics["selection_mode"]="RETURN_FIRST_RESELECT"
        elif previous_group is not None and previous_group.market_id.upper()==market_id.upper():
            invalid=[
                sid for sid in previous_group.members
                if sid!="P28_CASH" and (
                    sid not in state_map
                    or not state_map[sid].eligible
                    or state_map[sid].hard_failure
                    or state_map[sid].lifecycle not in {"active","reduced"}
                    or not state_map[sid].risk_ok
                    or not state_map[sid].capacity_ok
                    or not state_map[sid].liquidity_ok
                )
            ]
            if not invalid:
                previous_weights={sid:float(w) for sid,w in previous_group.weights.items() if sid=="P28_CASH" or sid in state_map}
                current_value=self._projected_robust_return(previous_weights,state_map,cfg)
                candidate_value=self._projected_robust_return(candidate_weights,state_map,cfg)
                turnover=sum(abs(candidate_weights.get(k,0.0)-previous_weights.get(k,0.0)) for k in set(candidate_weights)|set(previous_weights))
                holding_days=max(1,min(cfg.review_windows))
                annualizer=252.0/holding_days
                switching_cost=turnover*max(0.0,float(base_cost_bps))/10000.0*annualizer
                uncertainty_guard=cfg.switch_uncertainty_fraction*sum(
                    candidate_weights.get(sid,0.0)*max(0.0,state_map[sid].uncertainty)
                    for sid in candidate_weights if sid in state_map
                )
                hurdle=cfg.switch_hurdle_bps/10000.0*annualizer
                diagnostics["switch_test"]={
                    "current_projected_robust_return":current_value,
                    "candidate_projected_robust_return":candidate_value,
                    "candidate_advantage":candidate_value-current_value,
                    "turnover":turnover,
                    "annualized_switching_cost":switching_cost,
                    "uncertainty_guard":uncertainty_guard,
                    "annualized_hurdle":hurdle,
                    "invalid_previous_members":invalid,
                }
                if not self.should_switch(current_value,candidate_value,switching_cost,uncertainty_guard,hurdle):
                    candidate_weights=previous_weights
                    use_previous=True
                    diagnostics["selection_mode"]="HOLD_CURRENT_GROUP"
                else:
                    diagnostics["selection_mode"]="SWITCH_TO_CANDIDATE"
            else:
                diagnostics["selection_mode"]="FORCED_SWITCH_INVALID_PREVIOUS"
                diagnostics["invalid_previous_members"]=invalid
        else:
            diagnostics["selection_mode"]="INITIAL_GROUP"

        members=[sid for sid,w in candidate_weights.items() if w>1e-12]
        reasons:Dict[str,BilingualText]={}
        for sid in members:
            if sid=="P28_CASH":
                reasons[sid]=BilingualText(
                    zh="没有被当前最优风险策略占用的资金保留为现金，不为了满仓而强行承担低质量风险。",
                    en="Capital not justified by the current best risky strategies remains in cash rather than forcing low-quality exposure.",
                )
                continue
            s=state_map[sid]
            definition=self.definition(sid)
            if use_previous:
                reasons[sid]=BilingualText(
                    zh=f"{definition.name.zh if definition else sid} 继续保留。候选策略群扣除换群成本和不确定性后，没有足够净优势支持切换。",
                    en=f"{definition.name.en if definition else sid} is retained because the candidate group does not offer enough net improvement after switching cost and uncertainty.",
                )
            else:
                family=FAMILIES.get(sid,"unknown")
                diversification_on=cfg.redundancy_penalty>0 or cfg.near_duplicate_corr<=1.0 or cfg.family_cap<max_members
                if diversification_on:
                    zh_reason=f"并通过相关性、{family} 家族集中度、风险、流动性和容量约束后仍有正的群组边际价值。"
                    en_reason=f"and retains positive marginal group value after correlation, {family} family concentration, risk, liquidity and capacity constraints."
                else:
                    zh_reason="并通过风险、流动性、容量与收益约束后进入当前收益优先策略群。"
                    en_reason="and enters the return-first group after risk, liquidity, capacity and return constraints."
                reasons[sid]=BilingualText(
                    zh=f"{definition.name.zh if definition else sid} 的稳健状态收益分数为 {self._robust_return(s,cfg):.2%}，{zh_reason}",
                    en=f"{definition.name.en if definition else sid} has robust state-return score {self._robust_return(s,cfg):.2%} {en_reason}",
                )

        return StrategyGroup(
            group_version=self.version,
            config_version=cfg.config_version,
            market_id=market_id,
            members=members,
            weights=candidate_weights,
            reasons=reasons,
            diagnostics=diagnostics,
        )
