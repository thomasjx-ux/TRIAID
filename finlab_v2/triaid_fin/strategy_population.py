from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List

from .contracts import BilingualText, StrategyDefinition, StrategyGroup, StrategyState


@dataclass(frozen=True)
class PopulationConfig:
    config_version: str
    market_id: str
    review_windows: tuple[int, ...]
    entry_confirm_days: int
    exit_confirm_days: int
    cooldown_days: int
    max_weight: float
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
    config_version="population-us@0.2.0",
    market_id="US",
    review_windows=(21, 63, 126, 252),
    entry_confirm_days=3,
    exit_confirm_days=3,
    cooldown_days=5,
    max_weight=0.28,
)

CN_CONFIG = PopulationConfig(
    config_version="population-cn@0.2.0",
    market_id="CN",
    review_windows=(21, 63, 126, 252),
    entry_confirm_days=5,
    exit_confirm_days=3,
    cooldown_days=10,
    max_weight=0.28,
)


class StrategyPopulationModule:
    """Reference module for dynamic strategy groups.

    It owns registry, state interpretation, lifecycle, group selection and weighting.
    TRIAID Core consumes the selected group but does not own these rules.
    """

    version = "strategy-population@0.2.0"

    def __init__(self) -> None:
        self._registry: Dict[str, StrategyDefinition] = {}

    def config_for(self, market_id: str) -> PopulationConfig:
        key = market_id.upper()
        if key in {"CN", "A", "A_SHARE", "ASHARE"}:
            return CN_CONFIG
        return US_CONFIG

    def rules(self, market_id: str) -> dict:
        cfg = self.config_for(market_id)
        result = asdict(cfg)
        result["selection_objective"] = "maximize expected net return subject to risk, cost, liquidity, capacity and concentration constraints"
        result["switch_rule"] = "switch only when expected net improvement exceeds switching cost and uncertainty"
        result["exposure_rule"] = "only ACTIVE or REDUCED strategies can receive live experimental weight; SHADOW receives no exposure"
        result["empty_group_allowed"] = True
        return result

    def register(self, definition: StrategyDefinition) -> None:
        self._registry[definition.strategy_id] = definition

    def definitions(self) -> List[StrategyDefinition]:
        return list(self._registry.values())

    def strategy_cards(self, lang: str = "zh") -> List[dict]:
        cards = []
        for item in self.definitions():
            cards.append(
                {
                    "strategy_id": item.strategy_id,
                    "version": item.version,
                    "name": getattr(item.name, lang, item.name.zh),
                    "summary": getattr(item.summary, lang, item.summary.zh),
                    "logic": getattr(item.logic, lang, item.logic.zh),
                    "best_conditions": getattr(item.best_conditions, lang, item.best_conditions.zh),
                    "main_risks": getattr(item.main_risks, lang, item.main_risks.zh),
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
        underperforming = state.expected_net_return <= 0 or (state.oos_marginal_value is not None and state.oos_marginal_value <= 0)

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
    def should_switch(
        current_expected_net_return: float,
        candidate_expected_net_return: float,
        switching_cost: float,
        uncertainty: float,
    ) -> bool:
        return candidate_expected_net_return - current_expected_net_return > switching_cost + uncertainty

    @staticmethod
    def _allocate_with_cap(states: List[StrategyState], cap: float) -> Dict[str, float]:
        positive = {s.strategy_id: max(0.0, s.expected_net_return) for s in states}
        active = {k for k, v in positive.items() if v > 0}
        weights: Dict[str, float] = {}
        remaining = 1.0

        while active and remaining > 1e-12:
            total = sum(positive[k] for k in active)
            if total <= 0:
                break
            tentative = {k: remaining * positive[k] / total for k in active}
            capped = [k for k, w in tentative.items() if w > cap]
            if not capped:
                weights.update(tentative)
                remaining = 0.0
                break
            for k in capped:
                weights[k] = cap
                remaining -= cap
                active.remove(k)

        return weights

    def select(self, market_id: str, states: Iterable[StrategyState], max_members: int) -> StrategyGroup:
        cfg = self.config_for(market_id)
        feasible = [
            s for s in states
            if s.eligible
            and s.lifecycle in {"active", "reduced"}
            and not s.hard_failure
            and s.liquidity_ok
            and s.capacity_ok
            and s.risk_ok
            and s.concentration_ok
            and s.expected_net_return > 0
        ]
        ranked = sorted(feasible, key=lambda s: s.expected_net_return, reverse=True)[:max_members]

        if not ranked:
            return StrategyGroup(
                group_version=self.version,
                config_version=cfg.config_version,
                market_id=market_id,
                members=[],
                weights={},
                reasons={},
            )

        weights = self._allocate_with_cap(ranked, cfg.max_weight)
        members = [s.strategy_id for s in ranked if weights.get(s.strategy_id, 0.0) > 0]
        reasons = {}
        for s in ranked:
            if s.strategy_id not in members:
                continue
            reasons[s.strategy_id] = s.selection_reason or BilingualText(
                zh=f"当前为 {s.lifecycle.upper()}，预期净回报 {s.expected_net_return:.4f}，通过风险、流动性、容量和集中度约束，因此进入当前策略群。",
                en=f"Currently {s.lifecycle.upper()} with expected net return {s.expected_net_return:.4f}; it passes risk, liquidity, capacity and concentration constraints and is included in the current group.",
            )

        return StrategyGroup(
            group_version=self.version,
            config_version=cfg.config_version,
            market_id=market_id,
            members=members,
            weights={k: weights[k] for k in members},
            reasons=reasons,
        )
