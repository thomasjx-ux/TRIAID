from __future__ import annotations

from pathlib import Path

from triaid_fin.market_data import MarketDataHub
from triaid_fin.market_interfaces import MARKET_INTERFACE_REGISTRY, market_interface
from triaid_fin.market_registry import market_ids
from triaid_fin.runtime_jobs import RUNTIME_JOB_REGISTRY


ROOT=Path(__file__).resolve().parent
market_data_source=(ROOT/"triaid_fin"/"market_data.py").read_text(encoding="utf-8")
interfaces_source=(ROOT/"triaid_fin"/"market_interfaces.py").read_text(encoding="utf-8")
runtime_source=(ROOT/"triaid_fin"/"market_runtime.py").read_text(encoding="utf-8")
scheduler_source=(ROOT/"triaid_fin"/"decision_scheduler.py").read_text(encoding="utf-8")
projection_source=(ROOT/"triaid_fin"/"ui_projection.py").read_text(encoding="utf-8")
observation_source=(ROOT/"triaid_fin"/"observation.py").read_text(encoding="utf-8")
app_source=(ROOT/"app.py").read_text(encoding="utf-8")
risk_projection_source=(ROOT/"triaid_fin"/"risk_projection.py").read_text(encoding="utf-8")
runtime_ports_source=(ROOT/"triaid_fin"/"runtime_ports.py").read_text(encoding="utf-8")
ui_ports_source=(ROOT/"triaid_fin"/"ui_ports.py").read_text(encoding="utf-8")
projection_repository_source=(ROOT/"triaid_fin"/"projection_repository.py").read_text(encoding="utf-8")
validation_projection_source=(ROOT/"triaid_fin"/"validation_projection.py").read_text(encoding="utf-8")
market_contracts_source=(ROOT/"triaid_fin"/"market_contracts.py").read_text(encoding="utf-8")
market_profiles_source=(ROOT/"triaid_fin"/"market_profiles"/"__init__.py").read_text(encoding="utf-8")

markets=market_ids()
profiles=MARKET_INTERFACE_REGISTRY.ids()
hub=MarketDataHub()
chains=(hub.registry.status().get("chains") or {})

checks={
    "all_registered_markets_have_interface_profiles":set(markets)==set(profiles),
    "market_contracts_are_separate":"class MarketInterfaceRegistry" in market_contracts_source and "class MarketInterfaceProfile" in market_contracts_source,
    "market_profiles_are_plugin_loaded":"builtin_profiles" in market_profiles_source and "build_us_profile" in market_profiles_source and "build_cn_profile" in market_profiles_source and "build_hk_profile" in market_profiles_source,
    "central_market_interfaces_has_no_builtin_market_logic":all(token not in interfaces_source for token in ("US_ROUTE=","CN_ROUTE=","HK_ROUTE=","market_id=\"US\"","market_id=\"CN\"","market_id=\"HK\"")),
    "all_profiles_have_route_contracts":all(
        bool(market_interface(m).route.daily_fields) for m in markets
    ),
    "all_profiles_have_daily_provider_route":all(
        bool(market_interface(m).chain("DAILY")) for m in markets
    ),
    "provider_registry_matches_market_profiles":all(
        list(chains.get(f"{market}:{mode}") or [])==list(chain)
        for market in markets
        for mode,chain in market_interface(market).provider_chains.items()
    ),
    "runtime_jobs_are_registered":all(
        job in RUNTIME_JOB_REGISTRY.names()
        for market in markets
        for job in market_interface(market).runtime_jobs
    ),
    "market_data_control_flow_has_no_market_literal_branches":all(
        token not in market_data_source
        for token in (
            'market=="US"',
            'market=="CN"',
            'market=="HK"',
            'market!="US"',
            'market!="CN"',
            'market!="HK"',
        )
    ),
    "runtime_control_flow_has_no_market_literal_branches":all(
        token not in runtime_source
        for token in (
            'market_id=="US"',
            'market_id=="CN"',
            'market_id=="HK"',
            'market=="US"',
            'market=="CN"',
            'market=="HK"',
        )
    ),
    "runtime_uses_stable_services_port":"self.services." in runtime_source and "self.engine." not in runtime_source,
    "scheduler_uses_stable_services_port":"self.services." in scheduler_source and "self.engine." not in scheduler_source,
    "scheduler_storage_is_journal_port":"self.store=self.services.journal" in scheduler_source,
    "market_projection_uses_ui_read_port":"MarketPageReadPort" in projection_source and "self.engine." not in projection_source,
    "risk_projection_uses_ui_read_port":"RiskReadPort" in risk_projection_source and "self.engine." not in risk_projection_source,
    "runtime_frequency_policy_uses_journal_port":"FrequencyPolicy(self.services.journal)" in runtime_source,
    "runtime_capability_ports_present":all(token in runtime_ports_source for token in ("class MarketDataRuntimePort","class DecisionRuntimePort","class ResearchRuntimePort")),
    "runtime_uses_capability_ports":"self.services.market_data." in runtime_source and "self.services.decision." not in runtime_source,
    "scheduler_uses_capability_ports":"self.services.decision." in scheduler_source and "self.services.market_data." in scheduler_source,
    "ui_capability_ports_present":"class MarketPageReadPort" in ui_ports_source and "class RiskReadPort" in ui_ports_source,
    "formal_projection_evidence_repository_present":"class VerifiedProjectionRepository" in projection_repository_source and "future_information_excluded" in projection_repository_source,
    "instrument_labels_live_in_market_profiles":"market_interface(market_id).instrument_labels" in runtime_source,
    "ui_route_projection_is_profile_driven":"route_spec=market_interface(market).route" in projection_source,
    "ui_projection_has_no_market_route_literal_branch":all(
        token not in projection_source
        for token in (
            'market=="US"',
            'market=="CN"',
            'market=="HK"',
            'market in {"US","HK"}',
        )
    ),
    "observation_timezone_uses_market_registry":"MARKET_REGISTRY.get(key).timezone" in observation_source,
    "observation_has_no_market_timezone_ternary":"America/New_York" not in observation_source and "Asia/Hong_Kong" not in observation_source and "Asia/Shanghai" not in observation_source,
    "app_composes_one_shared_runtime_services":"runtime_services=RuntimeServices(engine)" in app_source and "DecisionScheduler(runtime_services)" in app_source and "MarketDataAutomation(runtime_services,decision_scheduler)" in app_source,
    "app_composes_narrow_ui_ports":"ui_read_services=UiReadServices(engine)" in app_source and "ui_read_services.market_page" in app_source and "ui_read_services.risk" in app_source,
    "risk_center_has_one_ui_projection_endpoint":'@app.get("/api/ui/risk-center")' in app_source,
    "system_interface_status_endpoint":'@app.get("/api/system/interfaces")' in app_source,
    "risk_center_frontend_uses_projection":"jsonCachedStale('/api/ui/risk-center',10000)" in app_source,
    "risk_center_frontend_no_domain_fanout":"jsonOrNullCached('/api/risk-warning/latest',10000)" not in app_source and "jsonOrNullCached('/api/risk-control/latest',10000)" not in app_source,
    "risk_projection_is_read_only":"risk_warning_run" not in risk_projection_source and "risk_control_run" not in risk_projection_source,
    "validation_projection_is_read_only":"class ValidationSummaryProjection" in validation_projection_source and "does not recompute returns" in validation_projection_source,
    "validation_frontend_uses_one_projection":"/api/ui/validation-summary?market_id=" in app_source,
    "validation_frontend_has_no_outcome_domain_fanout":"/api/experiments/outcomes/" not in app_source[app_source.find("async function refreshValidationSummary"):app_source.find("function phaseText")],
    "market_page_frontend_uses_projection":"projectionUrl='/api/ui/market-page/'+m" in app_source,
}

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit(
        "TRIAID_MODULAR_INTERFACE_ARCHITECTURE_FAILED:"+"|".join(failed)
    )

print(
    "TRIAID_MODULAR_INTERFACE_ARCHITECTURE_PASS",
    {
        "checks":len(checks),
        "markets":list(markets),
        "runtime_jobs":list(RUNTIME_JOB_REGISTRY.names()),
    },
)
