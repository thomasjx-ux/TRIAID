from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from .contracts import RunRecord


class ReviewModule:
    version = "review@0.1.0"

    def daily_summary(self, runs: Iterable[RunRecord]) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        rows = [r for r in runs if r.created_at.startswith(today)]
        evaluated = [r for r in rows if r.evaluation and r.evaluation.status == "EVALUATED"]
        excess = sum((r.evaluation.excess_return or 0.0) for r in evaluated)

        return {
            "date": today,
            "runs": len(rows),
            "evaluated_runs": len(evaluated),
            "cumulative_excess_return": excess,
            "runs_detail": [
                {
                    "run_id": r.run_id,
                    "market_id": r.market.market_id,
                    "snapshot_id": r.market.snapshot_id,
                    "status": r.status,
                    "module_manifest": r.module_manifest,
                    "strategy_group": {
                        "members": r.strategy_group.members,
                        "weights": r.strategy_group.weights,
                        "reasons": {k: v.model_dump() for k, v in r.strategy_group.reasons.items()},
                    } if r.strategy_group else None,
                    "triaid_decision": {
                        "weights_before": r.triaid_decision.weights_before,
                        "weights_after": r.triaid_decision.weights_after,
                        "reasons": {k: v.model_dump() for k, v in r.triaid_decision.reasons.items()},
                    } if r.triaid_decision else None,
                    "evaluation": r.evaluation.model_dump() if r.evaluation else None,
                    "audit": r.audit.model_dump() if r.audit else None,
                }
                for r in rows
            ],
        }

    def curves(self, runs: Iterable[RunRecord]) -> List[dict]:
        ordered = sorted(runs, key=lambda r: r.created_at)
        baseline_equity = 1.0
        triaid_equity = 1.0
        cumulative_excess = 0.0
        points = []

        for run in ordered:
            if not run.evaluation or run.evaluation.status != "EVALUATED":
                continue
            baseline_return = run.evaluation.baseline_return or 0.0
            triaid_return = run.evaluation.triaid_return or 0.0
            excess = run.evaluation.excess_return or 0.0
            baseline_equity *= 1.0 + baseline_return
            triaid_equity *= 1.0 + triaid_return
            cumulative_excess += excess
            points.append(
                {
                    "time": run.created_at,
                    "run_id": run.run_id,
                    "market_id": run.market.market_id,
                    "baseline_equity": baseline_equity,
                    "triaid_equity": triaid_equity,
                    "cumulative_excess_return": cumulative_excess,
                    "excess_equity_gap": triaid_equity - baseline_equity,
                }
            )
        return points
