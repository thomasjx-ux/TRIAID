from __future__ import annotations

from pathlib import Path

from triaid_fin.market_data import MarketDataHub
from triaid_fin.market_interfaces import MARKET_INTERFACE_REGISTRY, market_interface
from triaid_fin.market_registry import market_ids
from triaid_fin.runtime_jobs import RUNTIME_JOB_REGISTRY


ROOT=Path(__file__).resolve().parent
market_data_source=(ROOT/"triaid_fin"/"market_data.py").read_text(encoding="utf-8")
runtime_source=(ROOT/"triaid_fin"/"market_runtime.py").read_text(encoding="utf-8")
scheduler_source=(ROOT/"triaid_fin"/"decision_scheduler.py").read_text(encoding="utf-8")
projection_source=(ROOT/"triaid_fin"/"ui_projection.py").read_text(encoding="utf-8")
observation_source=(ROOT/"triaid_fin"/"observation.py").read_text(encoding="utf-8")
app_source=(ROOT/"app.py").read_text(encoding="utf-8")
risk_projection_source=(ROOT/"triaid_fin"/"risk_projection.py").read_text(encoding="utf-8")

markets=market_ids()
profiles=MARKET_INTERFACE_REGISTRY.ids()
hub=MarketDataHub()
chains=(hub.registry.status().get("chains") or {})

checks={
    "all_registered_markets_have_interface_profiles":set(markets)==set(profiles),
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
    "runtime_frequency_policy_uses_journal_port":"FrequencyPolicy(self.services.journal)" in runtime_source,
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
    "risk_center_has_one_ui_projection_endpoint":'@app.get("/api/ui/risk-center")' in app_source,
    "risk_center_frontend_uses_projection":"jsonCachedStale('/api/ui/risk-center',10000)" in app_source,
    "risk_center_frontend_no_domain_fanout":"jsonOrNullCached('/api/risk-warning/latest',10000)" not in app_source and "jsonOrNullCached('/api/risk-control/latest',10000)" not in app_source,
    "risk_projection_is_read_only":"risk_warning_run" not in risk_projection_source and "risk_control_run" not in risk_projection_source,
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
