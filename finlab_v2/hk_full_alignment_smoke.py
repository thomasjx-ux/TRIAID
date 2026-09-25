from __future__ import annotations

from pathlib import Path

from triaid_fin.contracts import (
    BilingualText,
    EvaluationResult,
    MarketSnapshot,
    RunRecord,
    StrategyGroup,
    StrategyState,
    TriaidDecision,
)
from triaid_fin.hk_return_max import HKD_CAPITAL_SLEEVES
from triaid_fin.review import ReviewModule


ROOT=Path(__file__).resolve().parent


def bt(text:str)->BilingualText:
    return BilingualText(zh=text,en=text)


def state(sid:str,value:float)->StrategyState:
    return StrategyState(
        strategy_id=sid,
        lifecycle="active",
        expected_net_return=value,
        risk=0.10,
        uncertainty=0.01,
        selection_reason=bt(sid+" evidence"),
    )


def run(
    run_id:str,
    as_of:str,
    created_at:str,
    base_weights:dict[str,float],
    triaid_weights:dict[str,float],
    realized:dict[str,float],
)->RunRecord:
    baseline=sum(float(w)*float(realized.get(sid,0.0)) for sid,w in base_weights.items())
    triaid=sum(float(w)*float(realized.get(sid,0.0)) for sid,w in triaid_weights.items())
    states=[
        state("P00_BUY_HOLD",0.10),
        state("P18_XMOM20",0.20),
        state("P25_BALANCED",0.15),
    ]
    return RunRecord(
        run_id=run_id,
        created_at=created_at,
        status="VERIFIED",
        module_manifest={"review":"review@hk-align"},
        market=MarketSnapshot(
            market_id="HK",
            as_of=as_of,
            snapshot_id=run_id+"-snap",
            regime="risk_on_trend",
            metadata={
                "experiment_mode":"HK_RETURN_MAX_CAPACITY",
                "daily_bar_complete":True,
                "run_scope":"OFFICIAL_EVIDENCE",
            },
        ),
        strategy_states=states,
        strategy_group=StrategyGroup(
            group_version="g",
            config_version="hk",
            market_id="HK",
            members=list(base_weights),
            weights=base_weights,
            reasons={sid:bt("frozen baseline") for sid in base_weights},
        ),
        triaid_decision=TriaidDecision(
            core_version="core",
            weights_before=base_weights,
            weights_after=triaid_weights,
            reasons={sid:bt("TRIAID reweight") for sid in triaid_weights},
        ),
        evaluation=EvaluationResult(
            status="EVALUATED",
            baseline_return=baseline,
            triaid_return=triaid,
            excess_return=triaid-baseline,
            trading_cost=0.0001,
            strategy_realized_returns=realized,
            baseline_contributions={sid:float(w)*float(realized.get(sid,0.0)) for sid,w in base_weights.items()},
            triaid_contributions={sid:float(w)*float(realized.get(sid,0.0)) for sid,w in triaid_weights.items()},
        ),
    )


previous=run(
    "HK-prev","2026-09-22","2026-09-22T08:30:00+00:00",
    {"P00_BUY_HOLD":0.5,"P25_BALANCED":0.5},
    {"P00_BUY_HOLD":0.4,"P25_BALANCED":0.6},
    {"P00_BUY_HOLD":0.01,"P25_BALANCED":0.02},
)
current=run(
    "HK-current","2026-09-23","2026-09-23T08:30:00+00:00",
    {"P18_XMOM20":0.6,"P25_BALANCED":0.4},
    {"P18_XMOM20":0.7,"P25_BALANCED":0.3},
    {"P18_XMOM20":0.03,"P25_BALANCED":0.01,"P00_BUY_HOLD":0.012},
)

review=ReviewModule()
assert review._primary_mode("HK")=="HK_RETURN_MAX_CAPACITY"
report=review.daily_summary([previous,current])
assert report["date"]=="2026-09-23",report
assert "HK" in report["return_comparisons"],report
assert "HK" in report["investment_strategy_reports"],report
strategy=report["investment_strategy_reports"]["HK"]
capitalized=strategy["performance_review"]["capitalized"]
assert capitalized["currency"]=="HKD",capitalized
assert capitalized["capital_sleeves"]==[float(x) for x in HKD_CAPITAL_SLEEVES],capitalized
assert len(capitalized["rows"])==4,capitalized
assert capitalized["rows"][0]["starting_capital"]==100000.0
assert capitalized["rows"][-1]["starting_capital"]==100000000.0

curves=review.curves([previous,current])
assert len(curves)==2,curves
assert all(row["market_id"]=="HK" for row in curves)
assert curves[-1]["run_id"]=="HK-current"

app=(ROOT/"app.py").read_text(encoding="utf-8")
engine=(ROOT/"triaid_fin"/"engine.py").read_text(encoding="utf-8")
checks={
    "hk_four_hkd_sleeves_ui":"四档港币资金规模容量实验" in app,
    "hk_strategy_weights_ui":'id="hkStrategyRows"' in app,
    "hk_asset_targets_ui":'id="hkAssetRows"' in app and "底层港股 ETF 目标敞口" in app,
    "hk_realized_capacity_ui":'id="hkRealizedRows"' in app,
    "hk_posterior_path_ui":'id="hkDailyRows"' in app,
    "old_capacity_gap_copy_removed":"港股独立容量后验账本尚未形成" not in app,
    "engine_exposes_hk_route_report":"hk_return_max_daily_report" in engine and 'def _daily_hk_sections' in engine and 'payload["hk_return_max"]=hk_return' in engine,
    "engine_records_hk_outcome":"hk_return_max_ledger.record_outcome" in engine,
    "engine_freezes_hk_decision":"hk_return_max_ledger.freeze" in engine,
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("TRIAID_HK_FULL_ALIGNMENT_SMOKE_FAILED:"+",".join(failed))

print("TRIAID_HK_FULL_ALIGNMENT_SMOKE_PASS",{
    "review_primary_mode":review._primary_mode("HK"),
    "curves":len(curves),
    "currency":capitalized["currency"],
    "capital_sleeves":capitalized["capital_sleeves"],
    "checks":len(checks),
})
