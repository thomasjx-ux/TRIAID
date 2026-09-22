from __future__ import annotations

from triaid_fin.contracts import (
    BilingualText,
    EvaluationResult,
    MarketSnapshot,
    RunRecord,
    StrategyGroup,
    StrategyState,
    TriaidDecision,
)
from triaid_fin.review import ReviewModule


def bt(text: str) -> BilingualText:
    return BilingualText(zh=text, en=text)


def state(strategy_id: str, value: float, selected: bool = True) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        expected_net_return=value,
        lifecycle="active" if selected else "candidate",
        selection_reason=bt(f"{strategy_id} evidence"),
    )


previous=RunRecord(
    run_id="US-prev",
    created_at="2026-09-21T20:00:00+00:00",
    status="VERIFIED",
    module_manifest={"review":"review@0.5.0"},
    market=MarketSnapshot(
        market_id="US",
        as_of="2026-09-21",
        snapshot_id="US-prev-snap",
        metadata={"experiment_mode":"US_RETURN_MAX_CAPACITY"},
    ),
    strategy_states=[state("P00_BUY_HOLD",0.10),state("P01_VOL10",0.08)],
    strategy_group=StrategyGroup(
        group_version="g",
        config_version="c",
        market_id="US",
        members=["P00_BUY_HOLD","P01_VOL10"],
        weights={"P00_BUY_HOLD":0.6,"P01_VOL10":0.4},
        reasons={"P00_BUY_HOLD":bt("hold"),"P01_VOL10":bt("vol")},
    ),
    triaid_decision=TriaidDecision(
        core_version="core",
        weights_before={"P00_BUY_HOLD":0.6,"P01_VOL10":0.4},
        weights_after={"P00_BUY_HOLD":0.6,"P01_VOL10":0.4},
        reasons={},
    ),
    evaluation=EvaluationResult(
        status="EVALUATED",
        baseline_return=0.01,
        triaid_return=0.01,
        excess_return=0.0,
        trading_cost=0.0,
        strategy_realized_returns={"P00_BUY_HOLD":0.01,"P01_VOL10":0.01},
        baseline_contributions={"P00_BUY_HOLD":0.006,"P01_VOL10":0.004},
        triaid_contributions={"P00_BUY_HOLD":0.006,"P01_VOL10":0.004},
    ),
)

current=RunRecord(
    run_id="US-current",
    created_at="2026-09-22T20:00:00+00:00",
    status="VERIFIED",
    module_manifest={"review":"review@0.5.0"},
    market=MarketSnapshot(
        market_id="US",
        as_of="2026-09-22",
        snapshot_id="US-current-snap",
        metadata={"experiment_mode":"US_RETURN_MAX_CAPACITY"},
    ),
    strategy_states=[
        state("P00_BUY_HOLD",0.12),
        state("P02_VOL15",0.15),
        state("P03_DD_GUARD",0.02,False),
    ],
    strategy_group=StrategyGroup(
        group_version="g",
        config_version="c",
        market_id="US",
        members=["P00_BUY_HOLD","P02_VOL15"],
        weights={"P00_BUY_HOLD":0.3,"P02_VOL15":0.7},
        reasons={"P00_BUY_HOLD":bt("reduced"),"P02_VOL15":bt("higher net state return")},
    ),
    triaid_decision=TriaidDecision(
        core_version="core",
        weights_before={"P00_BUY_HOLD":0.3,"P02_VOL15":0.7},
        weights_after={"P00_BUY_HOLD":0.2,"P02_VOL15":0.8},
        reasons={"P00_BUY_HOLD":bt("overlay reduce"),"P02_VOL15":bt("overlay increase")},
    ),
    evaluation=EvaluationResult(
        status="EVALUATED",
        baseline_return=0.0200,
        triaid_return=0.0210,
        excess_return=0.0010,
        trading_cost=0.0001,
        strategy_realized_returns={"P00_BUY_HOLD":0.01,"P02_VOL15":0.025},
        baseline_contributions={"P00_BUY_HOLD":0.003,"P02_VOL15":0.0175},
        triaid_contributions={"P00_BUY_HOLD":0.002,"P02_VOL15":0.0200},
    ),
)

report=ReviewModule().daily_summary([previous,current])
assert report["date"]=="2026-09-22"
assert report["report_contract"]["strategy_change_reason_required"] is True
assert report["report_contract"]["report_type"]=="INVESTMENT_STRATEGY_DAILY"
assert report["report_contract"]["technical_runtime_report_default"] is False
strategy_report=report["investment_strategy_reports"]["US"]
assert strategy_report["report_type"]=="INVESTMENT_STRATEGY_DAILY"
assert strategy_report["strategy_thesis"]["objective"].startswith("Maximize realizable net return")
assert strategy_report["session_review"]["added"]==["P02_VOL15"]
assert strategy_report["session_review"]["removed"]==["P01_VOL10"]
assert strategy_report["performance_review"]["conclusion"]=="TRIAID_OUTPERFORMED_BASELINE"
assert strategy_report["forward_view"]["forecast_discipline"].startswith("Forward view is conditional")
assert strategy_report["technical_appendix_policy"]["default_visibility"]=="COLLAPSED"
change=report["strategy_changes"][0]
assert change["added"]==["P02_VOL15"]
assert change["removed"]==["P01_VOL10"]
assert change["triaid_overlay_changed"] is True
assert abs(change["triaid_overlay_l1_change"]-0.2)<1e-12
assert change["excluded_strategies"][0]["strategy_id"]=="P03_DD_GUARD"
comparison=report["return_comparisons"]["US"]["current_run"]
assert comparison["outcome_status"]=="EVALUATED"
assert abs(comparison["realized_excess_return"]-0.001)<1e-12
assert abs(comparison["contribution_deltas"]["P02_VOL15"]-0.0025)<1e-12
assert any("策略成员变化" in line for line in report["analysis_zh"])
print("TRIAID_DAILY_REPORT_CHANGE_ATTRIBUTION_SMOKE_PASS")
