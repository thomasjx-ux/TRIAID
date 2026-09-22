from __future__ import annotations

import os
import shutil
import tempfile

tmp=tempfile.mkdtemp(prefix="triaid-primary-revision-smoke-")
os.environ["TRIAID_STORAGE_BACKEND"]="file"
os.environ["TRIAID_DATA_DIR"]=tmp

from triaid_fin.contracts import MarketSnapshot, OutcomeRequest, RunRequest, StrategyState
from triaid_fin.engine import EvolutionLabEngine


def states():
    return [
        StrategyState(
            strategy_id="P01_VOL10",
            lifecycle="active",
            expected_net_return=0.08,
            risk=0.08,
            uncertainty=0.01,
            recent_returns=[0.001]*80,
        ),
        StrategyState(
            strategy_id="P16_REV5",
            lifecycle="active",
            expected_net_return=0.06,
            risk=0.10,
            uncertainty=0.01,
            recent_returns=[0.0008]*80,
        ),
        StrategyState(
            strategy_id="P28_CASH",
            lifecycle="active",
            expected_net_return=0.0,
            risk=0.0,
            uncertainty=0.0,
            recent_returns=[0.0]*80,
        ),
    ]


def make_run(engine, revision):
    req=RunRequest(
        market=MarketSnapshot(
            market_id="CN",
            as_of="2026-09-22",
            snapshot_id="REVISION:CN:2026-09-22:FINAL",
            regime="risk_off",
            metadata={
                "daily_bar_complete":True,
                "experiment_mode":"CN_RETURN_MAX_CAPACITY",
                "market_route":"CN_RETURN_MAXIMIZATION",
                "primary_route_revision":revision,
            },
        ),
        strategy_states=states(),
        max_group_size=3,
    )
    run=engine.create_run(req)
    engine.execute(run.run_id,req)
    ready=engine.get_run(run.run_id)
    assert ready.status=="DECISION_READY_AWAITING_OUTCOME"
    return ready


try:
    engine=EvolutionLabEngine()
    old=make_run(engine,"fin-evolution-lab@0.13.0")

    def fake_run_live_research(market_id):
        assert market_id=="CN"
        return make_run(engine,engine.architecture_version)

    engine.run_live_research=fake_run_live_research

    receipt=engine.ensure_primary_reference("CN")
    assert receipt["created"] is True,receipt
    assert receipt["primary_route_revision"]==engine.architecture_version
    assert old.run_id in receipt["superseded_run_ids"]

    old_after=engine.get_run(old.run_id)
    current=engine.get_run(receipt["run_id"])
    assert old_after.status=="SUPERSEDED"
    assert old_after.market.metadata["primary_reference_superseded"] is True
    assert old_after.market.metadata["primary_reference_superseded_by"]==current.run_id
    assert current.market.metadata["primary_reference_bootstrap"] is True
    assert current.market.metadata["primary_route_revision"]==engine.architecture_version
    assert engine.latest_decision_run("CN").run_id==current.run_id
    assert engine.latest_run("CN").run_id==current.run_id

    try:
        engine.submit_outcome(
            old.run_id,
            OutcomeRequest(
                realized_returns={"P01_VOL10":0.01,"P16_REV5":0.005,"P28_CASH":0.0},
                trading_cost=0.0,
            ),
        )
    except ValueError as exc:
        assert str(exc)=="superseded_primary_reference_is_not_outcome_eligible"
    else:
        raise AssertionError("superseded primary reference must not accept outcomes")

    daily=engine.daily_summary("CN")
    assert daily["runs"]==1
    assert len(daily["runs_detail"])==1
    assert daily["runs_detail"][0]["run_id"]==current.run_id

    reloaded=EvolutionLabEngine()
    assert reloaded.get_run(old.run_id).status=="SUPERSEDED"
    assert reloaded.latest_decision_run("CN").run_id==current.run_id

    print("TRIAID_PRIMARY_REVISION_MIGRATION_SMOKE_PASS")
    print({
        "revision":engine.architecture_version,
        "current_run_id":current.run_id,
        "superseded_run_id":old.run_id,
    })
finally:
    shutil.rmtree(tmp,ignore_errors=True)
