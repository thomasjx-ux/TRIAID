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
from triaid_fin.observation import MarketObservationStore
from triaid_fin.trading_calendar import trading_day_info, official_session_phase, calendar_status, install_synced_calendar
from triaid_fin.trading_calendar_sync import parse_nyse_calendar, parse_cn_notice


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
    assert engine.status()["architecture_version"]=="fin-evolution-lab@0.13.1"
    runtime=MarketDataAutomation(engine)
    assert session_phase("CN",datetime(2026,9,22,9,20,tzinfo=ZoneInfo("Asia/Shanghai")))=="PREOPEN"
    assert session_phase("CN",datetime(2026,9,22,10,0,tzinfo=ZoneInfo("Asia/Shanghai")))=="OPEN"
    assert session_phase("CN",datetime(2026,9,22,12,0,tzinfo=ZoneInfo("Asia/Shanghai")))=="BREAK"
    assert session_phase("CN",datetime(2026,9,22,14,0,tzinfo=ZoneInfo("Asia/Shanghai")))=="OPEN"
    assert session_phase("CN",datetime(2026,9,22,15,5,tzinfo=ZoneInfo("Asia/Shanghai")))=="POSTCLOSE"
    assert session_phase("US",datetime(2026,9,22,8,0,tzinfo=ZoneInfo("America/New_York")))=="PREOPEN"
    assert session_phase("US",datetime(2026,9,22,10,0,tzinfo=ZoneInfo("America/New_York")))=="OPEN"

    # Official exchange calendar gates.
    us_holiday=trading_day_info("US","2026-07-03")
    assert us_holiday["calendar_known"] is True
    assert us_holiday["is_trading_day"] is False
    assert us_holiday["reason"]=="OFFICIAL_EXCHANGE_HOLIDAY"
    assert official_session_phase(
        "US",
        datetime(2026,7,3,10,0,tzinfo=ZoneInfo("America/New_York")),
    )=="CLOSED"

    us_early=trading_day_info("US","2026-11-27")
    assert us_early["is_trading_day"] is True
    assert us_early["early_close"] is True
    assert us_early["early_close_time"]=="13:00"
    assert official_session_phase(
        "US",
        datetime(2026,11,27,12,30,tzinfo=ZoneInfo("America/New_York")),
    )=="OPEN"
    assert official_session_phase(
        "US",
        datetime(2026,11,27,14,0,tzinfo=ZoneInfo("America/New_York")),
    )=="POSTCLOSE"

    cn_holiday=trading_day_info("CN","2026-09-25")
    assert cn_holiday["calendar_known"] is True
    assert cn_holiday["is_trading_day"] is False
    assert official_session_phase(
        "CN",
        datetime(2026,9,25,10,0,tzinfo=ZoneInfo("Asia/Shanghai")),
    )=="CLOSED"
    cn_open=trading_day_info("CN","2026-09-28")
    assert cn_open["is_trading_day"] is True
    cn_unknown=trading_day_info("CN","2027-01-04")
    assert cn_unknown["calendar_known"] is False
    assert cn_unknown["reason"]=="CALENDAR_YEAR_UNAVAILABLE"
    assert official_session_phase(
        "CN",
        datetime(2027,1,4,10,0,tzinfo=ZoneInfo("Asia/Shanghai")),
    )=="CALENDAR_UNAVAILABLE"
    cal=calendar_status()
    assert cal["version"]=="official-trading-calendar@0.2.0"
    assert cal["markets"]["US"]["coverage_years"]==[2026,2027,2028]
    assert cal["markets"]["CN"]["coverage_years"]==[2026]

    nyse_fixture="""
    <table>
      <tr><th>Holiday</th><th>2026</th><th>2027</th><th>2028</th></tr>
      <tr><td>New Year’s Day</td><td>Thursday, January 1</td><td>Friday, January 1</td><td>—</td></tr>
      <tr><td>Martin Luther King, Jr. Day</td><td>Monday, January 19</td><td>Monday, January 18</td><td>Monday, January 17</td></tr>
      <tr><td>Washington's Birthday</td><td>Monday, February 16</td><td>Monday, February 15</td><td>Monday, February 21</td></tr>
      <tr><td>Good Friday</td><td>Friday, April 3</td><td>Friday, March 26</td><td>Friday, April 14</td></tr>
      <tr><td>Memorial Day</td><td>Monday, May 25</td><td>Monday, May 31</td><td>Monday, May 29</td></tr>
      <tr><td>Juneteenth</td><td>Friday, June 19</td><td>Friday, June 18</td><td>Monday, June 19</td></tr>
      <tr><td>Independence Day</td><td>Friday, July 3</td><td>Monday, July 5</td><td>Tuesday, July 4</td></tr>
      <tr><td>Labor Day</td><td>Monday, September 7</td><td>Monday, September 6</td><td>Monday, September 4</td></tr>
      <tr><td>Thanksgiving Day</td><td>Thursday, November 26</td><td>Thursday, November 25</td><td>Thursday, November 23</td></tr>
      <tr><td>Christmas Day</td><td>Friday, December 25</td><td>Friday, December 24</td><td>Monday, December 25</td></tr>
    </table>
    Each market will close early at 1:00 p.m. on Friday, November 27, 2026.
    """
    nyse_parsed=parse_nyse_calendar(nyse_fixture)
    assert len(nyse_parsed[2026]["closed"])==10
    assert nyse_parsed[2026]["early_close"]["2026-11-27"]=="13:00"

    cn_fixture="""
    <h1>关于2027年部分节假日休市安排的通知</h1>
    元旦：1月1日至1月3日休市。
    春节：2月5日至2月13日休市。
    清明节：4月3日至4月5日休市。
    劳动节：5月1日至5月5日休市。
    端午节：6月9日至6月11日休市。
    中秋节：9月15日至9月17日休市。
    国庆节：10月1日至10月7日休市。
    """
    cn_parsed=parse_cn_notice(cn_fixture,2027)
    assert datetime(2027,10,1).date() in cn_parsed
    assert len(cn_parsed)>=30

    install_synced_calendar({
        "version":"official-trading-calendar-sync@selftest",
        "markets":{
            "CN":{
                "years":{
                    "2027":{
                        "validated":True,
                        "validated_at":"2026-12-20T00:00:00+00:00",
                        "validation":"SELFTEST_DUAL_OFFICIAL_MATCH",
                        "closed":[d.isoformat() for d in sorted(cn_parsed)],
                        "early_close":{},
                        "sources":[{"name":"SSE"},{"name":"SZSE"}],
                    }
                }
            }
        }
    })
    assert trading_day_info("CN","2027-10-01")["calendar_origin"]=="SYNCED_OFFICIAL"
    assert trading_day_info("CN","2027-10-01")["is_trading_day"] is False
    install_synced_calendar({})
    assert runtime.refresh_plan_for_phase("CN","PREOPEN")=={"PREOPEN":300,"REALTIME":60}
    assert runtime.refresh_plan_for_phase("CN","OPEN")=={"INTRADAY":300,"REALTIME":60}
    assert runtime.refresh_plan_for_phase("CN","BREAK")=={"REALTIME":300}
    assert runtime.refresh_plan_for_phase("US","PREOPEN")=={"PREOPEN":300,"REALTIME":60}
    assert runtime.refresh_plan_for_phase("US","OPEN")=={"INTRADAY":300,"REALTIME":60}
    assert runtime.refresh_plan_for_phase("US","CLOSED")=={}
    assert runtime.refresh_plan_for_phase("CN","CLOSED")=={}
    assert runtime.refresh_plan_for_phase("CN","CALENDAR_UNAVAILABLE")=={}
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
    stale_obs={**obs2,"source_latest_ts":1234568000,"latest":{"SPY":{"close":99.0,"volume":900.0}}}
    rejected_stale=engine.record_market_observation(stale_obs)
    assert rejected_stale["recorded"] is False
    assert rejected_stale["reason"]=="STALE_SOURCE_TIMESTAMP"
    assert rejected_stale["max_source_latest_ts"]==1234568490
    engine.store.save_json(
        "market_observation_watermarks.json",
        {"US:INTRADAY":1234568190},
    )
    healing_observations=MarketObservationStore(engine.store)
    duplicate_heal=healing_observations.record(obs3)
    assert duplicate_heal["recorded"] is False
    assert duplicate_heal["reason"]=="DUPLICATE_SNAPSHOT_CONTENT"
    assert healing_observations.watermarks["US:INTRADAY"]==1234568490
    restarted_observations=MarketObservationStore(engine.store)
    assert restarted_observations.watermarks["US:INTRADAY"]==1234568490
    rejected_after_restart=restarted_observations.record(stale_obs)
    assert rejected_after_restart["recorded"] is False
    assert rejected_after_restart["reason"]=="STALE_SOURCE_TIMESTAMP"
    assert rejected_after_restart["max_source_latest_ts"]==1234568490
    assert engine.market_observation_status()["count"]==3
    assert engine.market_observation_status()["transition_count"]==1
    assert len(engine.market_observations("US","INTRADAY",10))==3
    assert len(engine.market_transitions("US","INTRADAY",10))==1
    rt1={
        "market_id":"US","mode":"REALTIME","session_phase":"OPEN",
        "provider":"realtime-selftest","quality":"indicative_not_execution_grade","execution_grade":False,
        "source_latest_ts":1234569000,"interval":"1m","points":2,
        "symbols":["SPY"],"latest":{"SPY":{"close":500.0,"volume":2000.0}},
    }
    rt2={**rt1,"source_latest_ts":1234569060,"points":3,"latest":{"SPY":{"close":505.0,"volume":2200.0}}}
    assert engine.record_market_observation(rt1)["recorded"] is True
    assert engine.record_market_observation(rt2)["recorded"] is True
    live_window=runtime.live_indicators("US")
    assert live_window["available"] is True
    assert live_window["provider"]=="realtime-selftest"
    assert abs(live_window["instruments"][0]["change_pct"]-0.01)<1e-12
    activity_window=runtime.activity("US",20)
    assert any(x["kind"]=="DATA_FETCH" for x in activity_window["events"])
    assert "refresh_plan" in activity_window
    caps=engine.market_data_capabilities()
    products=engine.market_data_product_capabilities()
    providers=engine.market_data_provider_status()
    storage=engine.store.status()
    calibration=engine.execution_calibration_status()
    assert calibration["version"]=="execution-calibration@0.1.0"
    assert calibration["US"]["automatic_parameter_mutation"] is False
    assert calibration["CN"]["automatic_parameter_mutation"] is False
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
            metadata={"experiment_mode":"US_RETURN_MAX_CAPACITY"},
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
    assert decision.strategy_group.diagnostics["optimizer"]=="relative-return-max-v1"
    assert engine.strategy_population.config_for("US").redundancy_penalty==0.0
    assert engine.strategy_population.config_for("US").near_duplicate_corr>1.0
    assert all(w>=0 for w in decision.strategy_group.weights.values())
    assert sum(decision.strategy_group.weights.values())<=1.0000001
    assert max(decision.strategy_group.weights.values())<=1.0000001
    assert decision.triaid_decision
    assert decision.triaid_decision.core_version==engine.evolution.active().version
    assert sum(decision.triaid_decision.weights_after.values())<=1.0000001

    cn_ids=[
        "P00_BUY_HOLD","P01_VOL10","P02_VOL15","P03_DD_GUARD","P04_TREND50",
        "P05_TREND200","P06_DUAL_TREND","P07_MOM63","P08_STRESS_BLEND","P09_SHOCK_GUARD",
        "P10_VOL20","P11_TREND20",
    ]
    cn_states=[
        state(sid,-0.02-0.01*i,0.10+0.005*i,0.01)
        for i,sid in enumerate(cn_ids)
    ]+[state("P28_CASH",0.0,0.0,0.0)]
    assert engine.strategy_population.rules("CN")["active_research_experiment"]=="CN_RETURN_MAX_CAPACITY"
    assert engine.strategy_population.rules("CN")["market_route"]=="CN_RETURN_MAXIMIZATION"

    cn_request=RunRequest(
        market=MarketSnapshot(
            market_id="CN",
            as_of="2026-09-21",
            snapshot_id="SELFTEST:CN:RETURNMAX",
            regime="risk_on",
            metadata={"experiment_mode":"CN_RETURN_MAX_CAPACITY"},
        ),
        strategy_states=cn_states,
        max_group_size=12,
    )
    cn_run=engine.create_run(cn_request)
    engine.execute(cn_run.run_id,cn_request)
    cn_decision=engine.get_run(cn_run.run_id)
    assert cn_decision.status=="DECISION_READY_AWAITING_OUTCOME"
    assert cn_decision.strategy_group
    assert cn_decision.strategy_group.diagnostics["optimizer"]=="relative-return-max-v1"
    assert cn_decision.strategy_group.diagnostics["absolute_sign_used_as_cash_gate"] is False
    assert cn_decision.strategy_group.diagnostics["uncertainty_used_as_additive_penalty"] is False
    risky_members=[x for x in cn_decision.strategy_group.members if x!="P28_CASH"]
    assert len(risky_members)==12
    assert "P28_CASH" in cn_decision.strategy_group.members
    assert abs(cn_decision.strategy_group.weights.get("P28_CASH",0.0))<1e-12
    assert abs(sum(cn_decision.strategy_group.weights.values())-1.0)<1e-9
    assert cn_decision.triaid_decision
    assert cn_decision.triaid_decision.diagnostics["cash_is_fixed_template"] is False
    assert cn_decision.triaid_decision.diagnostics["regime_policy"]=="RISK_ON_FULL_ADMISSIBLE_GROUP"
    assert cn_decision.triaid_decision.weights_after.get("P28_CASH",0.0)<1e-12

    risk_off_market=cn_request.market.model_copy(deep=True)
    risk_off_market.regime="intraday_risk_off"
    risk_off=engine.core.decide(risk_off_market,cn_decision.strategy_group,cn_states)
    assert risk_off.diagnostics["regime_policy"]=="RISK_OFF_TOP_3"
    assert 0.0<risk_off.weights_after.get("P28_CASH",0.0)<0.5
    assert risk_off.weights_after!=cn_decision.triaid_decision.weights_after

    severe_market=cn_request.market.model_copy(deep=True)
    severe_market.regime="shock_high_vol"
    severe=engine.core.decide(severe_market,cn_decision.strategy_group,cn_states)
    assert severe.diagnostics["regime_policy"]=="SEVERE_RISK_TOP_2"
    assert severe.weights_after.get("P28_CASH",0.0)>risk_off.weights_after.get("P28_CASH",0.0)

    # The former worst-pool rescue remains available only as an explicit stress test.
    cn_stress_request=RunRequest(
        market=MarketSnapshot(
            market_id="CN",
            as_of="2026-09-21",
            snapshot_id="SELFTEST:CN:STRESS",
            regime="risk_off",
            metadata={"experiment_mode":"CN_WORST_POOL_RESCUE"},
        ),
        strategy_states=cn_states,
        max_group_size=12,
    )
    cn_stress_run=engine.create_run(cn_stress_request)
    engine.execute(cn_stress_run.run_id,cn_stress_request)
    cn_stress=engine.get_run(cn_stress_run.run_id)
    assert cn_stress.strategy_group.diagnostics["optimizer"]=="adversarial-worst-pool-v1"
    assert cn_stress.strategy_group.diagnostics["experiment_mode"]=="CN_WORST_POOL_RESCUE"

    # Primary-reference selection must never be hijacked by a later stress run.
    assert engine.primary_experiment_mode("CN")=="CN_RETURN_MAX_CAPACITY"
    assert engine.primary_experiment_mode("US")=="US_RETURN_MAX_CAPACITY"
    assert engine.latest_decision_run("CN").run_id==cn_decision.run_id
    assert engine.latest_decision_run("CN",primary_only=False).run_id==cn_stress.run_id
    cn_primary_receipt=engine.ensure_primary_reference("CN")
    assert cn_primary_receipt["created"] is False
    assert cn_primary_receipt["run_id"]==cn_decision.run_id
    us_primary_receipt=engine.ensure_primary_reference("US")
    assert us_primary_receipt["created"] is False
    assert us_primary_receipt["run_id"]==decision.run_id

    # Main reports and curves must exclude the explicitly evaluated stress route.
    cn_realized={sid:0.001 for sid in cn_ids}
    cn_realized["P28_CASH"]=0.0
    engine.submit_outcome(
        cn_decision.run_id,
        OutcomeRequest(realized_returns=cn_realized,trading_cost=0.0),
    )
    engine.submit_outcome(
        cn_stress.run_id,
        OutcomeRequest(realized_returns=cn_realized,trading_cost=0.0),
    )
    cn_daily=engine.daily_summary("CN")
    assert cn_daily["evaluated_runs"]==1
    assert cn_daily["runs_detail"][0]["experiment_mode"]=="CN_RETURN_MAX_CAPACITY"
    assert all(
        row.get("run_id")!=cn_stress.run_id
        for row in cn_daily.get("runs_detail",[])
    )
    cn_curve=engine.curves("CN")
    assert len(cn_curve)==1
    assert cn_curve[0]["run_id"]==cn_decision.run_id

    verified=engine.submit_outcome(
        run.run_id,
        OutcomeRequest(
            realized_returns={
                "P00_BUY_HOLD":0.004,
                "P09_SHOCK_GUARD":0.003,
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
    assert assessment["reason"]=="WARMUP_CALIBRATION"
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
    assert negative_recompute["diagnostics"]["regime_policy"]=="RISK_OFF_TOP_3"
    assert negative_recompute["weights_after"].get("P28_CASH",0.0) > negative_recompute["weights_before"].get("P28_CASH",0.0)

    curves=engine.curves("US")
    assert len(curves)>=1
    daily=engine.daily_summary("US")
    assert daily["evaluated_runs"]>=1

    stale=engine.create_pending_live_run("US")
    reloaded=EvolutionLabEngine()
    assert reloaded.get_run(run.run_id).status=="VERIFIED"
    assert reloaded.get_run(stale.run_id).status=="FETCHING_DATA"
    recovery_receipt=reloaded.recover_stale_runs()
    assert recovery_receipt["recovered_run_ids"]==[stale.run_id]
    assert reloaded.get_run(stale.run_id).status=="FAILED"
    assert reloaded.get_run(stale.run_id).diagnostic_summary["error"]=="STALE_INCOMPLETE_RUN_RECOVERED_AFTER_PROCESS_RESTART"
    assert len(reloaded.all_runs())>=1

    before=reloaded.evolution_status()["active_version"]
    proposal=reloaded.propose_core_candidate()
    assert proposal["created"] is False
    assert proposal["reason"]=="INSUFFICIENT_MARKET_STRATIFIED_POSTERIOR_EVALUATIONS"
    assert proposal["required_per_market"]==10
    assert reloaded.evolution_status()["active_version"]==before

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
