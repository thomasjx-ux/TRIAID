from __future__ import annotations

from threading import RLock
from typing import Dict, List
from uuid import uuid4

from .audit import AuditModule
from .contracts import MarketSnapshot, OutcomeRequest, RunRecord, RunRequest
from .core import TriaidCoreModule
from .evaluation import EvaluationModule
from .evolution import EvolutionModule
from .market_lab import prepare_live_market
from .population_state import PopulationStateTracker
from .review import ReviewModule
from .store import RunStore
from .strategy_population import StrategyPopulationModule


class EvolutionLabEngine:
    architecture_version = "fin-evolution-lab@0.4.1"
    market_adapter_version = "market-lab@0.1.0"

    def __init__(self) -> None:
        self.store=RunStore()
        self.evolution=EvolutionModule(self.store)
        self.strategy_population=StrategyPopulationModule()
        self.population_state=PopulationStateTracker(self.store,self.strategy_population)
        self.core=TriaidCoreModule(self.evolution.active())
        self.evaluation=EvaluationModule()
        self.audit=AuditModule()
        self.review=ReviewModule()
        self._runs:Dict[str,RunRecord]={r.run_id:r for r in self.store.list_runs()}
        self._lock=RLock()

    def refresh_core(self)->None:
        self.core=TriaidCoreModule(self.evolution.active())

    @property
    def module_manifest(self)->Dict[str,str]:
        return {
            "architecture":self.architecture_version,
            "market_data":self.market_adapter_version,
            "strategy_population":self.strategy_population.version,
            "population_state":self.population_state.version,
            "triaid_core":self.core.version,
            "evaluation":self.evaluation.version,
            "audit":self.audit.version,
            "review":self.review.version,
            "store":self.store.version,
            "evolution":self.evolution.version,
        }

    def create_run(self,request:RunRequest,run_id:str|None=None)->RunRecord:
        run_id=run_id or f"{request.market.market_id}-{uuid4().hex[:12]}"
        run=RunRecord(
            run_id=run_id,
            module_manifest=self.module_manifest,
            market=request.market,
            strategy_states=list(request.strategy_states),
        )
        with self._lock:
            self._runs[run_id]=run
            self.store.save_run(run)
        return run

    def create_pending_live_run(self,market_id:str)->RunRecord:
        market_id=market_id.upper()
        run_id=f"{market_id}-live-{uuid4().hex[:12]}"
        run=RunRecord(
            run_id=run_id,
            module_manifest=self.module_manifest,
            market=MarketSnapshot(market_id=market_id,as_of="",snapshot_id="PENDING"),
            status="FETCHING_DATA",
        )
        with self._lock:
            self._runs[run_id]=run
            self.store.save_run(run)
        return run

    def execute(self,run_id:str,request:RunRequest)->None:
        try:
            group=self.strategy_population.select(
                request.market.market_id,
                request.strategy_states,
                request.max_group_size,
            )
            decision=self.core.decide(request.market,group,request.strategy_states)
            with self._lock:
                run=self._runs[run_id]
                run.market=request.market
                run.strategy_states=list(request.strategy_states)
                run.module_manifest=self.module_manifest
                run.strategy_group=group
                run.triaid_decision=decision
                run.evaluation=self.evaluation.pending()
                run.audit=self.audit.audit(run)
                run.status="DECISION_READY_AWAITING_OUTCOME" if run.audit.passed else "FAILED"
                self.store.save_run(run)
        except Exception as exc:
            with self._lock:
                run=self._runs[run_id]
                run.status="FAILED"
                run.diagnostic_summary={"error":f"{type(exc).__name__}:{exc}"}
                self.store.save_run(run)
            raise

    def _meta_rebalance_cost(self,run:RunRecord)->float:
        if not run.triaid_decision:
            return 0.0
        before=run.triaid_decision.weights_before
        after=run.triaid_decision.weights_after
        turnover=sum(abs(after.get(k,0.0)-before.get(k,0.0)) for k in set(before)|set(after))
        bps=float(run.market.metadata.get("base_cost_bps",2.0) or 2.0)
        return turnover*bps/10000.0

    def _resolve_previous_period(
        self,
        market_id:str,
        previous_as_of:str,
        realized_returns:dict[str,float],
    )->list[str]:
        resolved=[]
        candidates=[
            r for r in self.all_runs()
            if r.market.market_id.upper()==market_id.upper()
            and r.status=="DECISION_READY_AWAITING_OUTCOME"
            and r.market.as_of==previous_as_of
        ]
        for run in candidates:
            self.submit_outcome(
                run.run_id,
                OutcomeRequest(
                    realized_returns=realized_returns,
                    trading_cost=self._meta_rebalance_cost(run),
                ),
            )
            resolved.append(run.run_id)
        return resolved

    def execute_live(self,run_id:str,market_id:str)->None:
        market_id=market_id.upper()
        try:
            prepared=prepare_live_market(market_id)
            snapshot=prepared["snapshot"]

            existing=[
                r for r in self.all_runs()
                if r.run_id!=run_id
                and r.market.market_id.upper()==market_id
                and r.market.snapshot_id==snapshot.snapshot_id
                and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED","NO_NEW_DATA"}
            ]
            if existing:
                with self._lock:
                    run=self._runs[run_id]
                    run.market=snapshot
                    run.status="NO_NEW_DATA"
                    run.previous_run_id=existing[-1].run_id
                    run.diagnostic_summary={"message":"Market source timestamp unchanged; existing run remains current."}
                    self.store.save_run(run)
                return

            resolved=self._resolve_previous_period(
                market_id,
                prepared["previous_as_of"],
                prepared["realized_returns_from_previous_period"],
            )
            states=self.population_state.apply(market_id,prepared["strategy_states"])
            request=RunRequest(
                market=snapshot,
                strategy_states=states,
                max_group_size=12,
            )
            with self._lock:
                run=self._runs[run_id]
                run.previous_run_id=resolved[-1] if resolved else None
                self.store.save_run(run)
            self.execute(run_id,request)
        except Exception as exc:
            with self._lock:
                run=self._runs[run_id]
                run.status="FAILED"
                run.diagnostic_summary={"error":f"{type(exc).__name__}:{exc}"}
                self.store.save_run(run)

    def submit_outcome(self,run_id:str,outcome:OutcomeRequest)->RunRecord:
        with self._lock:
            run=self._runs.get(run_id) or self.store.load_run(run_id)
            if run.strategy_group is None or run.triaid_decision is None:
                raise ValueError("Decision is not ready.")
            run.evaluation=self.evaluation.evaluate(
                run.strategy_group,
                run.triaid_decision,
                outcome.realized_returns,
                outcome.trading_cost,
            )
            run.audit=self.audit.audit(run)
            run.status="VERIFIED" if run.audit.passed else "FAILED"
            base=run.evaluation.baseline_contributions
            tri=run.evaluation.triaid_contributions
            deltas={k:tri.get(k,0.0)-base.get(k,0.0) for k in set(base)|set(tri)}
            run.diagnostic_summary={
                "positive_interventions":sum(1 for x in deltas.values() if x>0),
                "negative_interventions":sum(1 for x in deltas.values() if x<0),
                "largest_positive":max(deltas.items(),key=lambda x:x[1]) if deltas else None,
                "largest_negative":min(deltas.items(),key=lambda x:x[1]) if deltas else None,
                "contribution_deltas":deltas,
            }
            self._runs[run_id]=run
            self.store.save_run(run)
            return run

    def get_run(self,run_id:str)->RunRecord:
        with self._lock:
            if run_id in self._runs:
                return self._runs[run_id]
        return self.store.load_run(run_id)

    def all_runs(self)->List[RunRecord]:
        with self._lock:
            return sorted(self._runs.values(),key=lambda r:r.created_at)

    def latest_run(self,market_id:str|None=None)->RunRecord|None:
        rows=self.all_runs()
        if market_id:
            rows=[r for r in rows if r.market.market_id.upper()==market_id.upper()]
        useful=[r for r in rows if r.market.snapshot_id!="PENDING"]
        return useful[-1] if useful else (rows[-1] if rows else None)

    def status(self)->dict:
        counts:Dict[str,int]={}
        for run in self.all_runs():
            counts[run.status]=counts.get(run.status,0)+1
        return {
            "architecture_version":self.architecture_version,
            "module_manifest":self.module_manifest,
            "storage":self.store.status(),
            "active_core":self.evolution.active().__dict__,
            "strategy_registry_count":len(self.strategy_population.definitions()),
            "markets":["US","CN"],
            "run_counts":counts,
        }

    def daily_summary(self,market_id:str|None=None)->dict:
        rows=self.all_runs()
        if market_id:
            rows=[r for r in rows if r.market.market_id.upper()==market_id.upper()]
        return self.review.daily_summary(rows)

    def curves(self,market_id:str|None=None)->List[dict]:
        rows=self.all_runs()
        if market_id:
            rows=[r for r in rows if r.market.market_id.upper()==market_id.upper()]
        return self.review.curves(rows)

    def evolution_status(self)->dict:
        data=self.evolution.status()
        data["diagnosis"]=self.evolution.diagnose(self.all_runs())
        return data

    def propose_core_candidate(self)->dict:
        return self.evolution.propose_candidate(self.all_runs())

    def promote_core(self,version:str,validation:dict)->dict:
        result=self.evolution.promote(version,validation)
        if result.get("promoted"):
            self.refresh_core()
        return result
