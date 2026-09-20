from __future__ import annotations

from datetime import date
from threading import RLock
from typing import Dict, List
from uuid import uuid4

from .audit import AuditModule
from .contracts import OutcomeRequest, RunRecord, RunRequest
from .core import TriaidCoreModule
from .evaluation import EvaluationModule
from .strategy_population import StrategyPopulationModule


class EvolutionLabEngine:
    architecture_version = "fin-evolution-lab@0.1.0"

    def __init__(self) -> None:
        self.strategy_population = StrategyPopulationModule()
        self.core = TriaidCoreModule()
        self.evaluation = EvaluationModule()
        self.audit = AuditModule()
        self._runs: Dict[str, RunRecord] = {}
        self._lock = RLock()

    @property
    def module_manifest(self) -> Dict[str, str]:
        return {
            "architecture": self.architecture_version,
            "strategy_population": self.strategy_population.version,
            "triaid_core": self.core.version,
            "evaluation": self.evaluation.version,
            "audit": self.audit.version,
        }

    def create_run(self, request: RunRequest) -> RunRecord:
        run_id = f"{request.market.market_id}-{uuid4().hex[:12]}"
        run = RunRecord(
            run_id=run_id,
            module_manifest=self.module_manifest,
            market=request.market,
        )
        with self._lock:
            self._runs[run_id] = run
        return run

    def execute(self, run_id: str, request: RunRequest) -> None:
        try:
            group = self.strategy_population.select(request.strategy_states, request.max_group_size)
            decision = self.core.decide(request.market, group)
            with self._lock:
                run = self._runs[run_id]
                run.strategy_group = group
                run.triaid_decision = decision
                run.evaluation = self.evaluation.pending()
                run.audit = self.audit.audit(run)
                run.status = "DECISION_READY_AWAITING_OUTCOME" if run.audit.passed else "FAILED"
        except Exception:
            with self._lock:
                self._runs[run_id].status = "FAILED"
            raise

    def submit_outcome(self, run_id: str, outcome: OutcomeRequest) -> RunRecord:
        with self._lock:
            run = self._runs[run_id]
            if run.strategy_group is None or run.triaid_decision is None:
                raise ValueError("Decision is not ready.")
            run.evaluation = self.evaluation.evaluate(
                run.strategy_group,
                run.triaid_decision,
                outcome.realized_returns,
                outcome.trading_cost,
            )
            run.audit = self.audit.audit(run)
            run.status = "VERIFIED" if run.audit.passed else "FAILED"
            return run

    def get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            return self._runs[run_id]

    def status(self) -> dict:
        with self._lock:
            counts: Dict[str, int] = {}
            for run in self._runs.values():
                counts[run.status] = counts.get(run.status, 0) + 1
        return {
            "architecture_version": self.architecture_version,
            "module_manifest": self.module_manifest,
            "storage_mode": "ephemeral_scaffold",
            "run_counts": counts,
        }

    def daily_summary(self) -> dict:
        today = date.today().isoformat()
        with self._lock:
            rows = [r for r in self._runs.values() if r.created_at.startswith(today)]
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
                    "status": r.status,
                    "members": r.strategy_group.members if r.strategy_group else [],
                    "evaluation": r.evaluation.model_dump() if r.evaluation else None,
                }
                for r in rows
            ],
        }

    def curves(self) -> List[dict]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda r: r.created_at)
        baseline_equity = 1.0
        triaid_equity = 1.0
        points = []
        for run in runs:
            if not run.evaluation or run.evaluation.status != "EVALUATED":
                continue
            baseline_equity *= 1.0 + (run.evaluation.baseline_return or 0.0)
            triaid_equity *= 1.0 + (run.evaluation.triaid_return or 0.0)
            points.append(
                {
                    "time": run.created_at,
                    "run_id": run.run_id,
                    "baseline_equity": baseline_equity,
                    "triaid_equity": triaid_equity,
                    "excess_equity": triaid_equity - baseline_equity,
                }
            )
        return points
