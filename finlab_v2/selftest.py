import os
import shutil
import tempfile

tmp=tempfile.mkdtemp(prefix="triaid-fin-v2-selftest-")
os.environ["TRIAID_DATA_DIR"]=tmp

from triaid_fin.contracts import MarketSnapshot, OutcomeRequest, RunRequest, StrategyState
from triaid_fin.engine import EvolutionLabEngine


def state(strategy_id,expected,risk=0.05,uncertainty=0.01,lifecycle="active",recent=None):
    return StrategyState(
        strategy_id=strategy_id,
        lifecycle=lifecycle,
        expected_net_return=expected,
        risk=risk,
        uncertainty=uncertainty,
        oos_marginal_value=expected,
        shadow_evidence_pass=True,
        recent_returns=list(recent or []),
    )


try:
    engine=EvolutionLabEngine()

    assert engine.status()["strategy_registry_count"]==29
    zh=engine.strategy_population.strategy_cards("zh")
    en=engine.strategy_population.strategy_cards("en")
    assert len(zh)==29 and len(en)==29
    assert zh[0]["name"]!=en[0]["name"]

    request=RunRequest(
        market=MarketSnapshot(
            market_id="US",
            as_of="2026-09-18",
            snapshot_id="SELFTEST:1",
            regime="risk_on_trend",
        ),
        strategy_states=[
            state("P00_BUY_HOLD",0.12,0.18,0.02,recent=[0.001*((i%7)-3) for i in range(80)]),
            state("P09_SHOCK_GUARD",0.119,0.18,0.02,recent=[0.001*((i%7)-3) for i in range(80)]),
            state("P04_TREND50",0.16,0.12,0.02,recent=[0.0015*((i%5)-2) for i in range(80)]),
            state("P18_XMOM20",0.20,0.20,0.04,recent=[0.002*((i%9)-4) for i in range(80)]),
            state("P28_CASH",0.0,0.0,0.0),
            state("P16_REV5",0.50,0.20,0.04,lifecycle="shadow"),
        ],
        max_group_size=4,
    )

    run=engine.create_run(request)
    engine.execute(run.run_id,request)
    decision=engine.get_run(run.run_id)
    assert decision.status=="DECISION_READY_AWAITING_OUTCOME"
    assert decision.audit and decision.audit.passed
    assert decision.strategy_group
    assert "P16_REV5" not in decision.strategy_group.members
    assert decision.strategy_group.diagnostics["optimizer"]=="marginal-group-value-v1"
    assert engine.strategy_population.config_for("US").redundancy_penalty==0.0
    assert engine.strategy_population.config_for("US").near_duplicate_corr>1.0
    assert all(w>=0 for w in decision.strategy_group.weights.values())
    assert sum(decision.strategy_group.weights.values())<=1.0000001
    assert max(decision.strategy_group.weights.values())<=1.0000001
    assert decision.triaid_decision
    assert decision.triaid_decision.core_version==engine.evolution.active().version
    assert sum(decision.triaid_decision.weights_after.values())<=1.0000001

    verified=engine.submit_outcome(
        run.run_id,
        OutcomeRequest(
            realized_returns={
                "P00_BUY_HOLD":0.004,
                "P04_TREND50":0.005,
                "P18_XMOM20":-0.003,
                "P28_CASH":0.0,
            },
            trading_cost=0.0001,
        ),
    )
    assert verified.status=="VERIFIED"
    assert verified.evaluation and verified.evaluation.status=="EVALUATED"
    assert verified.audit and verified.audit.passed
    assert verified.diagnostic_summary.get("contribution_deltas") is not None

    curves=engine.curves("US")
    assert len(curves)>=1
    daily=engine.daily_summary("US")
    assert daily["evaluated_runs"]>=1

    reloaded=EvolutionLabEngine()
    assert reloaded.get_run(run.run_id).status=="VERIFIED"
    assert len(reloaded.all_runs())>=1

    before=reloaded.evolution_status()["active_version"]
    proposal=reloaded.propose_core_candidate()
    assert proposal["created"] is True
    candidate=proposal["candidate"]["version"]
    blocked=reloaded.promote_core(candidate,{"replay_pass":True,"holdout_pass":False,"shadow_pass":True,"audit_pass":True})
    assert blocked["promoted"] is False
    promoted=reloaded.promote_core(candidate,{"replay_pass":True,"holdout_pass":True,"shadow_pass":True,"audit_pass":True})
    assert promoted["promoted"] is True
    assert reloaded.evolution_status()["active_version"]==candidate
    assert before!=candidate

    hard_failure=state("P04_TREND50",0.2)
    hard_failure.hard_failure=True
    assert reloaded.strategy_population.recommend_lifecycle(hard_failure,"US")=="frozen"

    us_rules=reloaded.strategy_population.rules("US")
    cn_rules=reloaded.strategy_population.rules("CN")
    assert us_rules["entry_confirm_days"]==3 and us_rules["cooldown_days"]==5
    assert cn_rules["entry_confirm_days"]==5 and cn_rules["cooldown_days"]==10
    assert us_rules["max_weight"]==0.28 and cn_rules["max_weight"]==0.28

    strategy_evo=reloaded.strategy_evolution_status("US")
    assert strategy_evo["active_version"]=="strategy-rules-us@0.3.0"
    assert tuple(reloaded.strategy_evolution.active("US").window_weights)==(0.35,0.30,0.20,0.15)
    assert reloaded.strategy_evolution.active("US").switch_guard_enabled is False
    assert reloaded.strategy_evolution.active("CN").switch_guard_enabled is True
    assert reloaded.strategy_evolution.active("US").redundancy_penalty==0.0
    assert reloaded.strategy_evolution.active("CN").redundancy_penalty==0.0
    proposal_rules=reloaded.propose_strategy_candidate("US")
    assert proposal_rules["created"] is False
    assert proposal_rules["reason"]=="INSUFFICIENT_VERIFIED_RUNS"
    assert proposal_rules["required"]==20

    print("TRIAID_FIN_V2_SELFTEST_PASS")
    print(reloaded.module_manifest)
    print({"strategy_registry_count":reloaded.status()["strategy_registry_count"],"active_core":reloaded.evolution_status()["active_version"]})
finally:
    shutil.rmtree(tmp,ignore_errors=True)
