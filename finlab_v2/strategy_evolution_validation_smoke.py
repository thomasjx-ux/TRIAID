from __future__ import annotations

import os
import shutil
import tempfile
from copy import deepcopy
from dataclasses import asdict

tmp=tempfile.mkdtemp(prefix="triaid-strategy-evolution-smoke-")
os.environ["TRIAID_STORAGE_BACKEND"]="file"
os.environ["TRIAID_DATA_DIR"]=tmp

from triaid_fin.contracts import EvaluationResult, MarketSnapshot, RunRecord, StrategyState
from triaid_fin.store import RunStore
from triaid_fin.strategy_evolution import StrategyEvolutionModule


def states():
    return [
        StrategyState(
            strategy_id="P00_BUY_HOLD",lifecycle="active",
            expected_net_return=0.20,risk=0.10,uncertainty=0.01,
            recent_returns=[0.0010]*252,
        ),
        StrategyState(
            strategy_id="P04_TREND50",lifecycle="active",
            expected_net_return=0.16,risk=0.08,uncertainty=0.01,
            recent_returns=[0.0008]*252,
        ),
        StrategyState(
            strategy_id="P28_CASH",lifecycle="active",
            expected_net_return=0.0,risk=0.0,uncertainty=0.0,
            recent_returns=[0.0]*252,
        ),
    ]


def run(i:int)->RunRecord:
    day=i+1
    realized={"P00_BUY_HOLD":0.010,"P04_TREND50":0.008,"P28_CASH":0.0}
    return RunRecord(
        run_id=f"US-EVO-{i:03d}",
        created_at=f"2026-01-{day:02d}T12:00:00+00:00" if day<=31 else f"2026-02-{day-31:02d}T12:00:00+00:00",
        status="VERIFIED",
        module_manifest={},
        market=MarketSnapshot(
            market_id="US",
            as_of=f"2026-01-{day:02d}" if day<=31 else f"2026-02-{day-31:02d}",
            snapshot_id=f"STRATEVO:{i}",
            regime="risk_on_trend",
            metadata={
                "daily_bar_complete":True,
                "base_cost_bps":0.0,
                "experiment_mode":"US_RETURN_MAX_CAPACITY",
            },
        ),
        strategy_states=states(),
        evaluation=EvaluationResult(
            status="EVALUATED",
            baseline_return=0.009,
            triaid_return=0.009,
            excess_return=0.0,
            trading_cost=0.0,
            strategy_realized_returns=realized,
        ),
    )


try:
    store=RunStore(root=tmp)
    evo=StrategyEvolutionModule(store)
    active=evo.active("US")
    candidate=deepcopy(active)
    candidate.version="strategy-rules-us-candidate-validation-smoke"
    candidate.status="candidate"
    candidate.parent_version=active.version
    candidate.hypothesis="validation smoke identical profile"

    m=evo._market("US")
    m["profiles"][candidate.version]=asdict(candidate)
    all_runs=[run(i) for i in range(25)]
    dev=all_runs[:14]
    holdout=all_runs[14:20]
    shadow=all_runs[20:]
    m["history"].append({
        "event":"CANDIDATE_CREATED",
        "version":candidate.version,
        "parent":active.version,
        "created_at":"2026-01-20T23:59:59+00:00",
        "development_run_ids":[r.run_id for r in dev],
        "reserved_holdout_run_ids":[r.run_id for r in holdout],
        "evidence_scope":"PRIMARY_ROUTE_ONLY",
        "objective":"MAXIMIZE_REALIZABLE_NET_RETURN",
    })
    evo._save()

    fake=evo.promote("US",candidate.version,{
        "replay_pass":True,"holdout_pass":True,"shadow_pass":True,"audit_pass":True
    })
    assert fake["promoted"] is False
    assert fake["reason"]=="INTERNAL_VALIDATION_REQUIRED"

    receipt=evo.validate_candidate("US",candidate.version,all_runs)
    assert receipt["passed"] is True,receipt
    assert receipt["development"]["count"]==14
    assert receipt["holdout"]["count"]==6
    assert receipt["shadow"]["count"]==5
    assert receipt["replay_pass"] is True
    assert receipt["holdout_pass"] is True
    assert receipt["shadow_pass"] is True
    assert receipt["audit_pass"] is True
    assert receipt["evidence_scope"]=="PRIMARY_ROUTE_ONLY"
    assert receipt["objective"]=="MAXIMIZE_REALIZABLE_NET_RETURN"

    promoted=evo.promote("US",candidate.version,{"receipt_id":receipt["receipt_id"]})
    assert promoted["promoted"] is True,promoted
    assert evo.active("US").version==candidate.version

    print("TRIAID_STRATEGY_EVOLUTION_VALIDATION_SMOKE_PASS")
    print({"candidate":candidate.version,"receipt":receipt["receipt_id"]})
finally:
    shutil.rmtree(tmp,ignore_errors=True)
