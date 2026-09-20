from __future__ import annotations

from typing import Dict

from .contracts import EvaluationResult, StrategyGroup, TriaidDecision


class EvaluationModule:
    version = "evaluation@0.1.0"

    @staticmethod
    def _weighted_return(weights: Dict[str, float], realized: Dict[str, float]) -> float:
        return sum(weight * realized.get(strategy_id, 0.0) for strategy_id, weight in weights.items())

    def pending(self) -> EvaluationResult:
        return EvaluationResult(status="PENDING_OUTCOME")

    def evaluate(
        self,
        group: StrategyGroup,
        decision: TriaidDecision,
        realized_returns: Dict[str, float],
        trading_cost: float,
    ) -> EvaluationResult:
        baseline = self._weighted_return(group.weights, realized_returns)
        triaid_gross = self._weighted_return(decision.weights_after, realized_returns)
        triaid_net = triaid_gross - trading_cost
        return EvaluationResult(
            status="EVALUATED",
            baseline_return=baseline,
            triaid_return=triaid_net,
            excess_return=triaid_net - baseline,
            trading_cost=trading_cost,
        )
