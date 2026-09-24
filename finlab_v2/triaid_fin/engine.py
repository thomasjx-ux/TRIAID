from __future__ import annotations

from datetime import datetime
import os
from statistics import mean
from threading import RLock
from typing import Dict, List
from uuid import uuid4
from zoneinfo import ZoneInfo

from .audit import AuditModule
from .account_registry import ACCOUNT_REGISTRY, register_account, register_strategy_pool
from .alpha_evidence import AlphaEvidenceLedger
from .contracts import AccountProfile, DecisionContext, MarketSnapshot, OutcomeRequest, RunRecord, RunRequest, StrategyPoolSpec
from .core import TriaidCoreModule
from .evaluation import EvaluationModule
from .execution_calibration import ExecutionCalibration
from .evolution import EvolutionModule
from .market_registry import MARKET_REGISTRY, evidence_market_ids, market_ids, normalize_market_id
from .market_lab import MARKETS, market_data_auction_shadow_probe, market_data_capabilities, market_data_instrument_series, market_data_latest_quotes, market_data_product_capabilities, market_data_provider_status, market_data_snapshot, market_data_status, prepare_live_market, refresh_market_data, strategy_market_context
from .long_cycle_hypothesis import LongCycleHypothesisExperiment
from .cross_market_crash import CrossMarketCrashExperiment
from .latent_hazard import LatentHazardExperiment
from .policy_curve import PolicyExpectationCurve
from .hazard_prospective import HazardProspectiveLedger
from .risk_warning import RiskWarningSystem
from .risk_control import CrossMarketRiskControlExperiment
from .population_state import PopulationStateTracker
from .prospective_experiment import ProspectiveExperimentProtocol
from .recovery_core import RecoveryWaveCore
from .recovery_ledger import RecoveryWaveLedger
from .observation import MarketObservationStore
from .objective import VERSION as OBJECTIVE_CONSTITUTION_VERSION
from .review import ReviewModule
from .store import RunStore
from .strategy_evolution import StrategyEvolutionModule
from .strategy_population import StrategyPopulationModule
from .strategy_registry import strategy_ids_for_market
from .trading_calendar import VERSION as TRADING_CALENDAR_VERSION
from .trading_calendar_sync import VERSION as TRADING_CALENDAR_SYNC_VERSION
from .us_return_max import USReturnMaxLedger, USReturnMaxRoute
from .hk_return_max import HKReturnMaxLedger, HKReturnMaxRoute
from .volatility_forecast import cached_all_market_volatility_forecasts, cached_volatility_forecast, refresh_all_market_volatility_forecasts, refresh_market_volatility_forecast


class EvolutionLabEngine:
    architecture_version = "fin-evolution-lab@0.14.0"
    market_adapter_version = "market-lab@0.5.0"

    def __init__(self) -> None:
        self.store=RunStore()
        MARKET_REGISTRY.load_from_store(self.store)
        self.account_registry=ACCOUNT_REGISTRY
        self.account_registry.load_from_store(self.store)
        self.observations=MarketObservationStore(self.store)
        self.evolution=EvolutionModule(self.store)
        self.strategy_evolution=StrategyEvolutionModule(self.store)
        self.strategy_population=StrategyPopulationModule()
        self._apply_strategy_profiles()
        self.population_state=PopulationStateTracker(self.store,self.strategy_population)
        self.core=TriaidCoreModule(self.evolution.active())
        self.evaluation=EvaluationModule()
        self.audit=AuditModule()
        self.alpha_evidence=AlphaEvidenceLedger(self.store)
        self.review=ReviewModule()
        self.prospective_experiment=ProspectiveExperimentProtocol(self.store)
        self.recovery_wave_ledger=RecoveryWaveLedger(self.store)
        self.recovery_wave_core=RecoveryWaveCore(self.evolution.active())
        self.us_return_max=USReturnMaxRoute()
        self.us_return_max_ledger=USReturnMaxLedger(self.store)
        self.hk_return_max=HKReturnMaxRoute()
        self.hk_return_max_ledger=HKReturnMaxLedger(self.store)
        self.long_cycle_hypothesis=LongCycleHypothesisExperiment(self.store)
        self.cross_market_crash=CrossMarketCrashExperiment(self.store)
        self.latent_hazard=LatentHazardExperiment(self.store)
        self.policy_curve=PolicyExpectationCurve(self.store)
        self.hazard_prospective=HazardProspectiveLedger(self.store)
        self.risk_warning=RiskWarningSystem(self.store)
        self.risk_control=CrossMarketRiskControlExperiment(self.store)
        self._runs:Dict[str,RunRecord]={r.run_id:r for r in self.store.list_runs()}
        self._lock=RLock()
        self._live_lock=RLock()

    @staticmethod
    def _evidence_eligible_run(run:RunRecord)->bool:
        metadata=run.market.metadata or {}
        return (
            metadata.get("evidence_eligible") is not False
            and str(metadata.get("run_scope") or "OFFICIAL_EVIDENCE")!="MANUAL_PREVIEW"
            and run.status!="PREVIEW_READY"
        )

    def _save_run(self,run:RunRecord)->None:
        if self._evidence_eligible_run(run):
            self.store.save_run(run)

    def _prune_manual_previews(self,max_per_market:int=5)->None:
        buckets:dict[tuple[str,str,str],list[RunRecord]]={}
        for row in self._runs.values():
            if str((row.market.metadata or {}).get("run_scope") or "")!="MANUAL_PREVIEW":
                continue
            if row.status=="FETCHING_DATA":
                continue
            key=(
                str(row.market.market_id).upper(),
                str(row.account_id or "GLOBAL"),
                str(row.strategy_pool_id or "GLOBAL"),
            )
            buckets.setdefault(key,[]).append(row)
        for previews in buckets.values():
            previews.sort(key=lambda row:row.created_at)
            for row in previews[:-max_per_market]:
                self._runs.pop(row.run_id,None)

    def recover_stale_runs(self)->dict:
        recovered=[]
        with self._lock:
            for run in list(self._runs.values()):
                if run.status not in {"CREATED","FETCHING_DATA"}:
                    continue
                if not self._evidence_eligible_run(run):
                    continue
                run.status="FAILED"
                run.diagnostic_summary={
                    **dict(run.diagnostic_summary or {}),
                    "error":"STALE_INCOMPLETE_RUN_RECOVERED_AFTER_PROCESS_RESTART",
                    "recovery":"Previous process ended before this official research run completed.",
                }
                self._save_run(run)
                recovered.append(run.run_id)
            receipt={
                "event":"STALE_RUN_RECOVERY",
                "at":datetime.now(ZoneInfo("UTC")).isoformat(),
                "recovered_run_ids":recovered,
                "recovered_count":len(recovered),
            }
            self.store.append_jsonl("runtime_maintenance_events.jsonl",receipt)
        return receipt

    def _apply_strategy_profiles(self)->None:
        for market_id in market_ids():
            self.strategy_population.configure_market(self.strategy_evolution.active(market_id))

    def account_registry_status(self)->dict:
        return self.account_registry.snapshot()

    def upsert_strategy_pool(self,pool:StrategyPoolSpec)->dict:
        row=register_strategy_pool(pool,replace=True,persist=True)
        return row.model_dump(mode="json")

    def upsert_account(self,account:AccountProfile)->dict:
        row=register_account(account,replace=True,persist=True)
        return row.model_dump(mode="json")

    def refresh_core(self)->None:
        params=self.evolution.active()
        self.core=TriaidCoreModule(params)
        self.recovery_wave_core=RecoveryWaveCore(params)

    @property
    def module_manifest(self)->Dict[str,str]:
        return {
            "architecture":self.architecture_version,
            "account_registry":self.account_registry.version,
            "market_registry":MARKET_REGISTRY.version,
            "objective_constitution":OBJECTIVE_CONSTITUTION_VERSION,
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
            "strategy_rules_HK":self.strategy_evolution.active("HK").version,
            "triaid_core":self.core.version if hasattr(self,"core") else self.evolution.active().version,
            "evaluation":self.evaluation.version if hasattr(self,"evaluation") else "evaluation@0.2.0",
            "audit":self.audit.version if hasattr(self,"audit") else "audit@0.2.0",
            "alpha_evidence":self.alpha_evidence.version if hasattr(self,"alpha_evidence") else "alpha-evidence-ledger@unknown",
            "review":self.review.version if hasattr(self,"review") else "review@0.2.0",
            "prospective_experiment":self.prospective_experiment.version if hasattr(self,"prospective_experiment") else "cn-prospective-controls@unknown",
            "recovery_wave_core":self.recovery_wave_core.version if hasattr(self,"recovery_wave_core") else "recovery-wave-core@unknown",
            "recovery_wave_ledger":self.recovery_wave_ledger.version if hasattr(self,"recovery_wave_ledger") else "recovery-wave-ledger@unknown",
            "capital_capacity":self.recovery_wave_core.capital_capacity.version if hasattr(self,"recovery_wave_core") else "capital-capacity-layer@unknown",
            "us_return_max":self.us_return_max.version if hasattr(self,"us_return_max") else "us-return-max-route@unknown",
            "us_return_max_ledger":self.us_return_max_ledger.version if hasattr(self,"us_return_max_ledger") else "us-return-max-ledger@unknown",
            "hk_return_max":self.hk_return_max.version if hasattr(self,"hk_return_max") else "hk-return-max-route@unknown",
            "hk_return_max_ledger":self.hk_return_max_ledger.version if hasattr(self,"hk_return_max_ledger") else "hk-return-max-ledger@unknown",
            "long_cycle_hypothesis":self.long_cycle_hypothesis.version if hasattr(self,"long_cycle_hypothesis") else "us-long-cycle-hypothesis@unknown",
            "cross_market_crash":self.cross_market_crash.version if hasattr(self,"cross_market_crash") else "us-cn-hk-crash-linkage@unknown",
            "latent_hazard":self.latent_hazard.version if hasattr(self,"latent_hazard") else "latent-hazard-discovery@unknown",
            "policy_curve":self.policy_curve.version if hasattr(self,"policy_curve") else "policy-expectation-curve@unknown",
            "hazard_prospective":self.hazard_prospective.version if hasattr(self,"hazard_prospective") else "hazard-prospective-ledger@unknown",
            "risk_warning":self.risk_warning.version if hasattr(self,"risk_warning") else "risk-warning@unknown",
            "risk_control":self.risk_control.version if hasattr(self,"risk_control") else "cross-market-risk-control@unknown",
            "store":self.store.version,
            "evolution":self.evolution.version,
            "execution_calibration":ExecutionCalibration.version,
        }

    def create_run(self,request:RunRequest,run_id:str|None=None)->RunRecord:
        run_id=run_id or f"{request.market.market_id}-{uuid4().hex[:12]}"
        account_id=(request.account.account_id if request.account else (request.decision_context.account_id if request.decision_context else "GLOBAL"))
        strategy_pool_id=(request.strategy_pool.pool_id if request.strategy_pool else (request.decision_context.strategy_pool_id if request.decision_context else "GLOBAL"))
        run=RunRecord(
            run_id=run_id,
            module_manifest=self.module_manifest,
            market=request.market,
            account_id=account_id,
            strategy_pool_id=strategy_pool_id,
            decision_context=request.decision_context,
            strategy_states=list(request.strategy_states),
        )
        with self._lock:
            self._runs[run_id]=run
            self._save_run(run)
        return run

    def claim_manual_preview_run(
        self,
        market_id:str,
        account_id:str="GLOBAL",
    )->tuple[RunRecord,bool,str]:
        market_id=normalize_market_id(market_id)
        account=self.account_registry.get_account(account_id)
        strategy_pool=self.account_registry.get_pool(account.strategy_pool_id)
        cooldown_seconds=max(
            0,
            int(os.getenv("TRIAID_MANUAL_PREVIEW_COOLDOWN_SECONDS","60") or "60"),
        )
        now=datetime.now(ZoneInfo("UTC"))
        with self._lock:
            previews=sorted(
                [
                    r for r in self._runs.values()
                    if r.market.market_id.upper()==market_id
                    and str((r.market.metadata or {}).get("run_scope") or "")=="MANUAL_PREVIEW"
                ],
                key=lambda r:r.created_at,
            )
            pending=[r for r in previews if r.status=="FETCHING_DATA"]
            if pending:
                return pending[-1],False,"PENDING_REUSED"

            recent_ready=[
                r for r in previews
                if r.status=="PREVIEW_READY"
            ]
            if recent_ready and cooldown_seconds>0:
                recent=recent_ready[-1]
                try:
                    age_seconds=max(
                        0.0,
                        (now-datetime.fromisoformat(recent.created_at)).total_seconds(),
                    )
                except Exception:
                    age_seconds=float(cooldown_seconds)
                if age_seconds<cooldown_seconds:
                    return recent,False,"COOLDOWN_REUSED"

            self._prune_manual_previews()
            run_id=f"{market_id}-{account.account_id}-live-{uuid4().hex[:12]}"
            run=RunRecord(
                run_id=run_id,
                module_manifest=self.module_manifest,
                account_id=account.account_id,
                strategy_pool_id=strategy_pool.pool_id,
                market=MarketSnapshot(
                    market_id=market_id,
                    as_of="",
                    snapshot_id="PENDING",
                    metadata={
                        "run_scope":"MANUAL_PREVIEW",
                        "evidence_eligible":False,
                        "research_only":True,
                        "account_id":account.account_id,
                        "strategy_pool_id":strategy_pool.pool_id,
                    },
                ),
                status="FETCHING_DATA",
            )
            self._runs[run_id]=run
            self._save_run(run)
            return run,True,"CREATED"

    def create_pending_live_run(
        self,
        market_id:str,
        run_scope:str="OFFICIAL_EVIDENCE",
        account_id:str="GLOBAL",
    )->RunRecord:
        market_id=normalize_market_id(market_id)
        account=self.account_registry.get_account(account_id)
        strategy_pool=self.account_registry.get_pool(account.strategy_pool_id)
        run_scope=str(run_scope or "OFFICIAL_EVIDENCE").upper()
        if run_scope not in {"OFFICIAL_EVIDENCE","MANUAL_PREVIEW"}:
            raise ValueError("run_scope must be OFFICIAL_EVIDENCE or MANUAL_PREVIEW")
        if run_scope=="MANUAL_PREVIEW":
            run,_,_=self.claim_manual_preview_run(market_id,account.account_id)
            return run

        run_id=f"{market_id}-{account.account_id}-live-{uuid4().hex[:12]}"
        run=RunRecord(
            run_id=run_id,
            module_manifest=self.module_manifest,
            account_id=account.account_id,
            strategy_pool_id=strategy_pool.pool_id,
            market=MarketSnapshot(
                market_id=market_id,
                as_of="",
                snapshot_id="PENDING",
                metadata={
                    "run_scope":"OFFICIAL_EVIDENCE",
                    "evidence_eligible":True,
                    "research_only":True,
                    "account_id":account.account_id,
                    "strategy_pool_id":strategy_pool.pool_id,
                },
            ),
            status="FETCHING_DATA",
        )
        with self._lock:
            self._runs[run_id]=run
            self._save_run(run)
        return run

    @classmethod
    def _complete_daily_evidence_run(cls,run:RunRecord)->bool:
        # Legacy official runs without the explicit flag remain eligible. Manual
        # previews and provisional intraday runs are never evidence-bearing.
        return (
            cls._evidence_eligible_run(run)
            and (run.market.metadata or {}).get("daily_bar_complete") is not False
        )

    def _previous_us_route_decision(self,market_as_of:str)->dict|None:
        rows=[
            row for row in self.us_return_max_ledger.decisions(2000)
            if str(row.get("decision_status") or "")=="DAILY_FROZEN"
            and str(row.get("market_as_of") or "")<str(market_as_of)
        ]
        return rows[-1] if rows else None

    def _previous_group_for(
        self,
        market_id:str,
        exclude_run_id:str|None=None,
        experiment_mode:str|None=None,
        account_id:str="GLOBAL",
        strategy_pool_id:str="GLOBAL",
    ):
        mode=str(experiment_mode or "").upper()
        rows=[
            r for r in self.all_runs()
            if r.run_id!=exclude_run_id
            and r.market.market_id.upper()==market_id.upper()
            and str(r.account_id or "GLOBAL")==str(account_id or "GLOBAL")
            and str(r.strategy_pool_id or "GLOBAL")==str(strategy_pool_id or "GLOBAL")
            and r.strategy_group is not None
            and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
            and self._complete_daily_evidence_run(r)
            and (
                not mode
                or str((r.market.metadata or {}).get("experiment_mode") or "").upper()==mode
            )
        ]
        return rows[-1].strategy_group if rows else None

    def execute(self,run_id:str,request:RunRequest)->None:
        try:
            current_mode=str(request.market.metadata.get("experiment_mode") or "")
            request_account_id=(
                request.account.account_id
                if request.account
                else (request.decision_context.account_id if request.decision_context else "GLOBAL")
            )
            previous_group=self._previous_group_for(
                request.market.market_id,
                run_id,
                current_mode,
                request_account_id,
                (
                    request.strategy_pool.pool_id
                    if request.strategy_pool
                    else (request.decision_context.strategy_pool_id if request.decision_context else "GLOBAL")
                ),
            )
            previous_state_rows=[
                r for r in self.all_runs()
                if r.run_id!=run_id
                and r.market.market_id.upper()==request.market.market_id.upper()
                and str(r.account_id or "GLOBAL")==str(request_account_id or "GLOBAL")
                and str(r.strategy_pool_id or "GLOBAL")==str(
                    request.strategy_pool.pool_id
                    if request.strategy_pool
                    else (request.decision_context.strategy_pool_id if request.decision_context else "GLOBAL")
                )
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
                and request.market.metadata.get("evidence_eligible") is not False
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
                run.account_id=request_account_id
                run.strategy_pool_id=(
                    request.strategy_pool.pool_id
                    if request.strategy_pool
                    else (request.decision_context.strategy_pool_id if request.decision_context else "GLOBAL")
                )
                run.decision_context=request.decision_context
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
                if not run.audit.passed:
                    run.status="FAILED"
                elif request.market.metadata.get("evidence_eligible") is False:
                    run.status="PREVIEW_READY"
                else:
                    run.status="DECISION_READY_AWAITING_OUTCOME"
                self._save_run(run)
        except Exception as exc:
            with self._lock:
                run=self._runs[run_id]
                run.status="FAILED"
                run.diagnostic_summary={"error":f"{type(exc).__name__}:{exc}"}
                self._save_run(run)
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

    def execute_live(
        self,
        run_id:str,
        market_id:str,
        run_scope:str="OFFICIAL_EVIDENCE",
    )->None:
        # Official evidence runs mutate shared lifecycle/evidence ledgers and are
        # serialized with manual previews. Preview runs themselves remain read-only.
        with self._live_lock:
            return self._execute_live_locked(run_id,market_id,run_scope)

    def _execute_live_locked(
        self,
        run_id:str,
        market_id:str,
        run_scope:str="OFFICIAL_EVIDENCE",
    )->None:
        market_id=market_id.upper()
        run_scope=str(run_scope or "OFFICIAL_EVIDENCE").upper()
        if run_scope not in {"OFFICIAL_EVIDENCE","MANUAL_PREVIEW"}:
            raise ValueError("run_scope must be OFFICIAL_EVIDENCE or MANUAL_PREVIEW")
        evidence_eligible=run_scope=="OFFICIAL_EVIDENCE"
        try:
            with self._lock:
                run_context=self._runs[run_id]
                account_id=str(run_context.account_id or "GLOBAL")
                strategy_pool_id=str(run_context.strategy_pool_id or "GLOBAL")
            account=self.account_registry.get_account(account_id)
            strategy_pool=self.account_registry.get_pool(strategy_pool_id)
            profile=self.strategy_evolution.active(market_id)
            prepared=prepare_live_market(market_id,profile.window_weights)
            eligible_strategy_ids=set(
                strategy_ids_for_market(
                    market_id,
                    account_id=account_id,
                    strategy_pool_id=strategy_pool_id,
                )
            )
            prepared["strategy_states"]=[
                state for state in prepared["strategy_states"]
                if state.strategy_id in eligible_strategy_ids
            ]
            snapshot=prepared["snapshot"]
            snapshot.metadata["strategy_rules_version"]=profile.version
            snapshot.metadata["run_scope"]=run_scope
            snapshot.metadata["evidence_eligible"]=evidence_eligible
            snapshot.metadata["research_only"]=True
            snapshot.metadata["broker_execution_enabled"]=False
            snapshot.metadata["account_id"]=account_id
            snapshot.metadata["strategy_pool_id"]=strategy_pool_id
            snapshot.metadata["account_capital"]=account.capital
            snapshot.metadata["account_objective"]=account.objective

            phase=str(snapshot.metadata.get("session_phase") or "").upper()
            daily_bar_complete=bool(snapshot.metadata.get("daily_bar_complete"))
            snapshot.metadata["daily_bar_complete"]=daily_bar_complete
            snapshot.metadata["evidence_state"]="COMPLETE_DAILY" if daily_bar_complete else "PROVISIONAL_INTRADAY"
            recovery_outcome=None
            recovery_decision=None
            us_return_outcome=None
            hk_return_outcome=None
            if market_id=="CN":
                if evidence_eligible and daily_bar_complete:
                    recovery_outcome=self.recovery_wave_ledger.record_outcome(
                        market_id,
                        prepared["latest_as_of"],
                        prepared["previous_as_of"],
                        prepared.get("product_realized_returns_from_previous_period") or {},
                        snapshot.snapshot_id,
                        prepared.get("product_turnover_notional_from_previous_period") or {},
                    )
                    if recovery_outcome.get("recorded"):
                        self.alpha_evidence.record_cn(
                            self.recovery_wave_ledger.decisions(market_id,5000),
                            recovery_outcome.get("outcome") or {},
                        )
                if evidence_eligible:
                    existing_recovery=self.recovery_wave_ledger.by_snapshot(
                        market_id,
                        snapshot.snapshot_id,
                        self.recovery_wave_core.version,
                    )
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
                snapshot.metadata["recovery_wave_decision_id"]=recovery_decision.get("decision_id") if recovery_decision else None
                snapshot.metadata["recovery_wave_decision_hash"]=recovery_decision.get("decision_hash") if recovery_decision else None
                snapshot.metadata["recovery_wave_daily_bar_complete"]=daily_bar_complete
                snapshot.metadata["experiment_mode"]="CN_RETURN_MAX_CAPACITY"
                snapshot.metadata["experiment_design"]="Select from the full admissible A-share strategy universe with realizable net return as the sole optimization objective. Risk, liquidity, capacity, concentration and switching costs are constraints. Cash is residual rather than a fixed defensive target."
                snapshot.metadata["market_route"]="CN_RETURN_MAXIMIZATION"
                snapshot.metadata["stress_test_route"]="CN_WORST_POOL_RESCUE"
                snapshot.metadata["stress_test_role"]="SECONDARY_DIAGNOSTIC_ONLY"
            elif market_id=="US":
                if evidence_eligible and daily_bar_complete:
                    us_return_outcome=self.us_return_max_ledger.record_outcome(
                        prepared["latest_as_of"],
                        prepared["previous_as_of"],
                        prepared.get("realized_returns_from_previous_period") or {},
                        prepared.get("product_realized_returns_from_previous_period") or {},
                        prepared.get("product_turnover_notional_from_previous_period") or {},
                        snapshot.snapshot_id,
                    )
                    if us_return_outcome.get("recorded"):
                        self.alpha_evidence.record_us(
                            self.us_return_max_ledger.decisions(5000),
                            us_return_outcome.get("outcome") or {},
                        )
                snapshot.metadata["experiment_mode"]="US_RETURN_MAX_CAPACITY"
                snapshot.metadata["experiment_design"]="Use the existing return-first reselect strategy population as the primary US route, expand the frozen strategy mix to executable ETF exposures, and validate realized return versus SPY buy-and-hold and the generic TRIAID Core under four USD capital sleeves."
                snapshot.metadata["market_route"]="US_RETURN_MAXIMIZATION"
            else:
                if evidence_eligible and daily_bar_complete:
                    hk_return_outcome=self.hk_return_max_ledger.record_outcome(
                        prepared["latest_as_of"],
                        prepared["previous_as_of"],
                        prepared.get("realized_returns_from_previous_period") or {},
                        prepared.get("product_realized_returns_from_previous_period") or {},
                        prepared.get("product_turnover_notional_from_previous_period") or {},
                        snapshot.snapshot_id,
                    )
                snapshot.metadata["experiment_mode"]="HK_RETURN_MAX_CAPACITY"
                snapshot.metadata["experiment_design"]="Use the HK return-first strategy population and TRIAID Core as the frozen decision source, expand it into HK ETF exposures, and validate HK-only realized return, execution capacity and costs under four HKD capital sleeves. The route remains research-only and produces no broker orders."
                snapshot.metadata["market_route"]="HK_RETURN_MAXIMIZATION"
                snapshot.metadata["hk_tradable_universe"]=list(MARKETS["HK"].assets)
            snapshot.metadata["primary_route_revision"]=self.architecture_version
            snapshot.metadata["strategy_window_weights"]=list(profile.window_weights)

            current_experiment=snapshot.metadata.get("experiment_mode")
            existing_decisions=[] if not evidence_eligible else [
                r for r in self.all_runs()
                if r.run_id!=run_id
                and r.market.market_id.upper()==market_id
                and r.market.snapshot_id==snapshot.snapshot_id
                and r.market.metadata.get("experiment_mode")==current_experiment
                and str((r.market.metadata or {}).get("primary_route_revision") or "")==self.architecture_version
                and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
                and r.strategy_group is not None
                and r.triaid_decision is not None
            ]
            if existing_decisions:
                existing=existing_decisions[-1]
                prospective_bootstrap=None
                if (
                    market_id=="CN"
                    and str(current_experiment or "").upper()=="CN_WORST_POOL_RESCUE"
                    and self._complete_daily_evidence_run(existing)
                ):
                    prior_rows=[
                        r for r in self.all_runs()
                        if r.run_id!=existing.run_id
                        and r.market.market_id.upper()=="CN"
                        and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
                        and r.strategy_states
                        and self._complete_daily_evidence_run(r)
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
                hk_route_bootstrap=None
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
                elif market_id=="HK":
                    hk_route_bootstrap=self.hk_return_max_ledger.by_snapshot(
                        snapshot.snapshot_id,
                        self.hk_return_max.version,
                    )
                    if hk_route_bootstrap is None:
                        proposed_hk_route=self.hk_return_max.decide(
                            prepared["panel"],
                            existing.strategy_group,
                            existing.triaid_decision,
                            existing.strategy_states,
                            phase,
                        )
                        hk_route_bootstrap=self.hk_return_max_ledger.freeze(
                            proposed_hk_route,
                            snapshot.snapshot_id,
                            snapshot.as_of,
                        )
                    snapshot.metadata["hk_return_max_decision_id"]=hk_route_bootstrap.get("decision_id")
                    snapshot.metadata["hk_return_max_decision_hash"]=hk_route_bootstrap.get("decision_hash")
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
                        "hk_return_max_decision_id":hk_route_bootstrap.get("decision_id") if hk_route_bootstrap else None,
                        "hk_return_max_decision_hash":hk_route_bootstrap.get("decision_hash") if hk_route_bootstrap else None,
                        "hk_return_max_outcome_recorded":bool((hk_return_outcome or {}).get("recorded")),
                    }
                    self._save_run(run)
                return

            resolved=[]
            prospective_observation=None
            if evidence_eligible and daily_bar_complete:
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
            if evidence_eligible:
                states=self.population_state.apply(
                    market_id,
                    prepared["strategy_states"],
                    observation_key=f"DAILY:{snapshot.as_of}",
                    advance_observation=daily_bar_complete,
                )
            else:
                states=self.population_state.preview(
                    market_id,
                    prepared["strategy_states"],
                )
            request=RunRequest(
                market=snapshot,
                strategy_states=states,
                account=account,
                strategy_pool=strategy_pool,
                decision_context=DecisionContext(
                    market_id=market_id,
                    account_id=account_id,
                    strategy_pool_id=strategy_pool_id,
                    objective=account.objective,
                    capital_state={"capital":account.capital,"base_currency":account.base_currency},
                    risk_state={
                        "risk_budget":account.risk_budget,
                        "max_drawdown_constraint":account.max_drawdown_constraint,
                    },
                    cross_market_state={},
                ),
                max_group_size=min(profile.max_group_size,strategy_pool.max_group_size),
            )
            with self._lock:
                run=self._runs[run_id]
                run.previous_run_id=resolved[-1] if resolved else None
                self._save_run(run)
            self.execute(run_id,request)
            us_route_decision=None
            hk_route_decision=None
            if market_id=="US" and evidence_eligible:
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
            elif market_id=="HK" and evidence_eligible:
                completed_run=self.get_run(run_id)
                hk_route_decision=self.hk_return_max_ledger.by_snapshot(
                    snapshot.snapshot_id,
                    self.hk_return_max.version,
                )
                if hk_route_decision is None:
                    proposed_hk_route=self.hk_return_max.decide(
                        prepared["panel"],
                        completed_run.strategy_group,
                        completed_run.triaid_decision,
                        completed_run.strategy_states,
                        phase,
                    )
                    hk_route_decision=self.hk_return_max_ledger.freeze(
                        proposed_hk_route,
                        snapshot.snapshot_id,
                        snapshot.as_of,
                    )
                snapshot.metadata["hk_return_max_decision_id"]=hk_route_decision.get("decision_id")
                snapshot.metadata["hk_return_max_decision_hash"]=hk_route_decision.get("decision_hash")
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
                    "hk_return_max_decision_id":hk_route_decision.get("decision_id") if hk_route_decision else None,
                    "hk_return_max_decision_hash":hk_route_decision.get("decision_hash") if hk_route_decision else None,
                    "hk_return_max_decision_status":hk_route_decision.get("decision_status") if hk_route_decision else None,
                    "hk_return_max_outcome_recorded":bool((hk_return_outcome or {}).get("recorded")),
                    "daily_bar_complete":daily_bar_complete,
                    "evidence_state":(
                        "MANUAL_PREVIEW_NON_EVIDENCE"
                        if not evidence_eligible
                        else ("COMPLETE_DAILY" if daily_bar_complete else "PROVISIONAL_INTRADAY")
                    ),
                    "run_scope":run_scope,
                    "evidence_eligible":evidence_eligible,
                    "persistent_run_record":evidence_eligible,
                }
                if not evidence_eligible and run.status!="FAILED":
                    run.status="PREVIEW_READY"
                self._save_run(run)
                if not evidence_eligible:
                    self._prune_manual_previews()
        except Exception as exc:
            with self._lock:
                run=self._runs[run_id]
                run.status="FAILED"
                run.diagnostic_summary={"error":f"{type(exc).__name__}:{exc}"}
                self._save_run(run)

    def market_data_auction_shadow_probe(self,market_id:str)->dict:
        market=market_id.upper()
        symbols=MARKETS[market].risk_assets if market in MARKETS else ()
        return market_data_auction_shadow_probe(market,symbols)

    def alpha_evidence_status(self)->dict:
        return self.alpha_evidence.status()

    def alpha_evidence_rows(self,market_id:str|None=None,limit:int=200)->list[dict]:
        return self.alpha_evidence.rows(market_id,limit)

    def execution_calibration_status(self)->dict:
        return ExecutionCalibration.status(self.us_return_max_ledger,self.recovery_wave_ledger)

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

    def hk_return_max_status(self)->dict:
        return self.hk_return_max_ledger.status()

    def latest_hk_return_max_decision(self)->dict|None:
        return self.hk_return_max_ledger.latest()

    def hk_return_max_history(self,limit:int=100)->list[dict]:
        return self.hk_return_max_ledger.decisions(limit)

    def hk_return_max_daily_report(self)->dict|None:
        return self.hk_return_max_ledger.daily_report()

    def long_cycle_hypothesis_run(self,force:bool=False)->dict:
        return self.long_cycle_hypothesis.run(force=force)

    def long_cycle_hypothesis_latest(self)->dict|None:
        return self.long_cycle_hypothesis.latest()

    def long_cycle_hypothesis_history(self,limit:int=100)->list[dict]:
        return self.long_cycle_hypothesis.history(limit)

    def long_cycle_hypothesis_status(self)->dict:
        return self.long_cycle_hypothesis.status()

    def cross_market_crash_run(self,force:bool=False)->dict:
        return self.cross_market_crash.run(force=force)

    def cross_market_crash_latest(self)->dict|None:
        return self.cross_market_crash.latest()

    def cross_market_crash_history(self,limit:int=100)->list[dict]:
        return self.cross_market_crash.history(limit)

    def cross_market_crash_status(self)->dict:
        return self.cross_market_crash.status()

    def latent_hazard_run(self,force:bool=False)->dict:
        return self.latent_hazard.run(force=force)

    def latent_hazard_latest(self)->dict|None:
        return self.latent_hazard.latest()

    def latent_hazard_history(self,limit:int=100)->list[dict]:
        return self.latent_hazard.history(limit)

    def latent_hazard_status(self)->dict:
        return self.latent_hazard.status()

    def policy_curve_run(self,force:bool=False)->dict:
        return self.policy_curve.run(force=force)

    def policy_curve_latest(self)->dict|None:
        return self.policy_curve.latest()

    def policy_curve_history(self,limit:int=100)->list[dict]:
        return self.policy_curve.history(limit)

    def policy_curve_status(self)->dict:
        return self.policy_curve.status()

    def hazard_prospective_freeze(self,hazard_report:dict,policy_curve:dict|None=None)->dict:
        return self.hazard_prospective.freeze(hazard_report,policy_curve)

    def hazard_prospective_resolve(self)->dict:
        return self.hazard_prospective.resolve()

    def hazard_prospective_latest(self)->dict|None:
        return self.hazard_prospective.latest()

    def hazard_prospective_history(self,limit:int=100)->list[dict]:
        return self.hazard_prospective.rows(limit)

    def hazard_prospective_status(self)->dict:
        return self.hazard_prospective.status()

    def risk_warning_run(self,force:bool=False)->dict:
        return self.risk_warning.build(
            long_cycle=self.long_cycle_hypothesis.latest(),
            latent=self.latent_hazard.latest(),
            cross_market=self.cross_market_crash.latest(),
            policy_curve=self.policy_curve.latest(),
            prospective=self.hazard_prospective.latest(),
            force=force,
        )

    def risk_warning_latest(self)->dict|None:
        return self.risk_warning.latest()

    def risk_warning_history(self,limit:int=100)->list[dict]:
        return self.risk_warning.history(limit)

    def risk_warning_status(self)->dict:
        return self.risk_warning.status()

    def risk_control_run(self,force:bool=False)->dict:
        risk_warning=self.risk_warning.latest() or self.risk_warning_run(False)
        return self.risk_control.build(
            risk_warning=risk_warning,
            latent=self.latent_hazard.latest(),
            long_cycle=self.long_cycle_hypothesis.latest(),
            cross_market=self.cross_market_crash.latest(),
            policy_curve=self.policy_curve.latest(),
            prospective=self.hazard_prospective.latest(),
            market_data_status=market_data_status(),
            force=force,
        )

    def risk_control_latest(self)->dict|None:
        return self.risk_control.latest()

    def risk_control_history(self,limit:int=100)->list[dict]:
        return self.risk_control.history(limit)

    def risk_control_status(self)->dict:
        return self.risk_control.status()

    def prospective_experiment_status(self)->dict:
        return self.prospective_experiment.status()

    def prospective_experiments(self,limit:int=100)->list[dict]:
        return self.prospective_experiment.list(limit)

    def latest_prospective_experiment(self)->dict|None:
        return self.prospective_experiment.latest()

    def prospective_experiment_detail(self,experiment_id:str)->dict:
        return self.prospective_experiment.get(experiment_id)

    @staticmethod
    def primary_experiment_mode(market_id:str)->str:
        market=normalize_market_id(market_id)
        mode=str(MARKET_REGISTRY.get(market).metadata.get("primary_experiment_mode") or "").upper()
        if not mode:
            raise ValueError(f"primary experiment adapter not registered for market_id: {market}")
        return mode

    def latest_decision_run(
        self,
        market_id:str,
        primary_only:bool=True,
    )->RunRecord|None:
        market_id=market_id.upper()
        primary_mode=self.primary_experiment_mode(market_id) if primary_only else None
        rows=[
            r for r in self.all_runs()
            if r.market.market_id.upper()==market_id
            and r.strategy_group is not None
            and r.triaid_decision is not None
            and r.status in {"DECISION_READY_AWAITING_OUTCOME","VERIFIED"}
            and self._complete_daily_evidence_run(r)
            and (
                primary_mode is None
                or (
                    str((r.market.metadata or {}).get("experiment_mode") or "").upper()==primary_mode
                    and str((r.market.metadata or {}).get("primary_route_revision") or "")==self.architecture_version
                )
            )
        ]
        return rows[-1] if rows else None

    def run_live_research(self,market_id:str,account_id:str="GLOBAL")->RunRecord:
        run=self.create_pending_live_run(market_id,"OFFICIAL_EVIDENCE",account_id)
        self.execute_live(run.run_id,market_id,"OFFICIAL_EVIDENCE")
        return self.get_run(run.run_id)

    def ensure_primary_reference(self,market_id:str)->dict:
        market_id=market_id.upper()
        primary_mode=self.primary_experiment_mode(market_id)
        revision=self.architecture_version
        existing=self.latest_decision_run(market_id,primary_only=True)
        if existing is not None:
            return {
                "market_id":market_id,
                "primary_mode":primary_mode,
                "primary_route_revision":revision,
                "created":False,
                "run_id":existing.run_id,
                "status":existing.status,
                "as_of":existing.market.as_of,
                "snapshot_id":existing.market.snapshot_id,
                "superseded_run_ids":[],
                "reason":"PRIMARY_REFERENCE_ALREADY_PRESENT_FOR_REVISION",
            }

        run=self.run_live_research(market_id)
        reference=self.latest_decision_run(market_id,primary_only=True)
        if reference is None:
            return {
                "market_id":market_id,
                "primary_mode":primary_mode,
                "primary_route_revision":revision,
                "created":False,
                "run_id":run.run_id,
                "status":run.status,
                "as_of":run.market.as_of,
                "snapshot_id":run.market.snapshot_id,
                "superseded_run_ids":[],
                "reason":"PRIMARY_REFERENCE_NOT_EVIDENCE_READY",
            }

        superseded=[]
        superseded_at=datetime.now(ZoneInfo("UTC")).isoformat()
        with self._lock:
            stored=self._runs.get(reference.run_id)
            if stored is not None:
                stored.market.metadata=dict(stored.market.metadata or {})
                stored.market.metadata["primary_reference_bootstrap"]=True
                stored.market.metadata["primary_reference_bootstrap_reason"]="ROUTE_REVISION_MIGRATION_OR_EMPTY_PRIMARY_LEDGER"
                stored.market.metadata["primary_reference_mode"]=primary_mode
                stored.market.metadata["primary_route_revision"]=revision
                self._save_run(stored)
                reference=stored

            for old in self.all_runs():
                if old.run_id==reference.run_id:
                    continue
                metadata=old.market.metadata or {}
                if (
                    old.market.market_id.upper()==market_id
                    and str(metadata.get("experiment_mode") or "").upper()==primary_mode
                    and str(metadata.get("primary_route_revision") or "")!=revision
                    and old.status=="DECISION_READY_AWAITING_OUTCOME"
                    and (
                        old.evaluation is None
                        or str(old.evaluation.status or "")=="PENDING_OUTCOME"
                    )
                ):
                    old.status="SUPERSEDED"
                    old.market.metadata=dict(metadata)
                    old.market.metadata["primary_reference_superseded"]=True
                    old.market.metadata["primary_reference_superseded_by"]=reference.run_id
                    old.market.metadata["primary_reference_superseded_by_revision"]=revision
                    old.market.metadata["primary_reference_superseded_at"]=superseded_at
                    self._runs[old.run_id]=old
                    self._save_run(old)
                    superseded.append(old.run_id)

        return {
            "market_id":market_id,
            "primary_mode":primary_mode,
            "primary_route_revision":revision,
            "created":True,
            "run_id":reference.run_id,
            "status":reference.status,
            "as_of":reference.market.as_of,
            "snapshot_id":reference.market.snapshot_id,
            "superseded_run_ids":superseded,
            "reason":"PRIMARY_REFERENCE_CREATED_FOR_REVISION",
        }

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
            if run.status=="SUPERSEDED" or (run.market.metadata or {}).get("primary_reference_superseded") is True:
                raise ValueError("superseded_primary_reference_is_not_outcome_eligible")
            if not self._evidence_eligible_run(run):
                raise ValueError("manual_preview_is_not_evidence_eligible")
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
            self._save_run(run)
            return run

    def get_run(self,run_id:str)->RunRecord:
        with self._lock:
            if run_id in self._runs:
                return self._runs[run_id]
        return self.store.load_run(run_id)

    def all_runs(self)->List[RunRecord]:
        with self._lock:
            return sorted(self._runs.values(),key=lambda r:r.created_at)

    def latest_run(
        self,
        market_id:str|None=None,
        primary_only:bool=True,
    )->RunRecord|None:
        rows=[r for r in self.all_runs() if self._evidence_eligible_run(r)]
        if market_id:
            rows=[r for r in rows if r.market.market_id.upper()==market_id.upper()]
        if primary_only:
            rows=[
                r for r in rows
                if str((r.market.metadata or {}).get("experiment_mode") or "").upper()
                == self.primary_experiment_mode(str(r.market.market_id).upper())
                and str((r.market.metadata or {}).get("primary_route_revision") or "")==self.architecture_version
                and r.status!="SUPERSEDED"
            ]
        useful=[r for r in rows if r.market.snapshot_id!="PENDING"]
        return useful[-1] if useful else (rows[-1] if rows else None)

    def status(self)->dict:
        runs=self.all_runs()
        counts:Dict[str,int]={}
        for run in runs:
            counts[run.status]=counts.get(run.status,0)+1
        return {
            "architecture_version":self.architecture_version,
            "module_manifest":self.module_manifest,
            "storage":self.store.status(),
            "active_core":self.evolution.active().__dict__,
            "active_strategy_rules":{
                market_id:self.strategy_evolution.active(market_id).__dict__
                for market_id in market_ids()
            },
            "strategy_registry_count":len(self.strategy_population.definitions()),
            "markets":list(market_ids()),
            "evidence_markets":list(evidence_market_ids()),
            "market_registry":MARKET_REGISTRY.snapshot(),
            "account_registry":self.account_registry.snapshot(),
            "run_counts":counts,
            "run_scope_counts":{
                "official_evidence":sum(1 for r in runs if self._evidence_eligible_run(r)),
                "manual_preview_in_memory":sum(
                    1 for r in runs
                    if str((r.market.metadata or {}).get("run_scope") or "")=="MANUAL_PREVIEW"
                ),
            },
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

    def volatility_forecast(self,market_id:str)->dict:
        return cached_volatility_forecast(self.store,market_id)

    def volatility_forecasts(self)->dict:
        return cached_all_market_volatility_forecasts(self.store)

    def refresh_volatility_forecast(self,market_id:str)->dict:
        return refresh_market_volatility_forecast(self.store,market_id)

    def refresh_volatility_forecasts(self,require_all:bool=True)->dict:
        return refresh_all_market_volatility_forecasts(self.store,require_all)

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

    def daily_summary(self,market_id:str|None=None,compact:bool=False)->dict:
        rows=self.all_runs()
        if market_id:
            rows=[r for r in rows if r.market.market_id.upper()==market_id.upper()]
        summary=self.review.daily_summary(rows)
        include_us=(market_id is None) or market_id.upper()=="US"
        if include_us:
            us_return=self.us_return_max_ledger.daily_report()
            if us_return:
                summary["us_return_max"]=us_return
            if not compact:
                long_cycle=self.long_cycle_hypothesis.latest()
                if long_cycle:
                    summary["long_cycle_hypothesis"]=long_cycle
                crash_linkage=self.cross_market_crash.latest()
                if crash_linkage:
                    summary["cross_market_crash"]=crash_linkage
                latent=self.latent_hazard.latest()
                if latent:
                    summary["latent_hazard"]=latent
                policy_curve=self.policy_curve.latest()
                if policy_curve:
                    summary["policy_expectation_curve"]=policy_curve
                hazard_shadow=self.hazard_prospective.latest()
                if hazard_shadow:
                    summary["hazard_prospective"]=hazard_shadow
                risk_warning=self.risk_warning.latest()
                if risk_warning:
                    summary["risk_warning"]=risk_warning
                risk_control=self.risk_control.latest()
                if risk_control:
                    summary["risk_control"]=risk_control
        include_hk=(market_id is None) or market_id.upper()=="HK"
        if include_hk:
            hk_return=self.hk_return_max_ledger.daily_report()
            if hk_return:
                summary["hk_return_max"]=hk_return
        include_cn=(market_id is None) or market_id.upper()=="CN"
        if include_cn:
            recovery=self.recovery_wave_ledger.daily_report("CN")
            if recovery:
                summary["recovery_wave"]=recovery
            summary["prospective_experiment_status"]=self.prospective_experiment.status()
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
        if (
            manifest is None
            or manifest.get("evidence_scope")!="PRIMARY_ROUTE_ONLY"
            or not candidate.parent_version
        ):
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
            and str(r.market.market_id).upper() in set(evidence_market_ids())
            and str((r.market.metadata or {}).get("experiment_mode") or "").upper()
                == self.primary_experiment_mode(str(r.market.market_id).upper())
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
                for market in evidence_market_ids()
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

        replay_pass=all(nondegrading(dev_by_market[m],1) for m in evidence_market_ids())
        holdout_pass=all(nondegrading(holdout_by_market[m],1) for m in evidence_market_ids())
        shadow_min_per_market=5
        shadow_pass=all(nondegrading(shadow_by_market[m],shadow_min_per_market) for m in evidence_market_ids())
        audit_pass=bool(
            0.0<=candidate.intervention_strength<=1.0
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
            "evidence_scope":"PRIMARY_ROUTE_ONLY",
            "objective":"MAXIMIZE_REALIZABLE_NET_RETURN",
            "validation_discipline":"PRIMARY_ROUTE_ONLY; INTERNAL_MARKET_STRATIFIED_REPLAY_RESERVED_HOLDOUT_AND_POST_CREATION_SHADOW; NO_CROSS_MARKET_MASKING",
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
            for market_id in market_ids()
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
