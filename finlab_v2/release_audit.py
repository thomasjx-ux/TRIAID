from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent
MODE=(sys.argv[1] if len(sys.argv)>1 else "build").strip().lower()
RECEIPT_PATH=Path(os.getenv(
    "TRIAID_RELEASE_AUDIT_RECEIPT_PATH",
    "/tmp/triaid_release_audit.json" if MODE=="runtime" else "/tmp/triaid_build_audit.json",
))
BASE=os.getenv("TRIAID_POSTDEPLOY_SMOKE_BASE","http://127.0.0.1:8080").rstrip("/")

BUILD_CASES=[
    "selftest.py",
    "unified_constitution_smoke.py",
    "backend_audit_regression.py",
    "manual_preview_isolation_smoke.py",
    "bootstrap_persistence_guard_smoke.py",
    "startup_readonly_contract_smoke.py",
    "primary_revision_migration_smoke.py",
    "strategy_contract_smoke.py",
    "policy_triage_smoke.py",
    "transition_triage_gate_smoke.py",
    "preopen_baseline_freshness_smoke.py",
    "n_market_multi_account_smoke.py",
    "external_strategy_module_smoke.py",
    "homepage_swr_cache_smoke.py",
    "economic_evolution_smoke.py",
    "value_frontier_smoke.py",
    "trader_shadow_smoke.py",
    "provider_adjustment_smoke.py",
    "provider_freshness_smoke.py",
    "tushare_auction_smoke.py",
    "close_transition_regression.py",
    "prospective_experiment_smoke.py",
    "recovery_wave_smoke.py",
    "capital_capacity_smoke.py",
    "us_return_max_smoke.py",
    "hk_return_max_smoke.py",
    "hk_full_alignment_smoke.py",
    "adaptive_alpha_promotion_smoke.py",
    "core_evolution_validation_smoke.py",
    "strategy_evolution_validation_smoke.py",
    "daily_report_change_attribution_smoke.py",
    "daily_report_module_smoke.py",
    "daily_experiment_intelligence_smoke.py",
    "long_cycle_hypothesis_smoke.py",
    "cross_market_crash_smoke.py",
    "latent_hazard_smoke.py",
    "policy_curve_smoke.py",
    "hazard_prospective_smoke.py",
    "risk_warning_smoke.py",
    "risk_control_smoke.py",
    "volatility_forecast_smoke.py",
    "volatility_explainability_smoke.py",
    "volatility_persistent_cache_smoke.py",
    "hk_market_smoke.py",
    "hk_high_frequency_degradation_smoke.py",
    "ui_smoke.py",
    "market_switch_fastpath_smoke.py",
    "home_first_paint_smoke.py",
    "live_phase_semantics_smoke.py",
    "market_ui_readability_smoke.py",
    "ui_table_contract_smoke.py",
    "ui_copy_quality_smoke.py",
    "information_architecture_smoke.py",
    "all_market_page_layout_smoke.py",
    "compact_status_overview_smoke.py",
    "empty_posterior_layout_smoke.py",
    "all_table_surface_smoke.py",
    "us_page_live_surface_smoke.py",
    "ui_projection_smoke.py",
    "modular_interface_architecture_smoke.py",
    "capability_ports_evidence_smoke.py",
    "outcome_resolver_smoke.py",
    "validation_projection_smoke.py",
    "runtime_plugin_smoke.py",
    "cn_prospective_route_contract_smoke.py",
    "rendered_home_js_smoke.py",
    "calendar_sync_config_smoke.py",
    "runtime_env_config_smoke.py",
    "production_smoke_contract_static.py",
    "deployment_reproducibility_smoke.py",
    "manual_preview_guard_smoke.py",
    "hk_tencent_5m_aggregation_smoke.py",
    "storage_runtime_fence_smoke.py",
]

RUNTIME_BOOTSTRAPS=[
    "volatility_forecast_live_bootstrap.py",
    "hk_market_live_bootstrap.py",
    "policy_hazard_live_bootstrap.py",
]

RUNTIME_REQUIRED_PATHS=[
    "/health/live",
    "/api/status",
    "/api/audit/status",
    "/api/storage/status",
    "/api/market-data/status",
    "/api/market-data/registry",
    "/api/accounts/status",
    "/api/external-strategies/status",
    "/api/trader-shadow/status",
    "/api/system/interfaces",
    "/api/ui/market-clocks",
    "/api/ui/home-brief",
    "/api/evolution/economic-value?market_id=US",
    "/api/evolution/economic-value?market_id=CN",
    "/api/evolution/economic-value?market_id=HK",
    "/api/ui/market-page/US?lang=zh",
    "/api/ui/market-page/CN?lang=zh",
    "/api/ui/market-page/HK?lang=zh",
    "/api/ui/validation-summary?market_id=US",
    "/api/ui/validation-summary?market_id=CN",
    "/api/ui/validation-summary?market_id=HK",
    "/api/experiments/evidence/US/latest",
    "/api/experiments/evidence/CN/latest",
    "/api/experiments/evidence/HK/latest",
    "/api/experiments/outcomes/US/status",
    "/api/experiments/outcomes/CN/status",
    "/api/experiments/outcomes/HK/status",
    "/api/experiments/outcomes/US/latest",
    "/api/experiments/outcomes/CN/latest",
    "/api/experiments/outcomes/HK/latest",
    "/api/ui/market-page/US/live",
    "/api/ui/market-page/CN/live",
    "/api/ui/market-page/HK/live",
    "/api/ui/risk-center",
    "/api/volatility-forecast",
    "/api/risk-warning/latest",
    "/api/risk-control/latest",
    "/api/strategy-population/rules/US",
    "/api/strategy-population/rules/CN",
    "/api/strategy-population/rules/HK",
    "/api/decision-scheduler/events?market_id=US&limit=1",
    "/api/decision-scheduler/events?market_id=US&limit=120",
    "/api/decision-scheduler/events?market_id=CN&limit=1",
    "/api/decision-scheduler/events?market_id=HK&limit=1",
    "/api/decision-scheduler/status",
    "/api/daily?compact=true&market_id=US",
    "/api/daily?compact=true&market_id=CN",
    "/api/daily?compact=true&market_id=HK",
    "/api/ui/daily-report?compact=true",
    "/api/strategies?market_id=US&lang=zh",
    "/api/strategies?market_id=CN&lang=zh",
    "/api/strategies?market_id=HK&lang=zh",
    "/api/curves?market_id=US",
    "/api/curves?market_id=CN",
    "/api/curves?market_id=HK",
    "/api/market-data/live-indicators/US",
    "/api/market-data/live-indicators/CN",
    "/api/market-data/live-indicators/HK",
    "/api/experiments/cn/prospective/status",
    "/",
]

def write_receipt(payload:dict)->None:
    RECEIPT_PATH.parent.mkdir(parents=True,exist_ok=True)
    tmp=RECEIPT_PATH.with_suffix(RECEIPT_PATH.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True),encoding="utf-8")
    tmp.replace(RECEIPT_PATH)

def run_case(script:str)->dict:
    path=ROOT/script
    if not path.exists():
        return {"name":script,"passed":False,"returncode":127,"error":"MISSING_AUDIT_SCRIPT"}
    started=time.monotonic()
    cp=subprocess.run(
        [sys.executable,str(path)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    elapsed=round(time.monotonic()-started,3)
    return {
        "name":script,
        "passed":cp.returncode==0,
        "returncode":cp.returncode,
        "elapsed_seconds":elapsed,
        "stdout_tail":"\n".join(cp.stdout.splitlines()[-8:]),
        "stderr_tail":"\n".join(cp.stderr.splitlines()[-8:]),
    }

def structural_checks()->list[dict]:
    rows=[]
    def check(name:str,condition:bool,detail=None):
        rows.append({"name":name,"passed":bool(condition),"detail":detail})
    start=(ROOT/"start.sh").read_text(encoding="utf-8")
    gate=(ROOT/"build_gate.sh").read_text(encoding="utf-8")
    app=(ROOT/"app.py").read_text(encoding="utf-8")
    engine=(ROOT/"triaid_fin"/"engine.py").read_text(encoding="utf-8")
    scheduler=(ROOT/"triaid_fin"/"decision_scheduler.py").read_text(encoding="utf-8")
    projection=(ROOT/"triaid_fin"/"ui_projection.py").read_text(encoding="utf-8")
    runtime=(ROOT/"triaid_fin"/"market_runtime.py").read_text(encoding="utf-8")
    interfaces=(ROOT/"triaid_fin"/"market_interfaces.py").read_text(encoding="utf-8")
    market_contracts=(ROOT/"triaid_fin"/"market_contracts.py").read_text(encoding="utf-8")
    market_profiles=(ROOT/"triaid_fin"/"market_profiles"/"__init__.py").read_text(encoding="utf-8")
    risk_projection=(ROOT/"triaid_fin"/"risk_projection.py").read_text(encoding="utf-8")
    runtime_ports=(ROOT/"triaid_fin"/"runtime_ports.py").read_text(encoding="utf-8")
    ui_ports=(ROOT/"triaid_fin"/"ui_ports.py").read_text(encoding="utf-8")
    daily_report=(ROOT/"triaid_fin"/"daily_report.py").read_text(encoding="utf-8")
    economic_evolution=(ROOT/"triaid_fin"/"economic_evolution.py").read_text(encoding="utf-8")
    value_frontier=(ROOT/"triaid_fin"/"value_frontier.py").read_text(encoding="utf-8")
    projection_cache=(ROOT/"triaid_fin"/"projection_cache.py").read_text(encoding="utf-8")
    external_strategy=(ROOT/"triaid_fin"/"external_strategy.py").read_text(encoding="utf-8")
    external_strategy_api=(ROOT/"triaid_fin"/"external_strategy_api.py").read_text(encoding="utf-8")
    trader_shadow=(ROOT/"triaid_fin"/"trader_shadow.py").read_text(encoding="utf-8")
    trader_shadow_api=(ROOT/"triaid_fin"/"trader_shadow_api.py").read_text(encoding="utf-8")
    strategy_interfaces=(ROOT/"triaid_fin"/"strategy_interfaces.py").read_text(encoding="utf-8")
    contracts=(ROOT/"triaid_fin"/"contracts.py").read_text(encoding="utf-8")
    global_constitution=(ROOT/"triaid_constitution.py").read_text(encoding="utf-8")
    finance_objective=(ROOT/"triaid_fin"/"objective.py").read_text(encoding="utf-8")
    constitution_doc=(ROOT.parent/"TRIAID_CONSTITUTION.md").read_text(encoding="utf-8")
    projection_repository=(ROOT/"triaid_fin"/"projection_repository.py").read_text(encoding="utf-8")
    outcome_resolver=(ROOT/"triaid_fin"/"outcome_resolver.py").read_text(encoding="utf-8")
    validation_projection=(ROOT/"triaid_fin"/"validation_projection.py").read_text(encoding="utf-8")
    home_brief_source=(ROOT/"triaid_fin"/"home_brief.py").read_text(encoding="utf-8")
    projection_cache_source=(ROOT/"triaid_fin"/"projection_cache.py").read_text(encoding="utf-8")
    post=(ROOT/"postdeploy_runtime_smoke.py").read_text(encoding="utf-8")
    check(
        "unified_value_constitution_is_release_blocking",
        "MAXIMIZE_LONG_HORIZON_REALIZABLE_EVIDENCE_SUPPORTED_VALUE" in global_constitution
        and "DEFENSIVENESS_IS_NOT_SUCCESS_BY_ITSELF" in global_constitution
        and "INACTION_HAS_OPPORTUNITY_COST_AND_REQUIRES_EVIDENCE" in global_constitution
        and "objective_class_override_allowed" in global_constitution
        and "inherits_global_constitution" in finance_objective
        and "defensiveness_is_terminal_objective" in finance_objective
        and "TRIAID Constitution — Unified Value Maximization Discipline" in constitution_doc
        and "Anti-inaction rule" in constitution_doc,
        None,
    )
    check("build_gate_single_orchestrator","release_audit.py build" in gate,gate)
    check(
        "first_paint_market_brief_is_memory_only",
        "class HomeBriefProjection" in home_brief_source
        and "no_daily_report_rebuild" in home_brief_source
        and "no_outcome_resolution" in home_brief_source
        and '@app.get("/api/ui/home-brief")' in app
        and "refreshHomeBrief()" in app,
        None,
    )
    check(
        "full_market_cache_and_lazy_initial_load",
        "class ReadThroughProjectionCache" in projection_cache_source
        and "ui_projection_cache.peek(" in app
        and "ui_projection_cache.refresh(" in app
        and "STALE_WHILE_REVALIDATE" in app
        and "soft_ttl_seconds" in app
        and "setInterval(refreshAll,15000)" not in app
        and "setTimeout(warmAllMarkets,1200)" not in app
        and "setInterval(refreshFullIfDue,15000)" in app
        and "setInterval(refreshLiveIfDue,5000)" in app
        and 'copy_mode="shallow_top"' in app
        and "setTimeout(()=>{if(!document.hidden)refreshAll(true)},1400)" in app,
        None,
    )
    check("policy_triage_integrated","PolicyTriageModule" in engine and "\"policy_triage\"" in engine)
    check("risk_increase_requires_persistence","INTRADAY_RISK_INCREASE_REQUIRES_CONFIRMED_STATE_CHANGE" in scheduler)
    check("preopen_baseline_freshness_guard","PREOPEN_BASELINE_STALE" in scheduler and "baseline_expected_as_of" in scheduler and "baseline_reference_as_of" in scheduler)
    check("runtime_audit_is_blocking","release_audit.py runtime" in start and "wait \"$SERVER_PID\"" in start,start)
    check("critical_audit_not_echo_only","TRIAID_POSTDEPLOY_RUNTIME_SMOKE_FAILED" not in start and "TRIAID_RISK_CENTER_FULL_AUDIT_FAILED" not in start,start)
    check("liveness_endpoint_present",'@app.get("/health/live")' in app,None)
    check("readiness_audit_gate_present","TRIAID_RELEASE_AUDIT_REQUIRED" in app and "release_audit" in app,None)
    check("audit_status_api_present",'@app.get("/api/audit/status")' in app,None)
    check("railway_git_identity_contract","RAILWAY_GIT_COMMIT_SHA" in app and "identity_verified" in app,None)
    check("runtime_smoke_uses_liveness",'/health/live' in post,None)
    check(
        "market_page_projection_is_single_ui_contract",
        "class MarketPageProjection" in projection
        and "NO_UNEXPLAINED_EMPTY_SURFACES" in projection
        and '@app.get("/api/ui/market-page/{market_id}")' in app,
        None,
    )
    check(
        "market_interfaces_are_registry_driven",
        "MarketInterfaceRegistry" in interfaces
        and "builtin_profiles" in interfaces
        and "class MarketInterfaceRegistry" in market_contracts
        and "provider_chains" in market_contracts
        and "runtime_jobs" in market_contracts,
        None,
    )
    check(
        "market_profiles_are_plugin_loaded",
        all(
            token in market_profiles
            for token in ("build_us_profile","build_cn_profile","build_hk_profile")
        )
        and all(
            token not in interfaces
            for token in (
                "US_ROUTE=",
                "CN_ROUTE=",
                "HK_ROUTE=",
                'market_id="US"',
                'market_id="CN"',
                'market_id="HK"',
            )
        ),
        None,
    )
    check(
        "runtime_orchestration_uses_ports_and_plugins",
        "self.services." in runtime
        and "self.engine." not in runtime
        and "_run_registered_jobs" in runtime
        and 'market_id=="US"' not in runtime
        and 'market_id=="CN"' not in runtime
        and 'market_id=="HK"' not in runtime,
        None,
    )
    check(
        "scheduler_uses_runtime_services_port",
        "self.services." in scheduler and "self.engine." not in scheduler,
        None,
    )
    check(
        "runtime_capability_ports_present",
        all(
            token in runtime_ports
            for token in (
                "class MarketDataRuntimePort",
                "class DecisionRuntimePort",
                "class ResearchRuntimePort",
            )
        )
        and "self.services.market_data." in runtime
        and "self.services.decision." in scheduler
        and "self.services.market_data." in scheduler,
        None,
    )
    check(
        "account_strategy_runs_are_hard_isolated",
        "global_route_account" in engine
        and '"ACCOUNT_STRATEGY_POOL"' in engine
        and '"GLOBAL_ROUTE_LEDGER_WRITE_BLOCKED"' in engine
        and "all_runs_provider=self.global_runs" in engine
        and "rows=self.global_runs()" in engine
        and "def global_runs" in engine
        and "return self._engine.global_runs()" in ui_ports
        and "global_route_account and market_id==\"US\"" in engine
        and "global_route_account and market_id==\"HK\"" in engine,
        None,
    )
    check(
        "external_strategy_isolation_boundary",
        "class ExternalStrategyModule" in external_strategy
        and '"QUARANTINE"' in external_strategy
        and '"SHADOW"' in external_strategy
        and '"ACTIVE"' in external_strategy
        and "states_for_account" in external_strategy
        and "QUARANTINE_OR_SHADOW_NEVER_RECEIVES_CAPITAL" in external_strategy
        and "class ExternalStrategySpec" in external_strategy
        and "class ExternalStrategyObservation" in external_strategy
        and "build_external_strategy_router" in external_strategy_api
        and "build_external_strategy_router(engine)" in app
        and "max_strategy_weight" in contracts
        and '"external_strategy"' in engine,
        None,
    )
    check(
        "trader_shadow_is_minimal_automated_and_isolated",
        "class TraderShadowModule" in trader_shadow
        and "TRIAID_ASSISTED" in trader_shadow
        and "TRIAID_AUTO" in trader_shadow
        and "FULL_MARKET_AUTO_DEFAULT" in trader_shadow
        and "capital_optional" in trader_shadow
        and "formal_next_period_outcome_auto_resolved" in trader_shadow
        and "global_evidence_mutation" in trader_shadow
        and "build_trader_shadow_router" in trader_shadow_api
        and "build_trader_shadow_router(engine)" in app
        and "trader_shadow.resolve_market" in engine
        and '"trader_shadow"' in engine,
        None,
    )
    check(
        "open_strategy_interface_catalog_contract",
        "class StrategyInterfaceCatalog" in strategy_interfaces
        and "fixed_pool_is_not_the_boundary" in strategy_interfaces
        and "unknown_strategy_can_enter_via_trader_custom_adapter" in strategy_interfaces
        and "no_fabricated_strategy_state" in strategy_interfaces
        and "class TraderCustomStrategyRegistration" in trader_shadow
        and "class TraderCustomStrategyObservation" in trader_shadow
        and "register_custom_strategy" in trader_shadow
        and "observe_custom_strategy" in trader_shadow
        and "FIRST_OBSERVATION_AUTO_PROMOTES_QUARANTINE_TO_SHADOW_ONLY" in trader_shadow
        and "allow_shadow_simulation=True" in trader_shadow
        and '@router.post("/custom-strategies")' in trader_shadow_api
        and '@router.post("/custom-strategies/observe")' in trader_shadow_api
        and '"strategy_interface_catalog"' in engine,
        None,
    )
    check(
        "homepage_market_projection_uses_swr_fastpath",
        "def _schedule_market_page_refresh" in app
        and "STALE_WHILE_REVALIDATE" in app
        and "ui_projection_cache.peek" in app
        and "ui_projection_cache.refresh" in app
        and "soft_ttl_seconds" in app
        and "def peek" in projection_cache
        and "def refresh" in projection_cache,
        None,
    )
    check(
        "value_frontier_candidate_is_return_first_and_shadow_only",
        "class ValueFrontierAllocator" in value_frontier
        and "MAXIMIZE_REALIZABLE_NET_RETURN_SUBJECT_TO_HARD_CONSTRAINTS" in value_frontier
        and "GREEDY_NET_RETURN_RANK_WITH_POSITION_CAP_AND_RISK_BUDGET" in economic_evolution
        and "uses_frozen_t0_information_only" in economic_evolution
        and "reads_realized_t1_to_choose_weights" in economic_evolution
        and "SHADOW_ONLY_PENDING_PROSPECTIVE_VALIDATION" in economic_evolution
        and '"production_core_changed": False' in economic_evolution,
        None,
    )
    check(
        "economic_evolution_is_read_only_and_cost_aligned",
        "class EconomicEvolutionModule" in economic_evolution
        and "PER_ROUTE_FULL_WEIGHT_TURNOVER_TIMES_FROZEN_BASE_COST_BPS" in economic_evolution
        and "BEST_FIXED_SINGLE_AND_CAPPED_SINGLE_DIAGNOSTICS_NOT_BCRP_OPTIMIZER" in economic_evolution
        and "PROSPECTIVE_SHADOW_HOLDOUT_VS_UNCHANGED_PARENT" in economic_evolution
        and "EconomicEvolutionModule()" in daily_report
        and 'summary["trading_analysis"]["economic_evolution"]' in daily_report
        and '@app.get("/api/evolution/economic-value")' in app,
        None,
    )
    check(
        "daily_report_is_registry_driven_module",
        "class DailyReportModule" in daily_report
        and "self.market_ids()" in daily_report
        and "MARKET_REGISTRY_DRIVEN_NO_SILENT_OMISSION" in daily_report
        and "class DailyReportReadPort" in ui_ports
        and '"daily_report"' in engine
        and '@app.get("/api/ui/daily-report")' in app,
        None,
    )
    check(
        "daily_report_is_trading_analysis_first",
        "TRIAID_TRADING_ANALYSIS_DAILY" in daily_report
        and "_compose_trading_analysis" in daily_report
        and '"operations_in_body":False' in daily_report.replace(" ","")
        and "triaid_intervention" in daily_report
        and "realized_profit_analysis" in daily_report
        and "opportunity_cost" in daily_report,
        None,
    )
    check(
        "daily_report_is_market_phase_aware",
        "_content_profile" in daily_report
        and "MARKET_LOCAL_CALENDAR_AND_SESSION_PHASE_CONTROL_REPORT_CONTENT" in daily_report
        and "FORMAL_COMPLETED_SESSION_CUTOFFS_ONLY" in daily_report
        and "formal_and_live_layers_separated" in daily_report
        and "same_clock_time_does_not_imply_same_market_maturity" in daily_report,
        None,
    )
    check(
        "ui_capability_ports_present",
        "class MarketPageReadPort" in ui_ports
        and "class RiskReadPort" in ui_ports
        and "MarketPageReadPort" in projection
        and "RiskReadPort" in risk_projection,
        None,
    )
    check(
        "formal_evidence_repository_is_future_blind",
        "class VerifiedProjectionRepository" in projection_repository
        and "future_information_excluded" in projection_repository
        and "FORMAL_EVIDENCE_EXCLUDES_REALIZED_AND_INTRADAY_FUTURE_INFORMATION" in projection_repository
        and "verified_projection_repository" in app,
        None,
    )
    check(
        "t0_t1_outcome_resolver_is_thin",
        "class OutcomeResolver" in outcome_resolver
        and "does not recalculate market returns" in outcome_resolver
        and "outcome_resolver=OutcomeResolver" in app
        and '@app.get("/api/experiments/outcomes/{market_id}/status")' in app,
        None,
    )
    check(
        "validation_summary_is_single_ui_projection",
        "class ValidationSummaryProjection" in validation_projection
        and "does not recompute returns" in validation_projection
        and '@app.get("/api/ui/validation-summary")' in app
        and "/api/ui/validation-summary?market_id=" in app
        and "/api/experiments/outcomes/" not in app[
            app.find("async function refreshValidationSummary"):
            app.find("function phaseText")
        ],
        None,
    )
    check(
        "ui_projections_use_read_service_port",
        "self.services." in projection
        and "self.engine." not in projection
        and "self.services." in risk_projection
        and "self.engine." not in risk_projection
        and "ui_read_services=UiReadServices(engine)" in app,
        None,
    )
    check(
        "risk_center_projection_is_single_ui_contract",
        "class RiskCenterProjection" in risk_projection
        and '@app.get("/api/ui/risk-center")' in app
        and "jsonCachedStale('/api/ui/risk-center',10000)" in app,
        None,
    )
    refresh_start=app.find("async function refreshAll(preferStale=false,forceServer=false)")
    refresh_block=app[refresh_start:refresh_start+18000] if refresh_start>=0 else ""
    check(
        "market_page_frontend_no_legacy_multi_api_fanout",
        "projectionUrl=" in refresh_block
        and "const page=await marketGet(projectionUrl,30000);" in refresh_block
        and all(token not in refresh_block for token in (
            "/api/daily?compact=true&market_id=",
            "/api/strategies?market_id=",
            "/api/curves?market_id=",
            "/api/runs?market_id=",
            "jsonCachedStale('/api/evolution'",
            "json('/api/runs/'+encodeURIComponent(previewId)",
        )),
        None,
    )
    gateway=ROOT.parent/"gateway"/"gateway.py"
    if gateway.exists():
        gateway_text=gateway.read_text(encoding="utf-8")
        check("gateway_risk_quality_collapse_contract","RISK_QUALITY_OPEN_FIXED" in gateway_text and "quality_collapsed" in gateway_text,None)
        check("gateway_home_cache_bypass_contract",'Cache-Control","no-store, no-cache, must-revalidate, max-age=0"' in gateway_text,None)
    else:
        check("gateway_contract_out_of_scope",True,{"state":"NOT_IN_RUNTIME_BUILD_CONTEXT","path":str(gateway)})
    check("build_case_manifest_unique",len(BUILD_CASES)==len(set(BUILD_CASES)),BUILD_CASES)
    missing=[x for x in BUILD_CASES if not (ROOT/x).exists()]
    check("all_manifest_scripts_exist",not missing,missing)
    return rows

def http_get(path:str,timeout:float=20.0):
    req=urllib.request.Request(
        BASE+path,
        headers={"accept":"application/json","user-agent":"TRIAID-RELEASE-AUDIT/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req,timeout=timeout) as response:
        raw=response.read()
        ctype=response.headers.get("content-type") or ""
        text=raw.decode("utf-8","replace")
        if "json" in ctype.lower():
            return response.status,json.loads(text) if text else None
        return response.status,text

def wait_liveness(timeout_seconds:float=120.0):
    deadline=time.monotonic()+timeout_seconds
    last=None
    while time.monotonic()<deadline:
        try:
            code,payload=http_get("/health/live",timeout=3)
            if code==200 and isinstance(payload,dict) and payload.get("ok") is True:
                return payload
            last=f"{code}:{payload}"
        except Exception as exc:
            last=f"{type(exc).__name__}:{exc}"
        time.sleep(1)
    raise RuntimeError(f"LIVENESS_TIMEOUT:{last}")

def runtime_checks()->list[dict]:
    rows=[]
    def check(name:str,condition:bool,detail=None):
        rows.append({"name":name,"passed":bool(condition),"detail":detail})

    live=wait_liveness()
    check("process_liveness",live.get("ok") is True,live)

    maintenance_deadline=time.monotonic()+120
    maintenance=live.get("startup_maintenance_receipt") or {}
    while maintenance.get("state") not in {"COMPLETED","FAILED","DISABLED"} and time.monotonic()<maintenance_deadline:
        time.sleep(1)
        _,live=http_get("/health/live",timeout=5)
        maintenance=(live or {}).get("startup_maintenance_receipt") or {}
    check("startup_maintenance_complete",maintenance.get("state") in {"COMPLETED","DISABLED"},maintenance)

    for script in RUNTIME_BOOTSTRAPS:
        result=run_case(script)
        rows.append({"name":"runtime_bootstrap:"+script,"passed":result["passed"],"detail":result})

    for risk_path in ("/api/risk-warning/latest","/api/risk-control/latest"):
        risk_deadline=time.monotonic()+180
        while time.monotonic()<risk_deadline:
            try:
                code,_=http_get(risk_path,timeout=10)
                if code==200:
                    break
            except Exception:
                pass
            time.sleep(2)

    payloads={}
    for path in RUNTIME_REQUIRED_PATHS:
        try:
            code,payload=http_get(path)
            payloads[path]=payload
            check("http:"+path,code==200,{"status":code})
        except Exception as exc:
            check("http:"+path,False,f"{type(exc).__name__}:{exc}")

    status=payloads.get("/api/status") or {}
    storage=payloads.get("/api/storage/status") or {}
    market_status=payloads.get("/api/market-data/status") or {}
    market_registry=payloads.get("/api/market-data/registry") or {}
    account_registry=payloads.get("/api/accounts/status") or {}
    external_strategy_status=payloads.get("/api/external-strategies/status") or {}
    trader_shadow_status=payloads.get("/api/trader-shadow/status") or {}
    interface_status=payloads.get("/api/system/interfaces") or {}
    market_clocks=payloads.get("/api/ui/market-clocks") or {}
    home_brief=payloads.get("/api/ui/home-brief") or {}
    scheduler_status=payloads.get("/api/decision-scheduler/status") or {}
    volatility_forecast=payloads.get("/api/volatility-forecast") or {}
    risk_warning=payloads.get("/api/risk-warning/latest") or {}
    risk_control=payloads.get("/api/risk-control/latest") or {}
    risk_projection_payload=payloads.get("/api/ui/risk-center") or {}
    daily_report_payload=payloads.get("/api/ui/daily-report?compact=true") or {}
    economic_payloads={
        market:payloads.get(f"/api/evolution/economic-value?market_id={market}") or {}
        for market in ("US","CN","HK")
    }
    validation_payloads={
        market:payloads.get(f"/api/ui/validation-summary?market_id={market}") or {}
        for market in ("US","CN","HK")
    }
    home=payloads.get("/") or ""

    risk_projection_sections=risk_projection_payload.get("sections") or {}
    risk_projection_integrity=risk_projection_payload.get("integrity") or {}
    check(
        "risk_center_projection_contract",
        risk_projection_payload.get("contract_version")=="risk-center-projection@1.0.0"
        and risk_projection_payload.get("projection_scope")=="RISK_CENTER"
        and risk_projection_integrity.get("passed") is True,
        risk_projection_payload,
    )
    check(
        "risk_center_projection_sections_present",
        {"warning","control"}.issubset(set(risk_projection_sections)),
        sorted(risk_projection_sections),
    )

    interface_markets=(
        ((interface_status.get("market_interfaces") or {}).get("markets") or {})
    )
    interface_ports=interface_status.get("ports") or {}
    evidence_repo_status=interface_status.get("formal_evidence_repository") or {}
    check(
        "system_interface_registry_contract",
        interface_status.get("architecture")=="MODULAR_INTERFACE_REGISTRY"
        and set(interface_markets)>={"US","CN","HK"}
        and all(
            interface_ports.get(name)
            for name in (
                "runtime_services",
                "runtime_journal",
                "market_data",
                "decision",
                "research",
                "ui_read_services",
                "market_page",
                "risk",
            )
        )
        and evidence_repo_status.get("version")=="verified-projection-repository@1.0.0"
        and ((interface_status.get("ui_projections") or {}).get("validation_summary")
             =="validation-summary-projection@1.0.0"),
        interface_status,
    )

    strategy_source_modules=interface_status.get("strategy_source_modules") or {}
    isolation_policy=external_strategy_status.get("isolation_policy") or {}
    check(
        "external_strategy_runtime_contract",
        external_strategy_status.get("version")=="external-strategy@1.0.0"
        and isolation_policy.get("default")=="QUARANTINE"
        and isolation_policy.get("quarantine_allocation") is False
        and isolation_policy.get("shadow_allocation") is False
        and isolation_policy.get("fault_isolation")=="provider/account/pool scoped"
        and strategy_source_modules.get("external_strategy")=="external-strategy@1.0.0"
        and strategy_source_modules.get("fault_isolation")=="PROVIDER_ACCOUNT_POOL_SCOPED",
        {
            "status":external_strategy_status,
            "strategy_source_modules":strategy_source_modules,
        },
    )

    trader_shadow_policy=trader_shadow_status.get("automation_policy") or {}
    check(
        "trader_shadow_runtime_contract",
        trader_shadow_status.get("version")=="trader-shadow@1.1.0"
        and trader_shadow_policy.get("auto_create_shadow_account") is True
        and trader_shadow_policy.get("full_strategy_pool_default") is True
        and trader_shadow_policy.get("weights_optional_equal_weight_default") is True
        and trader_shadow_policy.get("three_route_comparison_auto_generated") is True
        and trader_shadow_policy.get("formal_next_period_outcome_auto_resolved") is True
        and trader_shadow_policy.get("broker_execution") is False
        and trader_shadow_policy.get("global_evidence_mutation") is False
        and trader_shadow_policy.get("open_strategy_interface_catalog") is True
        and trader_shadow_policy.get("custom_strategy_adapter") is True
        and trader_shadow_policy.get("first_observation_auto_enters_shadow") is True
        and trader_shadow_policy.get("shadow_strategy_simulation") is True
        and trader_shadow_policy.get("active_required_for_global_allocation") is True
        and strategy_source_modules.get("trader_shadow")=="trader-shadow@1.1.0"
        and strategy_source_modules.get("strategy_interface_catalog")=="strategy-interface-catalog@1.0.0"
        and strategy_source_modules.get("open_catalog_policy")=="EXPOSE_ALL_REGISTERED_STRATEGIES_AND_REGISTERABLE_STRATEGY_INTERFACES"
        and strategy_source_modules.get("shadow_simulation_rule")=="SHADOW_STRATEGIES_WITH_STANDARDIZED_STATE_MAY_RECEIVE_SIMULATED_WEIGHT_ONLY_INSIDE_TRADER_SHADOW",
        {
            "status":trader_shadow_status,
            "strategy_source_modules":strategy_source_modules,
        },
    )

    for market in ("US","CN","HK"):
        economic=economic_payloads.get(market) or {}
        check(
            f"{market}_economic_evolution_read_model",
            economic.get("version")=="economic-evolution@1.0.0"
            and economic.get("market_id")==market
            and economic.get("objective")=="MAXIMIZE_LONG_HORIZON_REALIZABLE_NET_COMPOUND_GROWTH"
            and economic.get("status") in {
                "WAITING_FOR_MATCHED_FORMAL_OUTCOMES",
                "EARLY_EVIDENCE_NOT_DECISION_GRADE",
                "EVALUABLE_DESCRIPTIVE_EVIDENCE",
                "COST_MODEL_UNAVAILABLE",
                "INVALID_MODELED_NET_RETURN",
            },
            economic,
        )
    for market in ("US","CN","HK"):
        validation=validation_payloads.get(market) or {}
        selected_validation=validation.get("selected_market") or {}
        overall_validation=validation.get("overall") or {}
        check(
            f"{market}_validation_summary_projection_contract",
            validation.get("contract_version")=="validation-summary-projection@1.0.0"
            and validation.get("projection_scope")=="TRIAID_REALIZED_VALUE_VALIDATION"
            and validation.get("market_id")==market
            and (validation.get("integrity") or {}).get("passed") is True
            and selected_validation.get("market_id")==market
            and int(overall_validation.get("evaluated_samples") or 0)>=0,
            validation,
        )
        definitions=validation.get("definitions") or {}
        check(
            f"{market}_validation_summary_not_account_return",
            "not account cumulative return" in str(definitions.get("sample_excess_sum") or ""),
            definitions,
        )
        outcome_status=payloads.get(f"/api/experiments/outcomes/{market}/status") or {}
        check(
            f"{market}_t0_t1_outcome_status_contract",
            outcome_status.get("resolver_version")=="triaid-outcome-resolver@1.0.0"
            and outcome_status.get("market_id")==market
            and int(outcome_status.get("formal_evidence_count") or 0)>=1
            and int(outcome_status.get("evaluated_count") or 0)>=0
            and int(outcome_status.get("waiting_count") or 0)>=0,
            outcome_status,
        )
        latest_outcome=payloads.get(f"/api/experiments/outcomes/{market}/latest") or {}
        if latest_outcome.get("state")=="EVALUATED":
            check(
                f"{market}_t0_t1_latest_evaluated_contract",
                bool(latest_outcome.get("evidence_id"))
                and len(str(latest_outcome.get("outcome_hash_sha256") or ""))==64
                and latest_outcome.get("triaid_excess_vs_baseline") is not None
                and latest_outcome.get("method") in {
                    "SYMMETRIC_THEORETICAL_HOLDINGS_COMPARISON",
                    "GENERIC_EVALUATION_MODULE",
                },
                latest_outcome,
            )
        else:
            check(
                f"{market}_t0_t1_latest_waiting_is_explicit",
                latest_outcome.get("state")=="WAITING"
                and bool(latest_outcome.get("reason")),
                latest_outcome,
            )

    registered_markets=[str(x).upper() for x in (status.get("markets") or [])]
    base_markets={"US","CN","HK"}
    check(
        "base_markets_present",
        base_markets.issubset(set(registered_markets)),
        registered_markets,
    )
    check(
        "registered_markets_unique",
        len(registered_markets)==len(set(registered_markets)),
        registered_markets,
    )
    strategy_count=status.get("strategy_registry_count")
    check(
        "strategy_registry_baseline_or_higher",
        isinstance(strategy_count,int) and strategy_count>=33,
        strategy_count,
    )
    registry_rows=market_registry.get("markets") or []
    registry_ids={str(x.get("market_id") or "").upper() for x in registry_rows if isinstance(x,dict)}
    check(
        "market_registry_matches_status",
        set(registered_markets)==registry_ids,
        {"status":registered_markets,"registry":sorted(registry_ids)},
    )
    accounts=(account_registry.get("accounts") or {})
    pools=(account_registry.get("strategy_pools") or {})
    check("global_account_present","GLOBAL" in accounts,sorted(accounts))
    check("global_strategy_pool_present","GLOBAL" in pools,sorted(pools))

    clock_rows=market_clocks.get("markets") or []
    clock_map={
        str(row.get("market_id") or "").upper():row
        for row in clock_rows if isinstance(row,dict)
    }
    check("market_clock_base_market_coverage",base_markets.issubset(set(clock_map)),sorted(clock_map))
    brief_rows=home_brief.get("markets") or {}
    brief_integrity=home_brief.get("integrity") or {}
    brief_policy=home_brief.get("performance_contract") or {}
    check(
        "fast_home_brief_runtime_contract",
        home_brief.get("version")=="home-brief@1.0.0"
        and set(registered_markets)==set(brief_rows)
        and brief_integrity.get("passed") is True
        and brief_policy.get("no_daily_report_rebuild") is True
        and brief_policy.get("no_market_data_fetch") is True
        and brief_policy.get("no_outcome_resolution") is True,
        {
            "version":home_brief.get("version"),
            "markets":sorted(brief_rows),
            "integrity":brief_integrity,
        },
    )
    for market in sorted(base_markets):
        brief=brief_rows.get(market) or {}
        check(
            f"{market}_fast_brief_formal_evidence_guard",
            brief.get("market_id")==market
            and brief.get("data_maturity") in {"FORMAL_COMPLETED_SESSION_ONLY","UNAVAILABLE"}
            and brief.get("status") in {"READY","WAITING"}
            and (
                brief.get("latest_evaluated") is None
                or (brief.get("latest_evaluated") or {}).get("evaluation",{}).get("status")=="EVALUATED"
            ),
            {
                "market_id":brief.get("market_id"),
                "status":brief.get("status"),
                "run_id":brief.get("run_id"),
                "data_maturity":brief.get("data_maturity"),
            },
        )

    daily_reports=daily_report_payload.get("reports") or {}
    daily_timing=daily_report_payload.get("market_timing") or {}
    daily_alignment=daily_report_payload.get("timing_alignment") or {}
    daily_integrity=daily_report_payload.get("integrity") or {}
    check(
        "daily_report_phase_aware_contract",
        daily_report_payload.get("version")=="daily-report@1.2.0"
        and daily_integrity.get("passed") is True
        and base_markets.issubset(set(daily_reports))
        and base_markets.issubset(set(daily_timing))
        and daily_alignment.get("cross_market_learning_basis")=="FORMAL_COMPLETED_SESSION_CUTOFFS_ONLY"
        and daily_alignment.get("alignment_rule")=="DO_NOT_FORCE_MARKETS_IN_DIFFERENT_SESSION_PHASES_OR_LOCAL_DATES_INTO_ONE_MATURITY_STATE",
        {
            "version":daily_report_payload.get("version"),
            "integrity":daily_integrity,
            "timing_alignment":daily_alignment,
        },
    )
    for market in sorted(base_markets):
        trading=((daily_reports.get(market) or {}).get("trading_analysis") or {})
        policy=trading.get("reporting_policy") or {}
        profit=trading.get("realized_profit_analysis") or {}
        check(
            f"{market}_daily_report_trading_analysis_contract",
            trading.get("report_type")=="TRIAID_TRADING_ANALYSIS_DAILY"
            and trading.get("market_id")==market
            and policy.get("primary_subject")=="TRADING_AND_ECONOMIC_VALUE"
            and policy.get("operations_in_body") is False
            and isinstance(trading.get("baseline_portfolio"),dict)
            and isinstance(trading.get("triaid_intervention"),dict)
            and isinstance(trading.get("trade_translation"),dict)
            and isinstance(profit,dict)
            and isinstance(trading.get("opportunity_cost"),dict)
            and isinstance(trading.get("next_trade_plan"),dict),
            {
                "report_type":trading.get("report_type"),
                "policy":policy,
                "keys":sorted(trading),
            },
        )
    allowed_profiles={
        "CALENDAR_DEGRADED",
        "NON_TRADING_DAY_LATEST_FINAL",
        "PREOPEN_BRIEF",
        "LIVE_INTRADAY_UPDATE",
        "MIDSESSION_BREAK_UPDATE",
        "POSTCLOSE_SETTLING",
        "FINAL_DAILY",
        "OFF_SESSION_LATEST_FINAL",
        "UNKNOWN_SESSION_STATE",
    }
    for market in sorted(base_markets):
        report=daily_reports.get(market) or {}
        timing=daily_timing.get(market) or {}
        report_timing=report.get("report_timing") or {}
        formal_date=timing.get("formal_evidence_date")
        working_date=timing.get("working_report_date")
        check(
            f"{market}_daily_report_timing_contract",
            timing.get("market_id")==market
            and timing.get("timezone")==((clock_map.get(market) or {}).get("timezone"))
            and timing.get("content_profile") in allowed_profiles
            and bool(timing.get("data_maturity"))
            and timing.get("timing_rule")=="MARKET_LOCAL_CALENDAR_AND_SESSION_PHASE_CONTROL_REPORT_CONTENT"
            and report_timing==timing
            and (report.get("report_contract") or {}).get("formal_and_live_layers_separated") is True,
            timing,
        )
        check(
            f"{market}_daily_report_formal_cutoff_not_future_of_working_date",
            not (formal_date and working_date) or str(formal_date)<=str(working_date),
            {"formal_evidence_date":formal_date,"working_report_date":working_date},
        )
        phase=str(timing.get("session_phase") or "").upper()
        expected_profile={
            "PREOPEN":"PREOPEN_BRIEF",
            "OPEN":"LIVE_INTRADAY_UPDATE",
            "BREAK":"MIDSESSION_BREAK_UPDATE",
        }.get(phase)
        if expected_profile:
            check(
                f"{market}_daily_report_active_phase_profile",
                timing.get("content_profile")==expected_profile
                and timing.get("working_report_is_formal") is False,
                timing,
            )
        if phase=="POSTCLOSE" and not timing.get("close_finalized") and not timing.get("current_session_formal"):
            check(
                f"{market}_daily_report_postclose_not_prematurely_final",
                timing.get("content_profile")=="POSTCLOSE_SETTLING"
                and timing.get("data_maturity")=="CLOSE_PENDING",
                timing,
            )
    forecast_markets=volatility_forecast.get("markets") or {}
    forecast_errors=volatility_forecast.get("errors") or {}
    check(
        "volatility_forecast_base_market_coverage",
        base_markets.issubset({str(x).upper() for x in forecast_markets}),
        {"markets":sorted(forecast_markets),"errors":forecast_errors},
    )
    check(
        "volatility_forecast_no_base_market_errors",
        not any(str(m).upper() in base_markets for m in forecast_errors),
        forecast_errors,
    )
    for market in sorted(base_markets):
        row=forecast_markets.get(market) or {}
        move=row.get("forecast_move_pct")
        expected_abs=row.get("expected_abs_move_pct")
        r68=row.get("range_68") or {}
        wf=row.get("walk_forward") or {}
        sample_count=wf.get("sample_count")
        coverage_68=wf.get("coverage_68")
        ratio=wf.get("rms_calibration_ratio")
        quality=str(wf.get("calibration_quality") or "")
        check(
            f"{market}_volatility_forecast_move_positive",
            isinstance(move,(int,float)) and math.isfinite(float(move)) and float(move)>0.0,
            move,
        )
        check(
            f"{market}_volatility_expected_abs_move_nonnegative",
            isinstance(expected_abs,(int,float)) and math.isfinite(float(expected_abs)) and float(expected_abs)>=0.0,
            expected_abs,
        )
        check(
            f"{market}_volatility_range_ordered",
            isinstance(r68.get("lower"),(int,float))
            and isinstance(r68.get("upper"),(int,float))
            and float(r68["lower"])<float(r68["upper"]),
            r68,
        )
        check(
            f"{market}_volatility_walkforward_sample",
            isinstance(sample_count,int) and sample_count>=60,
            sample_count,
        )
        check(
            f"{market}_volatility_coverage_68_valid",
            isinstance(coverage_68,(int,float)) and 0.0<=float(coverage_68)<=1.0,
            coverage_68,
        )
        check(
            f"{market}_volatility_calibration_ratio_valid",
            isinstance(ratio,(int,float)) and math.isfinite(float(ratio)) and float(ratio)>0.0,
            ratio,
        )
        check(
            f"{market}_volatility_calibration_quality_present",
            quality in {"WELL_CALIBRATED","USABLE","POORLY_CALIBRATED","INSUFFICIENT"},
            quality,
        )

    expected_timezones={"US":"America/New_York","CN":"Asia/Shanghai","HK":"Asia/Hong_Kong"}
    for market,timezone_name in expected_timezones.items():
        row=clock_map.get(market) or {}
        phase=str(row.get("session_phase") or "")
        check(f"{market}_market_clock_timezone",row.get("timezone")==timezone_name,row)
        check(f"{market}_market_clock_phase_present",phase in {"OPEN","PREOPEN","BREAK","POSTCLOSE","CLOSED","CALENDAR_UNAVAILABLE"},row)
        check(f"{market}_market_clock_green_semantics",bool(row.get("is_open"))==(phase=="OPEN"),row)
        check(f"{market}_market_clock_local_time_present",bool(row.get("local_iso")),row)
        if phase in {"PREOPEN","OPEN"}:
            baseline_deadline=time.monotonic()+120
            market_state=((scheduler_status.get("markets") or {}).get(market) or {})
            while (
                (
                    market_state.get("baseline_done") is not True
                    or market_state.get("baseline_fresh") is not True
                    or not market_state.get("baseline_expected_as_of")
                    or not market_state.get("baseline_reference_as_of")
                    or str(market_state.get("baseline_reference_as_of"))<str(market_state.get("baseline_expected_as_of"))
                )
                and time.monotonic()<baseline_deadline
            ):
                time.sleep(2)
                try:
                    _,scheduler_status=http_get("/api/decision-scheduler/status",timeout=10)
                except Exception:
                    continue
                market_state=((scheduler_status.get("markets") or {}).get(market) or {})
            check(
                f"{market}_active_session_baseline_fresh",
                market_state.get("baseline_done") is True
                and market_state.get("baseline_fresh") is True
                and bool(market_state.get("baseline_expected_as_of"))
                and bool(market_state.get("baseline_reference_as_of"))
                and str(market_state.get("baseline_reference_as_of"))>=str(market_state.get("baseline_expected_as_of")),
                market_state,
            )

    for market in ("US","CN","HK"):
        page=payloads.get(f"/api/ui/market-page/{market}?lang=zh") or {}
        live_page=payloads.get(f"/api/ui/market-page/{market}/live") or {}
        check(
            f"{market}_market_page_projection_contract",
            page.get("contract_version")=="market-page-projection@1.3.0"
            and page.get("projection_scope")=="FULL"
            and page.get("market_id")==market,
            {
                "contract_version":page.get("contract_version"),
                "projection_scope":page.get("projection_scope"),
                "market_id":page.get("market_id"),
            },
        )
        contract=page.get("contract") or {}
        integrity=page.get("integrity") or {}
        check(
            f"{market}_market_page_projection_integrity",
            integrity.get("passed") is True
            and integrity.get("frontend_safe") is True
            and integrity.get("unexplained_empty_count")==0
            and not (integrity.get("unexplained_non_ready_sections") or []),
            integrity,
        )
        check(
            f"{market}_market_page_single_source_contract",
            contract.get("single_market_page_source_of_truth") is True
            and contract.get("blank_without_reason_forbidden") is True
            and contract.get("intraday_is_not_formal_posterior") is True,
            contract,
        )
        sections=page.get("sections") or {}
        required_projection_sections={
            "daily","strategies","curves","runs","evolution","route",
            "posterior","preview","live","activity","scheduler","intraday",
        }
        check(
            f"{market}_market_page_projection_section_coverage",
            required_projection_sections.issubset(set(sections)),
            sorted(sections),
        )
        for required_section in ("daily","strategies","route"):
            section=sections.get(required_section) or {}
            check(
                f"{market}_projection_{required_section}_ready",
                section.get("state")=="READY",
                section,
            )
        for section_name,section in sections.items():
            check(
                f"{market}_projection_reason_contract_{section_name}",
                bool(section.get("state"))
                and (
                    section.get("state")=="READY"
                    or bool(section.get("reason"))
                ),
                section,
            )
        posterior=sections.get("posterior") or {}
        check(
            f"{market}_projection_posterior_never_unexplained",
            posterior.get("state")=="READY" or bool(posterior.get("reason")),
            posterior,
        )
        formal_evidence=page.get("formal_evidence") or {}
        check(
            f"{market}_formal_evidence_frozen",
            formal_evidence.get("state")=="FROZEN"
            and bool(formal_evidence.get("evidence_id"))
            and bool(formal_evidence.get("evidence_hash_sha256"))
            and bool((formal_evidence.get("decision_lineage") or {}).get("decision_id")),
            formal_evidence,
        )
        frozen=payloads.get(f"/api/experiments/evidence/{market}/latest") or {}
        excluded=set(frozen.get("future_information_excluded") or [])
        check(
            f"{market}_formal_evidence_repository_contract",
            frozen.get("evidence_schema")=="formal-market-projection-evidence@1.1.0"
            and frozen.get("evidence_id")==formal_evidence.get("evidence_id")
            and frozen.get("evidence_hash_sha256")==formal_evidence.get("evidence_hash_sha256")
            and excluded=={"posterior","curves","live","activity","intraday","route_embedded_reviews","localized_presentation_copy"}
            and all(
                key not in frozen
                for key in ("posterior","curves","live","activity","intraday","route","strategies")
            )
            and "formal_route" in frozen
            and "formal_strategies" in frozen
            and "latest_decision_review" not in (frozen.get("formal_route") or {}),
            {
                "evidence_id":frozen.get("evidence_id"),
                "excluded":sorted(excluded),
                "keys":sorted(frozen),
            },
        )
        check(
            f"{market}_live_projection_contract",
            live_page.get("contract_version")=="market-page-projection@1.3.0"
            and live_page.get("projection_scope")=="LIVE"
            and live_page.get("market_id")==market,
            {
                "contract_version":live_page.get("contract_version"),
                "projection_scope":live_page.get("projection_scope"),
                "market_id":live_page.get("market_id"),
            },
        )
        live_integrity=live_page.get("integrity") or {}
        live_sections=live_page.get("sections") or {}
        check(
            f"{market}_live_projection_integrity",
            live_integrity.get("passed") is True
            and live_integrity.get("frontend_safe") is True
            and live_integrity.get("unexplained_empty_count")==0
            and not (live_integrity.get("unexplained_non_ready_sections") or []),
            live_integrity,
        )
        check(
            f"{market}_live_projection_section_coverage",
            {"live","activity","scheduler","intraday"}.issubset(set(live_sections)),
            sorted(live_sections),
        )

    table_runtime_summary={}
    for market in ("US","CN","HK"):
        daily_payload=payloads.get(f"/api/daily?compact=true&market_id={market}")
        strategy_payload=payloads.get(f"/api/strategies?market_id={market}&lang=zh")
        curve_payload=payloads.get(f"/api/curves?market_id={market}")
        live_payload=payloads.get(f"/api/market-data/live-indicators/{market}")
        check(f"{market}_table_daily_payload",isinstance(daily_payload,dict),type(daily_payload).__name__)
        if isinstance(daily_payload,dict):
            intelligence=daily_payload.get("experiment_intelligence") or {}
            cross_learning=daily_payload.get("cross_market_learning") or {}
            check(
                f"{market}_daily_experiment_intelligence_present",
                isinstance(intelligence,dict) and intelligence.get("market_id")==market,
                intelligence,
            )
            check(
                f"{market}_daily_goal_gap_present",
                isinstance(intelligence.get("goal_gap"),dict)
                and bool(intelligence["goal_gap"].get("primary_gap_layer")),
                intelligence.get("goal_gap"),
            )
            check(
                f"{market}_daily_transition_evidence_present",
                isinstance(intelligence.get("transition_evidence"),dict)
                and "recomputed_count" in intelligence["transition_evidence"],
                intelligence.get("transition_evidence"),
            )
            check(
                f"{market}_cross_market_learning_present",
                isinstance(cross_learning,dict)
                and isinstance(cross_learning.get("markets"),list),
                cross_learning,
            )
        check(f"{market}_table_strategy_payload",isinstance(strategy_payload,list) and len(strategy_payload)>0,{"type":type(strategy_payload).__name__,"rows":len(strategy_payload) if isinstance(strategy_payload,list) else None})
        check(f"{market}_table_curve_payload",isinstance(curve_payload,list),type(curve_payload).__name__)
        check(f"{market}_table_live_payload",isinstance(live_payload,dict),type(live_payload).__name__)
        if isinstance(strategy_payload,list):
            malformed=[
                row.get("strategy_id") if isinstance(row,dict) else None
                for row in strategy_payload
                if not isinstance(row,dict) or not row.get("strategy_id") or "selected" not in row
            ]
            check(f"{market}_strategy_table_required_fields",not malformed,malformed[:10])
            selected_count=sum(1 for row in strategy_payload if isinstance(row,dict) and row.get("selected"))
            if market=="US":
                missing_numeric=[
                    row.get("strategy_id") if isinstance(row,dict) else None
                    for row in strategy_payload
                    if not isinstance(row,dict)
                    or not all(
                        isinstance(row.get(field),(int,float)) and math.isfinite(float(row.get(field)))
                        for field in ("expected_net_return","risk","baseline_weight","triaid_weight")
                    )
                ]
                check("US_strategy_surface_numeric_complete",not missing_numeric,missing_numeric[:10])
        else:
            selected_count=None
        daily_payload=daily_payload if isinstance(daily_payload,dict) else {}
        if market=="US":
            usrm=daily_payload.get("us_return_max") or {}
            latest_usrm=usrm.get("latest_decision") or {}
            usrm_cap=latest_usrm.get("capital_capacity") or {}
            check(
                "US_return_max_latest_decision_present",
                bool(latest_usrm.get("decision_id")),
                latest_usrm.get("decision_id"),
            )
            check(
                "US_return_max_strategy_weights_present",
                bool(latest_usrm.get("target_strategy_weights")),
                latest_usrm.get("target_strategy_weights"),
            )
            check(
                "US_return_max_asset_weights_present",
                isinstance(latest_usrm.get("target_asset_weights"),dict)
                and len(latest_usrm.get("target_asset_weights") or {})>=5,
                latest_usrm.get("target_asset_weights"),
            )
            check(
                "US_return_max_state_estimates_finite",
                all(
                    isinstance(latest_usrm.get(field),(int,float))
                    and math.isfinite(float(latest_usrm.get(field)))
                    for field in (
                        "projected_annualized_expected_net_return",
                        "generic_core_projected_annualized_expected_net_return",
                        "buy_hold_projected_annualized_expected_net_return",
                    )
                ),
                {
                    key:latest_usrm.get(key)
                    for key in (
                        "projected_annualized_expected_net_return",
                        "generic_core_projected_annualized_expected_net_return",
                        "buy_hold_projected_annualized_expected_net_return",
                    )
                },
            )
            check(
                "US_return_max_four_capital_sleeves_present",
                len(usrm_cap.get("sleeves") or [])==4,
                {
                    "currency":usrm_cap.get("currency"),
                    "sleeve_count":len(usrm_cap.get("sleeves") or []),
                },
            )
            us_clock=clock_map.get("US") or {}
            if str(us_clock.get("session_phase") or "").upper()=="OPEN":
                instruments=live_payload.get("instruments") if isinstance(live_payload,dict) else None
                check(
                    "US_open_live_surface_available",
                    isinstance(live_payload,dict)
                    and live_payload.get("available") is True
                    and isinstance(instruments,list)
                    and len(instruments)>=5,
                    {
                        "available":live_payload.get("available") if isinstance(live_payload,dict) else None,
                        "instrument_count":len(instruments) if isinstance(instruments,list) else None,
                    },
                )
                malformed_live=[
                    row.get("symbol") if isinstance(row,dict) else None
                    for row in (instruments or [])
                    if not isinstance(row,dict)
                    or not isinstance(row.get("close"),(int,float))
                    or not math.isfinite(float(row.get("close")))
                    or not isinstance(row.get("change_pct"),(int,float))
                    or not math.isfinite(float(row.get("change_pct")))
                ]
                check("US_open_live_instruments_numeric_complete",not malformed_live,malformed_live[:10])
                decision_rows=payloads.get("/api/decision-scheduler/events?market_id=US&limit=120")
                check(
                    "US_open_intraday_decision_surface_available",
                    isinstance(decision_rows,list) and len(decision_rows)>0,
                    {"event_count":len(decision_rows) if isinstance(decision_rows,list) else None},
                )
        table_runtime_summary[market]={
            "date":daily_payload.get("date"),
            "strategy_rows":len(strategy_payload) if isinstance(strategy_payload,list) else None,
            "selected_rows":selected_count,
            "curve_rows":len(curve_payload) if isinstance(curve_payload,list) else None,
            "live_payload_keys":len(live_payload) if isinstance(live_payload,dict) else None,
            "has_us_return_max":bool(daily_payload.get("us_return_max")),
            "has_cn_prospective":bool(daily_payload.get("prospective_experiment")),
            "has_cn_recovery_wave":bool(daily_payload.get("recovery_wave")),
        }

    risk_table_summary={
        "three_market_rows":len((risk_control.get("three_market_state") or risk_control.get("market_states") or [])) if isinstance(risk_control,dict) else None,
        "dynamics_rows":len((risk_control.get("dynamics_chain") or [])) if isinstance(risk_control,dict) else None,
        "macro_rows":len((risk_control.get("rates_policy_credit_snapshot") or {})) if isinstance(risk_control,dict) else None,
        "term_curve_contract_rows":sum(
            len(x or []) for k,x in ((risk_control.get("term_curve") or {}) if isinstance(risk_control,dict) else {}).items()
            if k in {"fed_funds","sofr_1m","sofr_3m"} and isinstance(x,list)
        ),
        "history_supported_rows":len((((risk_control.get("historical_validation") or {}).get("statistically_supported_composites") or []))) if isinstance(risk_control,dict) else None,
        "risk_control_rows":len((risk_control.get("three_market_state") or risk_control.get("market_states") or [])) if isinstance(risk_control,dict) else None,
    }
    check("risk_table_three_market_rows",isinstance(risk_table_summary["three_market_rows"],int) and risk_table_summary["three_market_rows"]>=3,risk_table_summary)
    print("TRIAID_TABLE_RUNTIME_AUDIT_SUMMARY",json.dumps({"markets":table_runtime_summary,"risk":risk_table_summary},ensure_ascii=False,separators=(",",":")),flush=True)

    cn_prospective_status=payloads.get("/api/experiments/cn/prospective/status") or {}
    check("cn_prospective_status_payload",isinstance(cn_prospective_status,dict),type(cn_prospective_status).__name__)
    check("cn_prospective_protocol_version",str(cn_prospective_status.get("version") or "")=="cn-prospective-controls@0.3.0",cn_prospective_status)
    check("cn_prospective_mode_is_auxiliary",str(cn_prospective_status.get("experiment_mode") or "")=="CN_WORST_POOL_RESCUE",cn_prospective_status)
    print("TRIAID_CN_PROSPECTIVE_STATUS",json.dumps(cn_prospective_status,ensure_ascii=False,separators=(",",":")),flush=True)

    deployment=status.get("deployment") or {}
    railway_sha=deployment.get("railway_git_commit_sha")
    declared_sha=deployment.get("declared_source_revision")
    runtime_sha=deployment.get("runtime_revision")
    check("railway_git_commit_sha_present",bool(railway_sha),deployment)
    check("declared_source_revision_present",bool(declared_sha),deployment)
    check("runtime_revision_present",bool(runtime_sha),deployment)
    check("declared_source_matches_railway",bool(railway_sha and declared_sha and railway_sha==declared_sha),deployment)
    check("runtime_revision_matches_railway",bool(railway_sha and runtime_sha and railway_sha==runtime_sha),deployment)
    check("deployment_identity_verified",deployment.get("identity_verified") is True,deployment)

    backend=storage.get("backend") or {}
    backend_name=backend.get("backend") if isinstance(backend,dict) else backend
    durability=storage.get("durability")
    check("storage_backend_known",backend_name in {"supabase","file"},{"backend":backend_name,"durability":durability})
    railway_runtime=bool(os.getenv("RAILWAY_PROJECT_ID") or os.getenv("RAILWAY_SERVICE_ID"))
    if railway_runtime:
        check(
            "railway_production_storage_must_be_supabase",
            backend_name=="supabase",
            {"backend":backend_name,"durability":durability},
        )
        check(
            "railway_production_storage_must_be_persistent",
            durability=="PERSISTENT",
            {"backend":backend_name,"durability":durability},
        )
    elif backend_name=="supabase":
        check("production_storage_persistent",durability=="PERSISTENT",durability)

    check("market_data_status_present",bool(market_status),None if market_status else "missing")
    for market in ("US","CN","HK"):
        rules=payloads.get(f"/api/strategy-population/rules/{market}") or {}
        check(f"{market}_rules_market_id",str(rules.get("market_id") or "").upper()==market,rules.get("market_id"))
        events=payloads.get(f"/api/decision-scheduler/events?market_id={market}&limit=1")
        check(f"{market}_scheduler_api",isinstance(events,list),type(events).__name__)

    market_rows=(risk_control.get("market_states") or risk_control.get("three_market_state") or [])
    risk_market_ids=[str(x.get("market") or "").upper() for x in market_rows if isinstance(x,dict)]
    check(
        "risk_control_base_market_coverage",
        base_markets.issubset(set(risk_market_ids)),
        risk_market_ids,
    )
    check(
        "risk_control_registered_market_scope",
        set(risk_market_ids).issubset(set(registered_markets)),
        {"risk":risk_market_ids,"registered":registered_markets},
    )
    overall=(risk_warning.get("overall") or {})
    score=overall.get("risk_pressure_index")
    check("risk_warning_score_present",isinstance(score,(int,float)),score)
    if isinstance(score,(int,float)):
        check("risk_warning_score_range",0.0<=float(score)<=100.0,score)
    check("risk_warning_no_production_action",risk_warning.get("production_action")=="NONE" and risk_warning.get("applied_to_weights") is False,{"action":risk_warning.get("production_action"),"applied":risk_warning.get("applied_to_weights")})
    rce=risk_control.get("risk_control_experiment") or {}
    check("risk_control_shadow_only",risk_control.get("shadow_only") is True,risk_control.get("shadow_only"))
    check("risk_control_no_production_action",rce.get("production_action")=="NONE" and rce.get("applied_to_weights") is False,{"action":rce.get("production_action"),"applied":rce.get("applied_to_weights")})

    if isinstance(home,str):
        for marker in (
            "TRIAID FIN",
            "TRIAID",
            "风险中心",
            "立即运行（预览）",
            'id="riskDataQuality"',
            'id="riskDataGaps"',
        ):
            check("ui_marker:"+marker,marker in home,None if marker in home else "missing")
        check("ui_no_raw_json_dump","JSON.stringify(d,null,2)" not in home,None)
        for marker in ('id="homeSummary"','id="homeSummaryPurpose"','id="homeSummaryDecision"','id="homeSummaryValidation"','id="homeSummaryRisk"','id="homeVolatility"','id="volCardUS"','id="volCardCN"','id="volCardHK"','data-clock-market="US"','data-clock-market="CN"','data-clock-market="HK"','id="marketHero"','id="dailyExperimentPanel"','id="deiCrossMarketRows"'):
            check("ui_market_identity_marker:"+marker,marker in home,None if marker in home else "missing")
        risk_pos=home.find('id="riskWarningPanel"')
        specialty_positions=[
            home.find('id="usReturnMaxPanel"'),
            home.find('id="prospectivePanel"'),
            home.find('id="recoveryWavePanel"'),
        ]
        check(
            "ui_market_specific_content_precedes_cross_market_risk",
            risk_pos>max(specialty_positions) and min(specialty_positions)>=0,
            {"risk_pos":risk_pos,"specialty_positions":specialty_positions},
        )

    maintenance=live.get("startup_maintenance_receipt")
    if isinstance(maintenance,dict):
        check("startup_maintenance_not_failed",maintenance.get("state")!="FAILED",maintenance)

    risk_audit=run_case("risk_center_full_audit.py")
    rows.append({"name":"risk_center_full_audit","passed":risk_audit["passed"],"detail":risk_audit})

    smoke=run_case("postdeploy_runtime_smoke.py")
    rows.append({"name":"postdeploy_runtime_smoke","passed":smoke["passed"],"detail":smoke})

    return rows

def finish(mode:str,rows:list[dict])->int:
    failed=[row for row in rows if not row.get("passed")]
    receipt={
        "audit":"TRIAID_RELEASE_AUDIT_CHAIN_V1",
        "mode":mode,
        "passed":not failed,
        "required_check_count":len(rows),
        "passed_check_count":len(rows)-len(failed),
        "failed_check_count":len(failed),
        "failed_checks":[row.get("name") for row in failed],
        "source_identity":{
            "railway_git_commit_sha":os.getenv("RAILWAY_GIT_COMMIT_SHA","").strip() or None,
            "declared_source_revision":(
                os.getenv("TRIAID_DEPLOY_REVISION","").strip()
                or os.getenv("TRIAID_DEPLOY_REV","").strip()
                or None
            ),
            "runtime_revision":os.getenv("TRIAID_V2_REV","").strip() or None,
        },
        "checks":rows,
        "completed_at_unix":time.time(),
    }
    write_receipt(receipt)
    # CI must identify the exact failing smoke instead of only listing its
    # filename; this is bounded and does not print complete domain payloads.
    for row in failed:
        detail=row.get("detail")
        source=detail if isinstance(detail,dict) else row
        if isinstance(source,dict) and (
            source.get("returncode") is not None
            or source.get("stderr_tail")
            or source.get("stdout_tail")
        ):
            compact={
                "name":row.get("name"),
                "returncode":source.get("returncode"),
                "stdout_tail":source.get("stdout_tail"),
                "stderr_tail":source.get("stderr_tail"),
            }
            print("TRIAID_RELEASE_AUDIT_FAILED_DETAIL",json.dumps(compact,ensure_ascii=False),flush=True)
    print(
        "TRIAID_RELEASE_AUDIT_"+("PASS" if receipt["passed"] else "FAIL"),
        json.dumps(
            {
                "mode":mode,
                "required":receipt["required_check_count"],
                "passed":receipt["passed_check_count"],
                "failed":receipt["failed_check_count"],
                "failed_checks":receipt["failed_checks"],
                "receipt_path":str(RECEIPT_PATH),
            },
            ensure_ascii=False,
            separators=(",",":"),
        ),
        flush=True,
    )
    return 0 if receipt["passed"] else 1

if MODE=="build":
    rows=structural_checks()
    compile_case=subprocess.run(
        [sys.executable,"-m","py_compile","app.py",*sorted(str(p.relative_to(ROOT)) for p in (ROOT/"triaid_fin").glob("*.py")),*sorted(p.name for p in ROOT.glob("*.py"))],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    rows.append({
        "name":"python_compile_all",
        "passed":compile_case.returncode==0,
        "detail":{"returncode":compile_case.returncode,"stderr_tail":"\n".join(compile_case.stderr.splitlines()[-8:])},
    })
    for script in BUILD_CASES:
        rows.append(run_case(script))
    raise SystemExit(finish("build",rows))

if MODE=="runtime":
    try:
        rows=runtime_checks()
    except Exception as exc:
        rows=[{"name":"runtime_audit_exception","passed":False,"detail":f"{type(exc).__name__}:{exc}"}]
    raise SystemExit(finish("runtime",rows))

raise SystemExit(f"unknown audit mode: {MODE}")
