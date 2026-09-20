from __future__ import annotations

from typing import Dict

from .contracts import EvaluationResult, StrategyGroup, TriaidDecision


class EvaluationModule:
    version = "evaluation@0.2.0"

    @staticmethod
    def _contributions(weights: Dict[str, float], realized: Dict[str, float]) -> Dict[str, float]:
        return {strategy_id:weight*realized.get(strategy_id,0.0) for strategy_id,weight in weights.items()}

    def pending(self) -> EvaluationResult:
        return EvaluationResult(status="PENDING_OUTCOME")

    def evaluate(
        self,
        group: StrategyGroup,
        decision: TriaidDecision,
        realized_returns: Dict[str, float],
        trading_cost: float,
    ) -> EvaluationResult:
        baseline_c=self._contributions(group.weights,realized_returns)
        triaid_c=self._contributions(decision.weights_after,realized_returns)
        baseline=sum(baseline_c.values())
        triaid_gross=sum(triaid_c.values())
        triaid_net=triaid_gross-trading_cost
        return EvaluationResult(
            status="EVALUATED",
            baseline_return=baseline,
            triaid_return=triaid_net,
            excess_return=triaid_net-baseline,
            trading_cost=trading_cost,
            strategy_realized_returns=dict(realized_returns),
            baseline_contributions=baseline_c,
            triaid_contributions=triaid_c,
        )
