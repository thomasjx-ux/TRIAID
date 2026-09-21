from __future__ import annotations

import math

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
        cost=float(trading_cost)
        if not math.isfinite(cost) or cost<0:
            raise ValueError("trading_cost must be finite and nonnegative")
        required={
            sid
            for sid,w in {**group.weights,**decision.weights_after}.items()
            if sid!="P28_CASH" and float(w)>1e-12
        }
        missing=sorted(sid for sid in required if sid not in realized_returns)
        if missing:
            raise ValueError(f"missing realized returns for required strategies: {missing}")
        normalized={}
        for sid,value in realized_returns.items():
            x=float(value)
            if not math.isfinite(x):
                raise ValueError(f"nonfinite realized return for {sid}")
            normalized[str(sid)]=x
        baseline_c=self._contributions(group.weights,normalized)
        triaid_c=self._contributions(decision.weights_after,normalized)
        baseline=sum(baseline_c.values())
        triaid_gross=sum(triaid_c.values())
        triaid_net=triaid_gross-cost
        return EvaluationResult(
            status="EVALUATED",
            baseline_return=baseline,
            triaid_return=triaid_net,
            excess_return=triaid_net-baseline,
            trading_cost=cost,
            strategy_realized_returns=dict(normalized),
            baseline_contributions=baseline_c,
            triaid_contributions=triaid_c,
        )
