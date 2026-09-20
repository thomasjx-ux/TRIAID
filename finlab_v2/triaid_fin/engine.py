from __future__ import annotations

from threading import RLock
from typing import Dict, List
from uuid import uuid4

from .audit import AuditModule
from .contracts import OutcomeRequest, RunRecord, RunRequest
from .core import TriaidCoreModule
from .evaluation import EvaluationModule
from .review import ReviewModule
from .strategy_population import StrategyPopulationModule


class EvolutionLabEngine:
    architecture_version = "fin-evolution-lab@0.2.0"

    def __init__(self) -> None:
        self.strategy_population = StrategyPopulationModule()
        self.core = TriaidCoreModule()
        self.evaluation = EvaluationModule()
        self.audit = AuditModule()
        self.review = ReviewModule()
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
            "review": self.review.version,
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
            group = self.strategy_population.select(
                request.market.market_id,
                request.strategy_states,
                request.max_group_size,
            )
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

    def _runs_snapshot(self) -> List[RunRecord]:
        with self._lock:
            return list(self._runs.values())

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
        return self.review.daily_summary(self._runs_snapshot())

    def curves(self) -> List[dict]:
        return self.review.curves(self._runs_snapshot())
