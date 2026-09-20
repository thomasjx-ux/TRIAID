from __future__ import annotations

from typing import Dict, Iterable, List

from .contracts import BilingualText, StrategyDefinition, StrategyGroup, StrategyState


class StrategyPopulationModule:
    """Reference module.

    The module boundary is stable; the selector and lifecycle rules are intentionally replaceable.
    This first implementation is a transparent baseline, not the final production selector.
    """

    version = "strategy-population@0.1.0"

    def __init__(self) -> None:
        self._registry: Dict[str, StrategyDefinition] = {}

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

    def select(self, states: Iterable[StrategyState], max_members: int) -> StrategyGroup:
        feasible = [
            s for s in states
            if s.eligible
            and s.lifecycle not in {"frozen", "retired"}
            and s.capacity_ok
            and s.risk_ok
        ]
        ranked = sorted(feasible, key=lambda s: s.expected_net_return, reverse=True)[:max_members]

        if not ranked:
            return StrategyGroup(
                group_version=self.version,
                members=[],
                weights={},
                reasons={},
            )

        positive = [max(0.0, s.expected_net_return) for s in ranked]
        total = sum(positive)
        if total <= 0:
            raw_weights = [1.0 / len(ranked)] * len(ranked)
        else:
            raw_weights = [x / total for x in positive]

        weights = {s.strategy_id: w for s, w in zip(ranked, raw_weights)}
        reasons = {}
        for s in ranked:
            reasons[s.strategy_id] = s.selection_reason or BilingualText(
                zh=f"在当前可行候选中，预期净回报为 {s.expected_net_return:.4f}，满足风险与容量约束。",
                en=f"Among currently feasible candidates, expected net return is {s.expected_net_return:.4f} and risk/capacity constraints are satisfied.",
            )

        return StrategyGroup(
            group_version=self.version,
            members=[s.strategy_id for s in ranked],
            weights=weights,
            reasons=reasons,
        )
