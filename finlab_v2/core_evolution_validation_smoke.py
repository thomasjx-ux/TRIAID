from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date, timedelta

tmp=tempfile.mkdtemp(prefix="triaid-core-evolution-smoke-")
os.environ["TRIAID_STORAGE_BACKEND"]="file"
os.environ["TRIAID_DATA_DIR"]=tmp

from triaid_fin.contracts import MarketSnapshot, OutcomeRequest, RunRequest, StrategyState
from triaid_fin.engine import EvolutionLabEngine


def states():
    return [
        StrategyState(
            strategy_id="P00_BUY_HOLD",lifecycle="active",
            expected_net_return=0.18,risk=0.05,uncertainty=0.01,
            recent_returns=[0.001]*80,
        ),
        StrategyState(
            strategy_id="P04_TREND50",lifecycle="active",
            expected_net_return=0.12,risk=0.05,uncertainty=0.01,
            recent_returns=[0.0007]*80,
        ),
        StrategyState(
            strategy_id="P28_CASH",lifecycle="active",
            expected_net_return=0.0,risk=0.0,uncertainty=0.0,
            recent_returns=[0.0]*80,
        ),
    ]


def add_verified(engine,market,day_index):
    d=(date(2026,1,5)+timedelta(days=day_index)).isoformat()
    req=RunRequest(
        market=MarketSnapshot(
            market_id=market,
            as_of=d,
            snapshot_id=f"EVOSMOKE:{market}:{day_index}",
            regime="risk_on_trend",
            metadata={"daily_bar_complete":True,"base_cost_bps":0.0},
        ),
        strategy_states=states(),
        max_group_size=3,
    )
    run=engine.create_run(req)
    engine.execute(run.run_id,req)
    ready=engine.get_run(run.run_id)
    assert ready.status=="DECISION_READY_AWAITING_OUTCOME"
    realized={
        "P00_BUY_HOLD":0.010,
        "P04_TREND50":0.006,
        "P28_CASH":0.0,
    }
    verified=engine.submit_outcome(
        run.run_id,
        OutcomeRequest(realized_returns=realized,trading_cost=0.0),
    )
    assert verified.status=="VERIFIED"
    return verified


try:
    engine=EvolutionLabEngine()
    for market in ("US","CN"):
        for i in range(10):
            add_verified(engine,market,i)

    proposal=engine.propose_core_candidate()
    assert proposal["created"] is True,proposal
    assert proposal["development_runs_by_market"]=={"US":7,"CN":7}
    assert proposal["reserved_holdout_runs_by_market"]=={"US":3,"CN":3}
    version=proposal["candidate"]["version"]

    before_shadow=engine.promote_core(version)
    assert before_shadow["promoted"] is False
    receipt=before_shadow["validation_receipt"]
    assert receipt["replay_pass"] is True
    assert receipt["holdout_pass"] is True
    assert receipt["shadow_pass"] is False
    assert receipt["shadow_by_market"]["US"]["count"]==0
    assert receipt["shadow_by_market"]["CN"]["count"]==0

    for market in ("US","CN"):
        for i in range(10,15):
            add_verified(engine,market,i)

    promoted=engine.promote_core(version)
    assert promoted["promoted"] is True,promoted
    assert engine.evolution.active().version==version
    stored=engine.evolution.status()["validations"][version]
    assert stored["passed"] is True
    assert stored["shadow_by_market"]["US"]["count"]>=5
    assert stored["shadow_by_market"]["CN"]["count"]>=5
    assert stored["validation_discipline"].endswith("NO_CROSS_MARKET_MASKING")

    print("TRIAID_CORE_EVOLUTION_VALIDATION_SMOKE_PASS")
    print({
        "candidate":version,
        "development":proposal["development_runs_by_market"],
        "holdout":proposal["reserved_holdout_runs_by_market"],
        "shadow":{
            "US":stored["shadow_by_market"]["US"]["count"],
            "CN":stored["shadow_by_market"]["CN"]["count"],
        },
    })
finally:
    shutil.rmtree(tmp,ignore_errors=True)
