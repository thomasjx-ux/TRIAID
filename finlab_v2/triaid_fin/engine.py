from __future__ import annotations

from datetime import datetime
from statistics import mean
from threading import RLock
from typing import Dict, List
from uuid import uuid4
from zoneinfo import ZoneInfo

from .audit import AuditModule
from .contracts import MarketSnapshot, OutcomeRequest, RunRecord, RunRequest
from .core import TriaidCoreModule
from .evaluation import EvaluationModule
from .evolution import EvolutionModule
from .market_lab import market_data_capabilities, market_data_instrument_series, market_data_latest_quotes, market_data_product_capabilities, market_data_provider_status, market_data_snapshot, market_data_status, prepare_live_market, refresh_market_data, strategy_market_context
from .population_state import PopulationStateTracker
from .prospective_experiment import ProspectiveExperimentProtocol
from .recovery_core import RecoveryWaveCore
from .recovery_ledger import RecoveryWaveLedger
from .observation import MarketObservationStore
from .review import ReviewModule
from .store import RunStore
from .strategy_evolution import StrategyEvolutionModule
from .strategy_population import StrategyPopulationModule
from .trading_calendar import VERSION as TRADING_CALENDAR_VERSION
from .trading_calendar_sync import VERSION as TRADING_CALENDAR_SYNC_VERSION
from .us_return_max import USReturnMaxLedger, USReturnMaxRoute


class EvolutionLabEngine:
    architecture_version = "fin-evolution-lab@0.11.0"
    market_adapter_version = "market-lab@0.3.0"

    def __init__(self) -> None:
        self.store=RunStore()
        self.observations=MarketObservationStore(self.store)
        self.evolution=EvolutionModule(self.store)
        self.strategy_evolution=StrategyEvolutionModule(self.store)
        self.strategy_population=StrategyPopulationModule()
        self._apply_strategy_profiles()
        self.population_state=PopulationStateTracker(self.store,self.strategy_population)
        self.core=TriaidCoreModule(self.evolution.active())
        self.evaluation=EvaluationModule()
        self.audit=AuditModule()
        self.review=ReviewModule()
        self.prospective_experiment=ProspectiveExperimentProtocol(self.store)
        self.recovery_wave_ledger=RecoveryWaveLedger(self.store)
        self.recovery_wave_core=RecoveryWaveCore(self.evolution.active())
        self.us_return_max=USReturnMaxRoute()
        self.us_return_max_ledger=USReturnMaxLedger(self.store)
        self._runs:Dict[str,RunRecord]={r.run_id:r for r in self.store.list_runs()}
        self._lock=RLock()
        self._live_lock=RLock()
        self._recover_stale_runs()

    def _recover_stale_runs(self)->None:
        for run in list(self._runs.values()):
            if run.status not in {"CREATED","FETCHING_DATA"}:
                continue
            run.status="FAILED"
            run.diagnostic_summary={
                **dict(run.diagnostic_summary or {}),
                "error":"STALE_INCOMPLETE_RUN_RECOVERED_AFTER_PROCESS_RESTART",
                "recovery":"Previous process ended before this research run completed.",
            }
            self.store.save_run(run)

    def _apply_strategy_profiles(self)->None:
        for market_id in ("US","CN"):
            self.strategy_population.configure_market(self.strategy_evolution.active(market_id))

    def refresh_core(self)->None:
        params=self.evolution.active()
        self.core=TriaidCoreModule(params)
        self.recovery_wave_core=RecoveryWaveCore(params)

    @property
    def module_manifest(self)->Dict[str,str]:
        return {
            "architecture":self.architecture_version,
            "market_data":self.market_adapter_version,
            "market_data_hub":market_data_status().get("version","market-data-hub@unknown"),
            "official_trading_calendar":TRADING_CALENDAR_VERSION,
            "official_trading_calendar_sync":TRADING_CALENDAR_SYNC_VERSION,
            "market_observation":self.observations.version if hasattr(self,"observations") else "market-observation@0.1.0",
            "strategy_population":self.strategy_population.version,
            "population_state":self.population_state.version if hasattr(self,"population_state") else "population-state@0.1.0",
            "strategy_evolution":self.strategy_evolution.version,
            "strategy_rules_US":self.strategy_evolution.active("US").version,
            "strategy_rules_CN":self.strategy_evolution.active("CN").version,
            "triaid_core":self.core.version if hasattr(self,"core") else self.evolution.active().version,
            "evaluation":self.evaluation.version if hasattr(self,"evaluation") else "evaluation@0.2.0",
            "audit":self.audit.version if hasattr(self,"audit") else "audit@0.2.0",
            "review":self.review.version if hasattr(self,"review") else "review@0.2.0",
            "prospective_experiment":self.prospective_experiment.version if hasattr(self,"prospective_experiment") else "cn-prospective-controls@unknown",
            "recovery_wave_core":self.recovery_wave_core.version if hasattr(self,"recovery_wave_core") else "recovery-wave-core@unknown",
            "recovery_wave_ledger":self.recovery_wave_ledger.version if hasattr(self,"recovery_wave_ledger") else "recovery-wave-ledger@unknown",
            "capital_capacity":self.recovery_wave_core.capital_capacity.version if hasattr(self,"recovery_wave_core") else "capital-capacity-layer@unknown",
            "us_return_max":self.us_return_max.version if hasattr(self,"us_return_max") else "us-return-max-route@unknown",
            "us_return_max_ledger":self.us_return_max_ledger.version if hasattr(self,"us_return_max_ledger") else "us-return-max-ledger@unknown",
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

    @staticmethod
    def _complete_daily_evidence_run(run:RunRecord)->bool:
        # Legacy/manual runs without this flag remain eligible. Live runs explicitly
        # marked False are provisional intraday research and must not advance daily
        # lifecycle, prospective experiments, or posterior evaluation.
        return (run.market.metadata or {}).get("daily_bar_complete") is not False

    def _previous_us_route_decision(self,market_as_of:str)->dict|None:
        rows=[
            row for row in self.us_return_max_ledger.decisions(2000)
            if str(row.get("decision_status") or "")=="DAILY_FROZEN"
            and str(row.get("market_as_of") or "")<str(market_as_of)
        ]
        return rows[-1] if rows else None

    def _previous_group_for(self,market_id:str,exclude_run_id:str|None=None):
        rows=[
            r for r in self.all_runs()
            if r.run_id!=exclude_run_id
            and r.market.market_id.upper()==market_id.upper()
            and r.strategy_group is not None
            and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
            and self._complete_daily_evidence_run(r)
        ]
        return rows[-1].strategy_group if rows else None

    def execute(self,run_id:str,request:RunRequest)->None:
        try:
            previous_group=self._previous_group_for(request.market.market_id,run_id)
            current_mode=str(request.market.metadata.get("experiment_mode") or "")
            previous_state_rows=[
                r for r in self.all_runs()
                if r.run_id!=run_id
                and r.market.market_id.upper()==request.market.market_id.upper()
                and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
                and r.strategy_states
                and self._complete_daily_evidence_run(r)
                and (
                    not current_mode
                    or str(r.market.metadata.get("experiment_mode") or "")==current_mode
                )
            ]
            previous_states=previous_state_rows[-1].strategy_states if previous_state_rows else []
            group=self.strategy_population.select(
                request.market.market_id,
                request.strategy_states,
                request.max_group_size,
                previous_group=previous_group,
                base_cost_bps=float(request.market.metadata.get("base_cost_bps",2.0) or 2.0),
                experiment_mode=request.market.metadata.get("experiment_mode"),
            )
            decision=self.core.decide(request.market,group,request.strategy_states)
            state_map={s.strategy_id:s for s in request.strategy_states}
            projected_baseline=sum(
                float(w)*(0.0 if sid=="P28_CASH" else float(state_map[sid].expected_net_return))
                for sid,w in group.weights.items()
                if sid=="P28_CASH" or sid in state_map
            )
            projected_after=sum(
                float(w)*(0.0 if sid=="P28_CASH" else float(state_map[sid].expected_net_return))
                for sid,w in decision.weights_after.items()
                if sid=="P28_CASH" or sid in state_map
            )
            prospective=None
            if (
                request.market.market_id.upper()=="CN"
                and str(request.market.metadata.get("experiment_mode") or "").upper()=="CN_WORST_POOL_RESCUE"
                and bool(group.diagnostics.get("experiment_available",True))
                and request.market.metadata.get("daily_bar_complete") is not False
            ):
                profile=self.strategy_evolution.active("CN")
                prospective=self.prospective_experiment.register(
                    run_id=run_id,
                    market=request.market,
                    group=group,
                    states=request.strategy_states,
                    decision=decision,
                    horizons=(
                        int(profile.exit_confirm_days),
                        int(profile.entry_confirm_days),
                        int(profile.cooldown_days),
                    ),
                    previous_states=previous_states,
                )
            with self._lock:
                run=self._runs[run_id]
                run.market=request.market
                run.strategy_states=list(request.strategy_states)
                run.module_manifest=self.module_manifest
                run.strategy_group=group
                run.triaid_decision=decision
                run.evaluation=self.evaluation.pending()
                run.diagnostic_summary={
                    "experiment_mode":request.market.metadata.get("experiment_mode"),
                    "projection_basis":"MULTI_WINDOW_ANNUALIZED_HISTORICAL_STATE_RETURN_ESTIMATE_NOT_CALIBRATED_FORECAST",
                    "projected_baseline_expected_return":projected_baseline,
                    "projected_triaid_expected_return":projected_after,
                    "projected_excess_expected_return":projected_after-projected_baseline,
                    "realized_outcome_pending":True,
                    "prospective_experiment_id":prospective.get("experiment_id") if prospective else None,
                    "prospective_protocol_version":prospective.get("protocol_version") if prospective else None,
                    "prospective_horizons_trading_days":(
                        prospective.get("design",{}).get("horizons_trading_days") if prospective else None
                    ),
                    "prospective_primary_evidence":"FUTURE_RANKING_ACCURACY_NOT_CASH_REDUCTION" if prospective else None,
                }
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
            and self._complete_daily_evidence_run(r)
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
        # Live runs mutate shared lifecycle/evidence ledgers. Serialize them so
        # API-triggered runs and scheduler/automation runs cannot interleave
        # read-modify-write state in a single replica.
        with self._live_lock:
            return self._execute_live_locked(run_id,market_id)

    def _execute_live_locked(self,run_id:str,market_id:str)->None:
        market_id=market_id.upper()
        try:
            profile=self.strategy_evolution.active(market_id)
            prepared=prepare_live_market(market_id,profile.window_weights)
            snapshot=prepared["snapshot"]
            snapshot.metadata["strategy_rules_version"]=profile.version

            phase=str(snapshot.metadata.get("session_phase") or "").upper()
            daily_bar_complete=bool(snapshot.metadata.get("daily_bar_complete"))
            snapshot.metadata["daily_bar_complete"]=daily_bar_complete
            snapshot.metadata["evidence_state"]="COMPLETE_DAILY" if daily_bar_complete else "PROVISIONAL_INTRADAY"
            recovery_outcome=None
            recovery_decision=None
            us_return_outcome=None
            if market_id=="CN":
                if daily_bar_complete:
                    recovery_outcome=self.recovery_wave_ledger.record_outcome(
                        market_id,
                        prepared["latest_as_of"],
                        prepared["previous_as_of"],
                        prepared.get("product_realized_returns_from_previous_period") or {},
                        snapshot.snapshot_id,
                        prepared.get("product_turnover_notional_from_previous_period") or {},
                    )
                existing_recovery=self.recovery_wave_ledger.by_snapshot(market_id,snapshot.snapshot_id,self.recovery_wave_core.version)
                if existing_recovery is None:
                    if daily_bar_complete:
                        prior_frozen=[
                            row for row in self.recovery_wave_ledger.decisions(market_id,1000)
                            if str(row.get("decision_status") or "")=="DAILY_FROZEN"
                            and str(row.get("market_as_of") or "")<str(snapshot.as_of)
                        ]
                        previous_recovery=prior_frozen[-1] if prior_frozen else None
                    else:
                        previous_recovery=self.recovery_wave_ledger.latest(market_id)
                    proposed_recovery=self.recovery_wave_core.decide(
                        prepared["panel"],
                        snapshot.regime,
                        previous_recovery,
                        phase,
                    )
                    recovery_decision=self.recovery_wave_ledger.freeze(
                        proposed_recovery,
                        snapshot.snapshot_id,
                        snapshot.as_of,
                    )
                else:
                    recovery_decision=existing_recovery
                snapshot.metadata["recovery_wave_decision_id"]=recovery_decision.get("decision_id")
                snapshot.metadata["recovery_wave_decision_hash"]=recovery_decision.get("decision_hash")
                snapshot.metadata["recovery_wave_daily_bar_complete"]=daily_bar_complete
                snapshot.metadata["experiment_mode"]="CN_WORST_POOL_RESCUE"
                snapshot.metadata["experiment_design"]="Freeze the adverse risky pool using only information available at the decision time, preregister established control rankings, and test future recovery ordering over the existing 3/5/10-day CN decision horizons. Cash defense is reported separately from recovery-selection evidence."
                snapshot.metadata["market_route"]="CN_RECOVERY_CAPACITY"
            else:
                if daily_bar_complete:
                    us_return_outcome=self.us_return_max_ledger.record_outcome(
                        prepared["latest_as_of"],
                        prepared["previous_as_of"],
                        prepared.get("realized_returns_from_previous_period") or {},
                        prepared.get("product_realized_returns_from_previous_period") or {},
                        prepared.get("product_turnover_notional_from_previous_period") or {},
                        snapshot.snapshot_id,
                    )
                snapshot.metadata["experiment_mode"]="US_RETURN_MAX_CAPACITY"
                snapshot.metadata["experiment_design"]="Use the existing return-first reselect strategy population as the primary US route, expand the frozen strategy mix to executable ETF exposures, and validate realized return versus SPY buy-and-hold and the generic TRIAID Core under four USD capital sleeves."
                snapshot.metadata["market_route"]="US_RETURN_MAXIMIZATION"
            snapshot.metadata["strategy_window_weights"]=list(profile.window_weights)

            current_experiment=snapshot.metadata.get("experiment_mode")
            existing_decisions=[
                r for r in self.all_runs()
                if r.run_id!=run_id
                and r.market.market_id.upper()==market_id
                and r.market.snapshot_id==snapshot.snapshot_id
                and r.market.metadata.get("experiment_mode")==current_experiment
                and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
                and r.strategy_group is not None
                and r.triaid_decision is not None
            ]
            if existing_decisions:
                existing=existing_decisions[-1]
                prospective_bootstrap=None
                if market_id=="CN" and self._complete_daily_evidence_run(existing):
                    prior_rows=[
                        r for r in self.all_runs()
                        if r.run_id!=existing.run_id
                        and r.market.market_id.upper()=="CN"
                        and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
                        and r.strategy_states
                        and str(r.market.metadata.get("experiment_mode") or "")==str(current_experiment or "")
                        and r.created_at<existing.created_at
                    ]
                    previous_states=prior_rows[-1].strategy_states if prior_rows else []
                    prospective_bootstrap=self.prospective_experiment.register(
                        run_id=existing.run_id,
                        market=existing.market,
                        group=existing.strategy_group,
                        states=existing.strategy_states,
                        decision=existing.triaid_decision,
                        horizons=(
                            int(profile.exit_confirm_days),
                            int(profile.entry_confirm_days),
                            int(profile.cooldown_days),
                        ),
                        previous_states=previous_states,
                    )
                us_route_bootstrap=None
                if market_id=="US":
                    us_route_bootstrap=self.us_return_max_ledger.by_snapshot(
                        snapshot.snapshot_id,
                        self.us_return_max.version,
                    )
                    if us_route_bootstrap is None:
                        proposed_us_route=self.us_return_max.decide(
                            prepared["panel"],
                            existing.strategy_group,
                            existing.triaid_decision,
                            existing.strategy_states,
                            phase,
                            self._previous_us_route_decision(snapshot.as_of),
                        )
                        us_route_bootstrap=self.us_return_max_ledger.freeze(
                            proposed_us_route,
                            snapshot.snapshot_id,
                            snapshot.as_of,
                        )
                    snapshot.metadata["us_return_max_decision_id"]=us_route_bootstrap.get("decision_id")
                    snapshot.metadata["us_return_max_decision_hash"]=us_route_bootstrap.get("decision_hash")
                with self._lock:
                    run=self._runs[run_id]
                    run.market=snapshot
                    run.status="NO_NEW_DATA"
                    run.previous_run_id=existing.run_id
                    run.diagnostic_summary={
                        "message":"Market source timestamp unchanged; existing complete decision remains current.",
                        "dedupe_basis":"COMPLETE_DECISION_FOR_SNAPSHOT",
                        "prospective_bootstrap_experiment_id":(
                            prospective_bootstrap.get("experiment_id") if prospective_bootstrap else None
                        ),
                        "prospective_bootstrap_source_run_id":(
                            existing.run_id if prospective_bootstrap else None
                        ),
                        "recovery_wave_decision_id":recovery_decision.get("decision_id") if recovery_decision else None,
                        "recovery_wave_decision_hash":recovery_decision.get("decision_hash") if recovery_decision else None,
                        "recovery_wave_decision_status":recovery_decision.get("decision_status") if recovery_decision else None,
                        "recovery_wave_daily_bar_complete":daily_bar_complete if market_id=="CN" else None,
                        "recovery_wave_outcome_recorded":bool((recovery_outcome or {}).get("recorded")),
                        "us_return_max_decision_id":us_route_bootstrap.get("decision_id") if us_route_bootstrap else None,
                        "us_return_max_decision_hash":us_route_bootstrap.get("decision_hash") if us_route_bootstrap else None,
                        "us_return_max_outcome_recorded":bool((us_return_outcome or {}).get("recorded")),
                    }
                    self.store.save_run(run)
                return

            resolved=[]
            prospective_observation=None
            if daily_bar_complete:
                resolved=self._resolve_previous_period(
                    market_id,
                    prepared["previous_as_of"],
                    prepared["realized_returns_from_previous_period"],
                )
                if market_id=="CN":
                    prospective_observation=self.prospective_experiment.observe_period(
                        prepared["latest_as_of"],
                        prepared["realized_returns_from_previous_period"],
                    )
            states=self.population_state.apply(
                market_id,
                prepared["strategy_states"],
                observation_key=f"DAILY:{snapshot.as_of}",
                advance_observation=daily_bar_complete,
            )
            request=RunRequest(
                market=snapshot,
                strategy_states=states,
                max_group_size=profile.max_group_size,
            )
            with self._lock:
                run=self._runs[run_id]
                run.previous_run_id=resolved[-1] if resolved else None
                self.store.save_run(run)
            self.execute(run_id,request)
            us_route_decision=None
            if market_id=="US":
                completed_run=self.get_run(run_id)
                us_route_decision=self.us_return_max_ledger.by_snapshot(
                    snapshot.snapshot_id,
                    self.us_return_max.version,
                )
                if us_route_decision is None:
                    proposed_us_route=self.us_return_max.decide(
                        prepared["panel"],
                        completed_run.strategy_group,
                        completed_run.triaid_decision,
                        completed_run.strategy_states,
                        phase,
                        self._previous_us_route_decision(snapshot.as_of),
                    )
                    us_route_decision=self.us_return_max_ledger.freeze(
                        proposed_us_route,
                        snapshot.snapshot_id,
                        snapshot.as_of,
                    )
                snapshot.metadata["us_return_max_decision_id"]=us_route_decision.get("decision_id")
                snapshot.metadata["us_return_max_decision_hash"]=us_route_decision.get("decision_hash")
            with self._lock:
                run=self._runs[run_id]
                run.market=snapshot
                run.diagnostic_summary={
                    **dict(run.diagnostic_summary or {}),
                    "recovery_wave_decision_id":recovery_decision.get("decision_id") if recovery_decision else None,
                    "recovery_wave_decision_hash":recovery_decision.get("decision_hash") if recovery_decision else None,
                    "recovery_wave_decision_status":recovery_decision.get("decision_status") if recovery_decision else None,
                    "recovery_wave_daily_bar_complete":daily_bar_complete if market_id=="CN" else None,
                    "recovery_wave_outcome_recorded":bool((recovery_outcome or {}).get("recorded")),
                    "us_return_max_decision_id":us_route_decision.get("decision_id") if us_route_decision else None,
                    "us_return_max_decision_hash":us_route_decision.get("decision_hash") if us_route_decision else None,
                    "us_return_max_decision_status":us_route_decision.get("decision_status") if us_route_decision else None,
                    "us_return_max_outcome_recorded":bool((us_return_outcome or {}).get("recorded")),
                    "daily_bar_complete":daily_bar_complete,
                    "evidence_state":"COMPLETE_DAILY" if daily_bar_complete else "PROVISIONAL_INTRADAY",
                }
                self.store.save_run(run)
        except Exception as exc:
            with self._lock:
                run=self._runs[run_id]
                run.status="FAILED"
                run.diagnostic_summary={"error":f"{type(exc).__name__}:{exc}"}
                self.store.save_run(run)

    def recovery_wave_status(self,market_id:str|None=None)->dict:
        return self.recovery_wave_ledger.status(market_id)

    def latest_recovery_wave_decision(self,market_id:str="CN")->dict|None:
        return self.recovery_wave_ledger.latest(market_id)

    def recovery_wave_history(self,market_id:str="CN",limit:int=100)->list[dict]:
        return self.recovery_wave_ledger.decisions(market_id,limit)

    def recovery_wave_daily_report(self,market_id:str="CN")->dict|None:
        return self.recovery_wave_ledger.daily_report(market_id)

    def us_return_max_status(self)->dict:
        return self.us_return_max_ledger.status()

    def latest_us_return_max_decision(self)->dict|None:
        return self.us_return_max_ledger.latest()

    def us_return_max_history(self,limit:int=100)->list[dict]:
        return self.us_return_max_ledger.decisions(limit)

    def us_return_max_daily_report(self)->dict|None:
        return self.us_return_max_ledger.daily_report()

    def prospective_experiment_status(self)->dict:
        return self.prospective_experiment.status()

    def prospective_experiments(self,limit:int=100)->list[dict]:
        return self.prospective_experiment.list(limit)

    def latest_prospective_experiment(self)->dict|None:
        return self.prospective_experiment.latest()

    def prospective_experiment_detail(self,experiment_id:str)->dict:
        return self.prospective_experiment.get(experiment_id)

    def latest_decision_run(self,market_id:str)->RunRecord|None:
        market_id=market_id.upper()
        rows=[
            r for r in self.all_runs()
            if r.market.market_id.upper()==market_id
            and r.strategy_group is not None
            and r.triaid_decision is not None
            and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
            and self._complete_daily_evidence_run(r)
        ]
        return rows[-1] if rows else None

    def run_live_research(self,market_id:str)->RunRecord:
        run=self.create_pending_live_run(market_id)
        self.execute_live(run.run_id,market_id)
        return self.get_run(run.run_id)

    def recompute_transition_research(
        self,
        market_id:str,
        transition:dict,
        mode:str,
    )->dict:
        market_id=market_id.upper()
        reference=self.latest_decision_run(market_id)
        if reference is None:
            attempted=self.run_live_research(market_id)
            reference=self.latest_decision_run(market_id)
            if reference is None:
                return {
                    "research_only":True,
                    "action_generated":False,
                    "status":"NO_REFERENCE_DECISION",
                    "attempt_run_id":attempted.run_id,
                    "attempt_status":attempted.status,
                }

        market=reference.market.model_copy(deep=True)
        source_ts=transition.get("source_latest_ts")
        mean_return=float(transition.get("mean_return") or 0.0)
        advancers=int(transition.get("advancers") or 0)
        decliners=int(transition.get("decliners") or 0)
        if mean_return<0 and decliners>advancers:
            transition_regime="intraday_risk_off"
        elif mean_return>0 and advancers>decliners:
            transition_regime="intraday_risk_on"
        else:
            transition_regime="intraday_mixed"
        market.regime=transition_regime
        market.snapshot_id=f"{market_id}:{mode.upper()}:TRANSITION:{source_ts}"
        market.as_of=str(source_ts)
        market.metadata=dict(market.metadata or {})
        market.metadata.update({
            "research_only":True,
            "decision_trigger":"STATE_TRANSITION",
            "transition_mode":mode.upper(),
            "transition_source_latest_ts":source_ts,
            "transition_regime":transition_regime,
            "intraday_policy":"RISK_REDUCTION_ALLOWED_RISK_INCREASE_REQUIRES_DAILY_EVIDENCE",
            "transition_features":{
                "mean_return":transition.get("mean_return"),
                "mean_abs_return":transition.get("mean_abs_return"),
                "max_abs_return":transition.get("max_abs_return"),
                "cross_sectional_dispersion":transition.get("cross_sectional_dispersion"),
                "advancers":transition.get("advancers"),
                "decliners":transition.get("decliners"),
            },
            "reference_run_id":reference.run_id,
        })
        decision=self.core.decide(
            market,
            reference.strategy_group,
            reference.strategy_states,
        )
        prior=reference.triaid_decision.weights_after if reference.triaid_decision else {}
        keys=set(prior)|set(decision.weights_after)
        l1=sum(abs(decision.weights_after.get(k,0.0)-prior.get(k,0.0)) for k in keys)
        return {
            "research_only":True,
            "action_generated":False,
            "status":"RECOMPUTED",
            "market_id":market_id,
            "mode":mode.upper(),
            "source_latest_ts":source_ts,
            "reference_run_id":reference.run_id,
            "core_version":decision.core_version,
            "transition_regime":transition_regime,
            "weights_before":decision.weights_before,
            "weights_after":decision.weights_after,
            "weight_change_l1_vs_reference":l1,
            "diagnostics":decision.diagnostics,
        }

    def submit_outcome(self,run_id:str,outcome:OutcomeRequest)->RunRecord:
        with self._lock:
            run=self._runs.get(run_id) or self.store.load_run(run_id)
            if run.strategy_group is None or run.triaid_decision is None:
                raise ValueError("Decision is not ready.")
            if run.evaluation and run.evaluation.status=="EVALUATED":
                incoming={str(k):float(v) for k,v in outcome.realized_returns.items()}
                existing={str(k):float(v) for k,v in run.evaluation.strategy_realized_returns.items()}
                same_returns=(
                    set(incoming)==set(existing)
                    and all(abs(incoming[k]-existing[k])<=1e-15 for k in incoming)
                )
                same_cost=abs(float(outcome.trading_cost)-float(run.evaluation.trading_cost))<=1e-15
                if same_returns and same_cost:
                    return run
                raise ValueError("outcome_already_evaluated_conflict")
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
            previous_diagnostics=dict(run.diagnostic_summary or {})
            run.diagnostic_summary={
                **previous_diagnostics,
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
            "active_strategy_rules":{
                market_id:self.strategy_evolution.active(market_id).__dict__
                for market_id in ("US","CN")
            },
            "strategy_registry_count":len(self.strategy_population.definitions()),
            "markets":["US","CN"],
            "run_counts":counts,
            "market_data":{
                "status":market_data_status(),
                "capabilities":market_data_capabilities(),
                "observations":self.observations.status(),
            },
        }



    def market_data_status(self)->dict:
        return market_data_status()

    def market_data_capabilities(self,market_id:str|None=None)->dict:
        return market_data_capabilities(market_id)

    def market_data_product_capabilities(self,market_id:str|None=None)->dict:
        return market_data_product_capabilities(market_id)

    def market_data_provider_status(self)->dict:
        return market_data_provider_status()

    def market_data_latest_quotes(self,market_id:str,symbols:list[str]|tuple[str,...])->dict:
        return market_data_latest_quotes(market_id,symbols)

    def market_data_instrument_series(self,market_id:str,symbol:str,mode:str="DAILY")->dict:
        return market_data_instrument_series(market_id,symbol,mode)

    def strategy_market_context(self,market_id:str)->dict:
        return strategy_market_context(market_id)

    def record_market_observation(self,snapshot:dict)->dict:
        return self.observations.record(snapshot)

    def market_observations(
        self,
        market_id:str|None=None,
        mode:str|None=None,
        limit:int=500,
    )->list[dict]:
        return self.observations.list(market_id,mode,limit)

    def market_observation_status(self)->dict:
        return self.observations.status()

    def market_transitions(
        self,
        market_id:str|None=None,
        mode:str|None=None,
        limit:int=500,
    )->list[dict]:
        return self.observations.transitions(market_id,mode,limit)

    def market_data_snapshot(self,market_id:str,mode:str,refresh:bool=False)->dict:
        return market_data_snapshot(market_id,mode,refresh)

    def refresh_market_data(self,market_id:str,mode:str)->dict:
        return refresh_market_data(market_id,mode)

    def daily_summary(self,market_id:str|None=None)->dict:
        rows=self.all_runs()
        if market_id:
            rows=[r for r in rows if r.market.market_id.upper()==market_id.upper()]
        summary=self.review.daily_summary(rows)
        include_us=(market_id is None) or market_id.upper()=="US"
        if include_us:
            us_return=self.us_return_max_ledger.daily_report()
            if us_return:
                summary["us_return_max"]=us_return
        include_cn=(market_id is None) or market_id.upper()=="CN"
        if include_cn:
            recovery=self.recovery_wave_ledger.daily_report("CN")
            if recovery:
                summary["recovery_wave"]=recovery
            prospective=self.prospective_experiment.daily_report()
            if prospective:
                try:
                    source=self.get_run(str(prospective.get("source_run_id")))
                except Exception:
                    source=None
                reason_map={}
                if source and source.strategy_group:
                    reason_map={
                        sid:value.model_dump()
                        for sid,value in source.strategy_group.reasons.items()
                    }
                for row in prospective.get("strategy_determination",[]):
                    sid=row.get("strategy_id")
                    definition=self.strategy_population.definition(str(sid))
                    row["name"]={
                        "zh":definition.name.zh if definition else str(sid),
                        "en":definition.name.en if definition else str(sid),
                    }
                    row["selection_reason"]=reason_map.get(sid)
                summary["prospective_experiment"]=prospective
        return summary

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

    def validate_core_candidate(self,version:str)->dict:
        candidate=self.evolution.get(version)
        manifest=self.evolution.candidate_manifest(version)
        if manifest is None or not candidate.parent_version:
            receipt={
                "receipt_id":f"COREVAL-{version}-{uuid4().hex[:12]}",
                "passed":False,
                "replay_pass":False,
                "holdout_pass":False,
                "shadow_pass":False,
                "audit_pass":False,
                "reason":"CANDIDATE_EVIDENCE_MANIFEST_MISSING",
            }
            return self.evolution.record_validation(version,receipt)
        parent=self.evolution.get(candidate.parent_version)
        if not manifest.get("development_run_ids_by_market") or not manifest.get("reserved_holdout_run_ids_by_market"):
            receipt={
                "receipt_id":f"COREVAL-{version}-{uuid4().hex[:12]}",
                "passed":False,
                "replay_pass":False,
                "holdout_pass":False,
                "shadow_pass":False,
                "audit_pass":False,
                "reason":"LEGACY_UNSTRATIFIED_CANDIDATE_EVIDENCE",
            }
            return self.evolution.record_validation(version,receipt)

        run_map={r.run_id:r for r in self.all_runs()}
        dev=[run_map[x] for x in manifest.get("development_run_ids",[]) if x in run_map]
        holdout=[run_map[x] for x in manifest.get("reserved_holdout_run_ids",[]) if x in run_map]
        created_at=str(manifest.get("created_at") or "")
        known_ids=set(manifest.get("development_run_ids",[]))|set(manifest.get("reserved_holdout_run_ids",[]))
        shadow=[
            r for r in self.all_runs()
            if created_at and r.created_at>created_at
            and r.evaluation and r.evaluation.status=="EVALUATED"
            and self._complete_daily_evidence_run(r)
            and str(r.market.market_id).upper() in {"US","CN"}
            and r.run_id not in known_ids
        ]

        def replay(rows:list[RunRecord])->dict:
            cand_vals=[];parent_vals=[];valid=True
            cand_core=TriaidCoreModule(candidate)
            parent_core=TriaidCoreModule(parent)
            for run in rows:
                if not run.strategy_group or not run.evaluation or run.evaluation.status!="EVALUATED":
                    valid=False
                    continue
                try:
                    cand_decision=cand_core.decide(run.market,run.strategy_group,run.strategy_states)
                    parent_decision=parent_core.decide(run.market,run.strategy_group,run.strategy_states)
                    realized=run.evaluation.strategy_realized_returns
                    def cost(decision):
                        turnover=sum(
                            abs(float(decision.weights_after.get(k,0.0))-float(run.strategy_group.weights.get(k,0.0)))
                            for k in set(decision.weights_after)|set(run.strategy_group.weights)
                        )
                        bps=float(run.market.metadata.get("base_cost_bps",2.0) or 2.0)
                        return turnover*bps/10000.0
                    cand_eval=self.evaluation.evaluate(run.strategy_group,cand_decision,realized,cost(cand_decision))
                    parent_eval=self.evaluation.evaluate(run.strategy_group,parent_decision,realized,cost(parent_decision))
                    cand_vals.append(float(cand_eval.triaid_return))
                    parent_vals.append(float(parent_eval.triaid_return))
                except Exception:
                    valid=False
            return {
                "count":len(cand_vals),
                "candidate_mean":mean(cand_vals) if cand_vals else None,
                "parent_mean":mean(parent_vals) if parent_vals else None,
                "valid":valid and len(cand_vals)==len(rows),
            }

        def replay_by_market(rows:list[RunRecord])->dict:
            return {
                market:replay([r for r in rows if str(r.market.market_id).upper()==market])
                for market in ("US","CN")
            }

        dev_result=replay(dev)
        holdout_result=replay(holdout)
        shadow_result=replay(shadow)
        dev_by_market=replay_by_market(dev)
        holdout_by_market=replay_by_market(holdout)
        shadow_by_market=replay_by_market(shadow)

        def nondegrading(result:dict,min_count:int)->bool:
            return bool(
                result["valid"] and result["count"]>=min_count
                and result["candidate_mean"] is not None
                and result["parent_mean"] is not None
                and result["candidate_mean"]>=result["parent_mean"]-1e-12
            )

        replay_pass=all(nondegrading(dev_by_market[m],1) for m in ("US","CN"))
        holdout_pass=all(nondegrading(holdout_by_market[m],1) for m in ("US","CN"))
        shadow_min_per_market=5
        shadow_pass=all(nondegrading(shadow_by_market[m],shadow_min_per_market) for m in ("US","CN"))
        audit_pass=bool(
            0.0<=candidate.intervention_strength<=1.0
            and candidate.risk_penalty>=0
            and candidate.uncertainty_penalty>=0
            and 0.0<=candidate.risk_off_multiplier<=1.0
            and dev_result["valid"] and holdout_result["valid"] and shadow_result["valid"]
        )
        receipt={
            "receipt_id":f"COREVAL-{version}-{uuid4().hex[:12]}",
            "passed":all((replay_pass,holdout_pass,shadow_pass,audit_pass)),
            "replay_pass":replay_pass,
            "holdout_pass":holdout_pass,
            "shadow_pass":shadow_pass,
            "audit_pass":audit_pass,
            "development":dev_result,
            "holdout":holdout_result,
            "shadow":shadow_result,
            "development_by_market":dev_by_market,
            "holdout_by_market":holdout_by_market,
            "shadow_by_market":shadow_by_market,
            "shadow_min_runs_per_market":shadow_min_per_market,
            "candidate_parent":candidate.parent_version,
            "validation_discipline":"INTERNAL_MARKET_STRATIFIED_REPLAY_RESERVED_HOLDOUT_AND_POST_CREATION_SHADOW; NO_CROSS_MARKET_MASKING",
        }
        return self.evolution.record_validation(version,receipt)

    def promote_core(self,version:str,validation:dict|None=None)->dict:
        receipt=self.validate_core_candidate(version)
        result=self.evolution.promote(version,{"receipt_id":receipt.get("receipt_id")})
        if result.get("promoted"):
            self.refresh_core()
        else:
            result["validation_receipt"]=receipt
        return result

    def strategy_evolution_status(self,market_id:str|None=None)->dict:
        if market_id:
            data=self.strategy_evolution.status(market_id)
            return {
                **data,
                "diagnosis":self.strategy_evolution.diagnose(market_id,self.all_runs()),
            }
        return {
            market_id:self.strategy_evolution_status(market_id)
            for market_id in ("US","CN")
        }

    def propose_strategy_candidate(self,market_id:str)->dict:
        return self.strategy_evolution.propose_candidate(market_id,self.all_runs())

    def validate_strategy_candidate(self,market_id:str,version:str)->dict:
        return self.strategy_evolution.validate_candidate(market_id,version,self.all_runs())

    def promote_strategy_rules(self,market_id:str,version:str,validation:dict|None=None)->dict:
        receipt=self.validate_strategy_candidate(market_id,version)
        result=self.strategy_evolution.promote(market_id,version,{"receipt_id":receipt.get("receipt_id")})
        if result.get("promoted"):
            self._apply_strategy_profiles()
        else:
            result["validation_receipt"]=receipt
        return result
