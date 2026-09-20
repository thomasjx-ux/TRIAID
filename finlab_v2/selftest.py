import os
import shutil
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

tmp=tempfile.mkdtemp(prefix="triaid-fin-v2-selftest-")
os.environ["TRIAID_DATA_DIR"]=tmp
os.environ["TRIAID_STORAGE_BACKEND"]="file"

from triaid_fin.contracts import MarketSnapshot, OutcomeRequest, RunRequest, StrategyState
from triaid_fin.engine import EvolutionLabEngine
from triaid_fin.decision_scheduler import DecisionScheduler
from triaid_fin.frequency_policy import FrequencyPolicy
from triaid_fin.market_data import session_phase
from triaid_fin.market_runtime import MarketDataAutomation


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

    assert engine.status()["strategy_registry_count"]==33
    assert engine.status()["architecture_version"]=="fin-evolution-lab@0.8.0"
    runtime=MarketDataAutomation(engine)
    assert session_phase("CN",datetime(2026,9,22,9,20,tzinfo=ZoneInfo("Asia/Shanghai")))=="PREOPEN"
    assert session_phase("CN",datetime(2026,9,22,10,0,tzinfo=ZoneInfo("Asia/Shanghai")))=="OPEN"
    assert session_phase("CN",datetime(2026,9,22,12,0,tzinfo=ZoneInfo("Asia/Shanghai")))=="BREAK"
    assert session_phase("CN",datetime(2026,9,22,14,0,tzinfo=ZoneInfo("Asia/Shanghai")))=="OPEN"
    assert session_phase("CN",datetime(2026,9,22,15,5,tzinfo=ZoneInfo("Asia/Shanghai")))=="POSTCLOSE"
    assert session_phase("US",datetime(2026,9,22,8,0,tzinfo=ZoneInfo("America/New_York")))=="PREOPEN"
    assert session_phase("US",datetime(2026,9,22,10,0,tzinfo=ZoneInfo("America/New_York")))=="OPEN"
    assert runtime.refresh_plan_for_phase("CN","PREOPEN")=={"REALTIME":60}
    assert runtime.refresh_plan_for_phase("CN","OPEN")=={"INTRADAY":300,"REALTIME":60}
    assert runtime.refresh_plan_for_phase("CN","BREAK")=={"REALTIME":300}
    assert runtime.refresh_plan_for_phase("US","PREOPEN")=={"PREOPEN":300,"REALTIME":60}
    assert runtime.refresh_plan_for_phase("US","OPEN")=={"INTRADAY":300,"REALTIME":60}
    frequency=FrequencyPolicy(engine.store)
    assert frequency.interval("US","REALTIME")==60
    hold=frequency.record_evidence(
        "US","REALTIME",
        evaluated_samples=5,
        incremental_net_return=-0.001,
        incremental_information_gain=-0.01,
        confidence=0.90,
    )
    assert hold["evaluation_action"]=="HOLD_INSUFFICIENT_EVIDENCE"
    assert hold["interval_seconds"]==60
    step=frequency.record_evidence(
        "US","REALTIME",
        evaluated_samples=30,
        incremental_net_return=-0.001,
        incremental_information_gain=-0.01,
        confidence=0.90,
    )
    assert step["evaluation_action"]=="STEP_DOWN_ONE_LEVEL"
    assert step["interval_seconds"]==120
    step_up=frequency.record_evidence(
        "US","REALTIME",
        evaluated_samples=30,
        incremental_net_return=0.002,
        incremental_information_gain=0.01,
        confidence=0.90,
    )
    assert step_up["evaluation_action"]=="STEP_UP_ONE_LEVEL"
    assert step_up["interval_seconds"]==60
    locked=frequency.set_level("US","REALTIME",0,lock=True,reason="selftest")
    assert locked["interval_seconds"]==60
    locked_hold=frequency.record_evidence(
        "US","REALTIME",
        evaluated_samples=50,
        incremental_net_return=-0.01,
        incremental_information_gain=-0.10,
        confidence=0.99,
    )
    assert locked_hold["evaluation_action"]=="MANUAL_LOCK_HOLD"
    assert locked_hold["interval_seconds"]==60
    frequency.unlock("US","REALTIME")
    obs={
        "market_id":"US","mode":"INTRADAY","session_phase":"OPEN",
        "provider":"selftest","quality":"research_intraday","execution_grade":False,
        "source_latest_ts":1234567890,"interval":"5m","points":10,
        "symbols":["SPY"],"latest":{"SPY":{"close":100.0,"volume":1000.0}},
    }
    first_obs=engine.record_market_observation(obs)
    second_obs=engine.record_market_observation(obs)
    obs2={**obs,"source_latest_ts":1234568190,"latest":{"SPY":{"close":101.0,"volume":1200.0}}}
    third_obs=engine.record_market_observation(obs2)
    assert first_obs["recorded"] is True
    assert second_obs["recorded"] is False
    assert third_obs["recorded"] is True
    assert third_obs["transition"] is not None
    assert third_obs["transition"]["research_only"] is True
    assert third_obs["transition"]["action_generated"] is False
    assert abs(third_obs["transition"]["symbol_returns"]["SPY"]-0.01)<1e-12
    obs3={**obs2,"provider":"backup-selftest","source_latest_ts":1234568490,"latest":{"SPY":{"close":102.0,"volume":1300.0}}}
    fourth_obs=engine.record_market_observation(obs3)
    assert fourth_obs["recorded"] is True
    assert fourth_obs["transition"] is None
    assert fourth_obs["observation"]["provider_boundary"] is True
    assert fourth_obs["observation"]["previous_provider"]=="selftest"
    assert engine.market_observation_status()["count"]==3
    assert engine.market_observation_status()["transition_count"]==1
    assert len(engine.market_observations("US","INTRADAY",10))==3
    assert len(engine.market_transitions("US","INTRADAY",10))==1
    caps=engine.market_data_capabilities()
    products=engine.market_data_product_capabilities()
    providers=engine.market_data_provider_status()
    storage=engine.store.status()
    assert storage["backend"]["backend"]=="file"
    assert storage["backend"]["version"]=="file-storage-backend@0.2.0"
    assert storage["durability"] in {"EPHEMERAL","PERSISTENT"}
    assert storage["backend"]["volume"]["expected_mount"]=="/data"
    assert storage["backend"]["volume"]["expected_mount_is_mounted"] is False
    assert storage["backend"]["persistence_probe"]["confirmed_across_deployments"] is False
    assert providers["registry"]["version"]=="provider-registry@0.2.0"
    assert providers["registry"]["routes"]["US:DAILY"]=="research_bars"
    assert providers["registry"]["routes"]["US:QUOTE_L1"]=="us_l1_quotes"
    assert providers["registry"]["chains"]["US:DAILY"]==["research_bars","sina_us_backup"]
    assert providers["registry"]["chains"]["US:INTRADAY"]==["research_bars","sina_us_backup"]
    assert providers["registry"]["chains"]["CN:DAILY"]==["research_bars","tencent_cn_backup"]
    assert providers["registry"]["chains"]["CN:REALTIME"]==["research_bars","tencent_cn_backup"]
    assert providers["registry"]["chains"]["US:PREOPEN"]==["research_bars"]
    assert products["US"]["BAR_DAILY"]["available"] is True
    assert products["US"]["BAR_INTRADAY"]["available"] is True
    assert products["US"]["ORDERBOOK_L2"]["available"] is False
    assert products["US"]["BROKER_FILLS"]["available"] is False
    assert products["CN"]["PREOPEN_AUCTION"]["available"] is False
    assert products["CN"]["ORDERBOOK_L2"]["available"] is False
    assert products["CN"]["DERIVATIVES_CHAIN"]["available"] is False
    assert providers["bar_provider"]["configured"] is True
    quotes=engine.market_data_latest_quotes("CN",["510300.SS"])
    assert quotes["available"] is False
    assert caps["US"]["DAILY"]["supported"] is True
    assert caps["US"]["INTRADAY"]["supported"] is True
    assert caps["US"]["PREOPEN"]["supported"] is True
    assert caps["US"]["REALTIME"]["supported"] is True
    assert caps["US"]["REALTIME"]["execution_grade"] is False
    assert caps["CN"]["PREOPEN"]["supported"] is False
    zh=engine.strategy_population.strategy_cards("zh","US")
    en=engine.strategy_population.strategy_cards("en","US")
    cn=engine.strategy_population.strategy_cards("zh","CN")
    assert len(zh)==29 and len(en)==29
    assert len(cn)==33
    assert {x["strategy_id"] for x in cn if x["strategy_id"].startswith("C")}=={"C29_SIZE_REL20","C30_SIZE_REL63","C32_VOL_BREAKOUT20","C36_BREADTH_ACCEL"}
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

    scheduler=DecisionScheduler(engine)
    assessment=scheduler.assess_transition("US","INTRADAY",third_obs["transition"])
    assert assessment["trigger"] is True
    assert assessment["reason"]=="WARMUP_HIGH_SENSITIVITY"
    auto=scheduler.after_refresh(
        "US",
        "INTRADAY",
        {"market_id":"US","mode":"INTRADAY","session_phase":"OPEN","source_latest_ts":third_obs["transition"]["source_latest_ts"]},
        {"transition":third_obs["transition"]},
    )
    assert auto["enabled"] is True
    assert auto["action"]=="TRANSITION_EVALUATED"
    assert auto["event"]["event_type"]=="TRANSITION_RESEARCH_DECISION"
    assert auto["event"]["decision"]["research_only"] is True
    assert auto["event"]["decision"]["action_generated"] is False
    assert scheduler.status()["broker_execution_enabled"] is False

    negative_transition={
        "source_latest_ts":1234568790,
        "mean_return":-0.02,
        "mean_abs_return":0.02,
        "max_abs_return":0.03,
        "cross_sectional_dispersion":0.01,
        "advancers":0,
        "decliners":3,
    }
    negative_recompute=engine.recompute_transition_research("US",negative_transition,"INTRADAY")
    assert negative_recompute["status"]=="RECOMPUTED"
    assert negative_recompute["transition_regime"]=="intraday_risk_off"
    assert negative_recompute["diagnostics"]["risk_off_detected"] is True
    assert sum(negative_recompute["weights_after"].values()) < sum(negative_recompute["weights_before"].values())

    curves=engine.curves("US")
    assert len(curves)>=1
    daily=engine.daily_summary("US")
    assert daily["evaluated_runs"]>=1

    stale=engine.create_pending_live_run("US")
    reloaded=EvolutionLabEngine()
    assert reloaded.get_run(run.run_id).status=="VERIFIED"
    assert reloaded.get_run(stale.run_id).status=="FAILED"
    assert reloaded.get_run(stale.run_id).diagnostic_summary["error"]=="STALE_INCOMPLETE_RUN_RECOVERED_AFTER_PROCESS_RESTART"
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

    incubator=state("C29_SIZE_REL20",0.20,0.20,0.03)
    incubator.metrics["latest_return"]=0.01
    tracked=reloaded.population_state.apply("CN",[incubator],observation_key="DAILY:2026-09-18")[0]
    tracked_again=reloaded.population_state.apply("CN",[incubator],observation_key="DAILY:2026-09-18")[0]
    assert tracked.lifecycle=="shadow"
    assert tracked.metrics["shadow_live_days"]==1.0
    assert tracked_again.metrics["shadow_live_days"]==1.0
    assert tracked.shadow_evidence_pass is False

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
    print({"strategy_registry_count":reloaded.status()["strategy_registry_count"],"us_strategy_count":len(reloaded.strategy_population.strategy_cards("zh","US")),"cn_strategy_count":len(reloaded.strategy_population.strategy_cards("zh","CN")),"active_core":reloaded.evolution_status()["active_version"]})
finally:
    shutil.rmtree(tmp,ignore_errors=True)
