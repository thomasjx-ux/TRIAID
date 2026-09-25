from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine
from triaid_fin.market_registry import MARKET_REGISTRY, MarketSpec, market_ids


engine=EvolutionLabEngine()
expected=list(market_ids())

assert engine.daily_report.version.startswith("daily-report@")
assert engine.module_manifest["daily_report"]==engine.daily_report.version

payload=engine.daily_reports(compact=True)
assert payload["markets"]==expected
assert payload["market_count"]==len(expected)
assert set(payload["reports"])==set(expected)
assert payload["integrity"]["registry_market_count"]==len(expected)
assert payload["integrity"]["report_market_count"]==len(expected)
assert payload["integrity"]["missing_markets"]==[]

for market in expected:
    report=payload["reports"][market]
    module=report["report_module"]
    assert module["version"]==engine.daily_report.version
    assert module["market_id"]==market
    assert module["enabled_markets"]==expected
    assert module["coverage_rule"]=="MARKET_REGISTRY_DRIVEN_NO_SILENT_OMISSION"
    assert report["report_contract"]["report_type"]=="INVESTMENT_STRATEGY_DAILY"

# Prove that report coverage follows the registry rather than a fixed US/CN/HK list.
MARKET_REGISTRY.register(
    MarketSpec(
        "TST",
        "TST.BENCH",
        ("TST.A",),
        ("TST.A",),
        (),
        "TST",
        1_000_000.0,
        1.0,
        20.0,
        0.02,
        metadata={"primary_experiment_mode":"TST_RETURN_MAX_CAPACITY"},
    )
)
expanded=engine.daily_reports(compact=True)
assert "TST" in expanded["markets"]
assert "TST" in expanded["reports"]
assert expanded["reports"]["TST"]["report_module"]["market_extension_state"]=="BASE_REPORT_ONLY"
assert expanded["integrity"]["missing_markets"]==[]

print("TRIAID_DAILY_REPORT_MODULE_SMOKE_PASS")
print({
    "version":expanded["version"],
    "markets":expanded["markets"],
    "integrity":expanded["integrity"],
})
