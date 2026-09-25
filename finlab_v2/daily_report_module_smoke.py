from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine
from triaid_fin.market_registry import MARKET_REGISTRY, MarketSpec, market_ids


engine=EvolutionLabEngine()
expected=list(market_ids())

assert engine.daily_report.version=="daily-report@1.2.0"
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
    assert module["timing_rule"]=="MARKET_LOCAL_CALENDAR_AND_SESSION_PHASE_CONTROL_REPORT_CONTENT"
    assert report["report_contract"]["report_type"]=="TRIAID_TRADING_ANALYSIS_DAILY"
    assert report["report_contract"]["market_phase_aware"] is True
    assert report["report_contract"]["formal_and_live_layers_separated"] is True
    assert report["report_contract"]["trading_analysis_is_primary"] is True
    assert report["report_contract"]["operations_in_body"] is False
    trading=report["trading_analysis"]
    assert trading["report_type"]=="TRIAID_TRADING_ANALYSIS_DAILY"
    assert trading["market_id"]==market
    assert trading["reporting_policy"]["primary_subject"]=="TRADING_AND_ECONOMIC_VALUE"
    assert trading["reporting_policy"]["operations_in_body"] is False
    assert "baseline_portfolio" in trading
    assert "triaid_intervention" in trading
    assert "trade_translation" in trading
    assert "realized_profit_analysis" in trading
    assert "opportunity_cost" in trading
    assert "next_trade_plan" in trading
    timing=report["report_timing"]
    assert timing["market_id"]==market
    assert timing["content_profile"]
    assert timing["data_maturity"]
    assert timing["timing_rule"]=="MARKET_LOCAL_CALENDAR_AND_SESSION_PHASE_CONTROL_REPORT_CONTENT"

# Prove that one aggregate clock does not force markets into one report maturity.
profiles=engine.daily_report._content_profile
assert profiles("PREOPEN",is_trading_day=True,close_finalized=False,current_session_formal=False,calendar_known=True)[:2]==("PREOPEN_BRIEF","PREVIOUS_FINAL_PLUS_PREOPEN")
assert profiles("OPEN",is_trading_day=True,close_finalized=False,current_session_formal=False,calendar_known=True)[:2]==("LIVE_INTRADAY_UPDATE","LIVE_PARTIAL")
assert profiles("BREAK",is_trading_day=True,close_finalized=False,current_session_formal=False,calendar_known=True)[:2]==("MIDSESSION_BREAK_UPDATE","LIVE_PARTIAL")
assert profiles("POSTCLOSE",is_trading_day=True,close_finalized=False,current_session_formal=False,calendar_known=True)[:2]==("POSTCLOSE_SETTLING","CLOSE_PENDING")
assert profiles("POSTCLOSE",is_trading_day=True,close_finalized=True,current_session_formal=True,calendar_known=True)[:2]==("FINAL_DAILY","CURRENT_SESSION_FINAL")
assert profiles("CLOSED",is_trading_day=False,close_finalized=False,current_session_formal=False,calendar_known=True)[:2]==("NON_TRADING_DAY_LATEST_FINAL","FINAL_HISTORICAL")

assert payload["timing_alignment"]["cross_market_learning_basis"]=="FORMAL_COMPLETED_SESSION_CUTOFFS_ONLY"
assert payload["timing_alignment"]["alignment_rule"]=="DO_NOT_FORCE_MARKETS_IN_DIFFERENT_SESSION_PHASES_OR_LOCAL_DATES_INTO_ONE_MATURITY_STATE"
assert payload["integrity"]["missing_timing_context"]==[]

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
assert expanded["reports"]["TST"]["report_timing"]["content_profile"]=="CALENDAR_DEGRADED"
assert expanded["integrity"]["missing_markets"]==[]
assert expanded["integrity"]["missing_timing_context"]==[]

print("TRIAID_DAILY_REPORT_MODULE_SMOKE_PASS")
print({
    "version":expanded["version"],
    "markets":expanded["markets"],
    "integrity":expanded["integrity"],
})
