from __future__ import annotations

import os
import shutil
import tempfile
from types import SimpleNamespace

tmp=tempfile.mkdtemp(prefix="triaid-fin-close-transition-regression-")
os.environ["TRIAID_DATA_DIR"]=tmp
os.environ["TRIAID_STORAGE_BACKEND"]="file"
os.environ["TRIAID_DECISION_MIN_REALTIME_SECONDS"]="300"
os.environ["TRIAID_DECISION_MAD_MULTIPLIER"]="2.0"
os.environ["TRIAID_DECISION_STATE_CONFIRMATIONS"]="3"
os.environ["TRIAID_ALLOCATION_ACTION_L1_THRESHOLD"]="0.05"

from triaid_fin.decision_scheduler import DecisionScheduler
from triaid_fin.observation import MarketObservationStore
from triaid_fin.store import RunStore


class FakeEngine:
    def __init__(self):
        self.store=RunStore()
        self.observations=MarketObservationStore(self.store)
        self.transitions=[]
        self.recompute_calls=0
        self.live_calls=0

    def market_transitions(self,market_id,mode,limit=250):
        return list(self.transitions[-limit:])

    def recompute_transition_research(self,market_id,transition,mode):
        self.recompute_calls+=1
        return {
            "research_only":True,
            "action_generated":False,
            "status":"RECOMPUTED",
            "market_id":market_id,
            "mode":mode,
            "source_latest_ts":transition.get("source_latest_ts"),
            "reference_run_id":"fake-reference",
            "core_version":"fake-core",
            "transition_regime":"intraday_risk_off",
            "weights_before":{"P28_CASH":1.0},
            "weights_after":{"P28_CASH":0.94,"P00_BUY_HOLD":0.06},
            "weight_change_l1_vs_reference":0.06,
            "diagnostics":{},
        }

    def run_live_research(self,market_id):
        self.live_calls+=1
        return SimpleNamespace(
            run_id=f"{market_id}-close-final",
            status="DECISION_READY_AWAITING_OUTCOME",
            market=SimpleNamespace(snapshot_id=f"{market_id}:FINAL:fixture"),
        )

    def latest_decision_run(self,market_id):
        return None


class DedupFinalEngine(FakeEngine):
    def run_live_research(self,market_id):
        self.live_calls+=1
        return SimpleNamespace(
            run_id=f"{market_id}-dedup-final-attempt",
            status="NO_NEW_DATA",
            previous_run_id=f"{market_id}-existing-final",
            market=SimpleNamespace(snapshot_id=f"{market_id}:123:FINAL:fixture"),
        )

    def get_run(self,run_id):
        return SimpleNamespace(
            run_id=run_id,
            status="DECISION_READY_AWAITING_OUTCOME",
            market=SimpleNamespace(snapshot_id="CN:123:FINAL:fixture"),
        )


def transition(ts,positive=True,score=0.001):
    return {
        "market_id":"CN",
        "mode":"REALTIME",
        "provider":"fixture",
        "source_latest_ts":ts,
        "mean_return":score if positive else -score,
        "mean_abs_return":abs(score),
        "max_abs_return":abs(score)*2.0,
        "cross_sectional_dispersion":abs(score),
        "advancers":4 if positive else 1,
        "decliners":1 if positive else 4,
    }


try:
    engine=FakeEngine()

    daily={
        "market_id":"CN",
        "mode":"DAILY",
        "session_phase":"OPEN",
        "provider":"fixture",
        "quality":"research_grade",
        "execution_grade":False,
        "source_latest_ts":1789954200,
        "interval":"1d",
        "points":2405,
        "symbols":["510300.SS"],
        "latest":{"510300.SS":{"close":4.000,"volume":1000.0}},
    }
    first=engine.observations.record(daily)
    duplicate=engine.observations.record(daily)
    revised=engine.observations.record({
        **daily,
        "session_phase":"POSTCLOSE",
        "latest":{"510300.SS":{"close":4.050,"volume":1600.0}},
    })
    assert first["recorded"] is True
    assert duplicate["recorded"] is False
    assert duplicate["reason"]=="DUPLICATE_SNAPSHOT_CONTENT"
    assert revised["recorded"] is True
    assert revised["content_revision"] is True
    assert revised["transition"] is None

    restarted=MarketObservationStore(engine.store)
    restarted_duplicate=restarted.record({
        **daily,
        "session_phase":"POSTCLOSE",
        "latest":{"510300.SS":{"close":4.050,"volume":1600.0}},
    })
    assert restarted_duplicate["recorded"] is False
    assert restarted_duplicate["reason"]=="DUPLICATE_SNAPSHOT_CONTENT"

    scheduler=DecisionScheduler(engine)
    prior=[]
    for i in range(20):
        prior.append(transition(100+i*60,positive=(i%2==0),score=0.001))
    engine.transitions=prior
    noisy=transition(2000,positive=True,score=0.0001)
    assessed=scheduler.assess_transition("CN","REALTIME",noisy)
    assert assessed["trigger"] is False
    assert assessed["reason"]=="BELOW_ADAPTIVE_SALIENCE"

    prior[-3]=transition(100+17*60,positive=False,score=0.001)
    prior[-2]=transition(100+18*60,positive=True,score=0.001)
    prior[-1]=transition(100+19*60,positive=True,score=0.001)
    engine.transitions=prior
    confirmed=transition(2060,positive=True,score=0.0001)
    assessed_confirmed=scheduler.assess_transition("CN","REALTIME",confirmed)
    assert assessed_confirmed["trigger"] is True
    assert assessed_confirmed["reason"]=="CONFIRMED_STATE_CHANGE"

    state=scheduler._market_state("CN")
    state["last_decision_source_ts"]=2000
    scheduler._save()
    skipped=scheduler._transition_decision("CN","REALTIME",confirmed)
    assert skipped is None
    assert engine.recompute_calls==0
    assert scheduler.events("CN",1)[-1]["assessment"]["reason"]=="MIN_RECOMPUTE_INTERVAL"

    confirmed_later={**confirmed,"source_latest_ts":2300}
    event=scheduler._transition_decision("CN","REALTIME",confirmed_later)
    assert event is not None
    assert engine.recompute_calls==1
    assert event["decision"]["decision_layer"]=="ALLOCATION_ACTION_CANDIDATE"
    assert event["decision"]["allocation_change_recommended"] is True
    assert scheduler._market_state("CN")["allocation_action_count"]==1

    close_scheduler=DecisionScheduler(engine)
    close_state=close_scheduler._market_state("CN")
    close_state["close_done"]=False
    close_state["close_event_id"]=None
    close_state["close_wait_signature"]=None
    close_scheduler._save()

    postclose_snapshot={
        **daily,
        "session_phase":"POSTCLOSE",
        "latest":{"510300.SS":{"close":4.050,"volume":1600.0}},
    }
    close_scheduler._postclose_settled=lambda market_id: False
    wait_event=close_scheduler._close(
        "CN",
        postclose_snapshot,
        {"recorded":False,"reason":"DUPLICATE_SNAPSHOT_CONTENT"},
    )
    assert wait_event is not None
    assert wait_event["event_type"]=="CLOSE_WAITING_FOR_NEW_DAILY_DATA"
    assert close_scheduler._market_state("CN")["close_done"] is False
    assert engine.live_calls==0

    close_scheduler._postclose_settled=lambda market_id: True
    recovered_event=close_scheduler._close(
        "CN",
        postclose_snapshot,
        {"recorded":False,"reason":"DUPLICATE_SNAPSHOT_CONTENT"},
    )
    assert recovered_event is not None
    assert recovered_event["event_type"]=="CLOSE_FINAL"
    assert recovered_event["recovered_from_duplicate_content"] is True
    assert close_scheduler._market_state("CN")["close_done"] is True
    assert engine.live_calls==1

    close_state=close_scheduler._market_state("CN")
    close_state["close_done"]=False
    close_state["close_event_id"]=None
    close_state["last_close_signature"]=None
    close_scheduler._save()
    final_event=close_scheduler._close(
        "CN",
        {
            **postclose_snapshot,
            "latest":{"510300.SS":{"close":4.060,"volume":1700.0}},
        },
        {"recorded":True,"content_revision":True},
    )
    assert final_event is not None
    assert final_event["event_type"]=="CLOSE_FINAL"
    assert close_scheduler._market_state("CN")["close_done"] is True
    assert engine.live_calls==2

    dedup_engine=DedupFinalEngine()
    dedup_scheduler=DecisionScheduler(dedup_engine)
    dedup_state=dedup_scheduler._market_state("CN")
    dedup_state["close_done"]=False
    dedup_state["close_event_id"]=None
    dedup_state["last_close_signature"]=dedup_engine.observations.snapshot_signature(postclose_snapshot)
    dedup_state["close_wait_signature"]=dedup_state["last_close_signature"]
    dedup_scheduler._save()
    dedup_scheduler._postclose_settled=lambda market_id: True
    dedup_final=dedup_scheduler._close(
        "CN",
        postclose_snapshot,
        {"recorded":False,"reason":"DUPLICATE_SNAPSHOT_CONTENT"},
    )
    assert dedup_final is not None
    assert dedup_final["event_type"]=="CLOSE_FINAL"
    assert dedup_final["run_status"]=="NO_NEW_DATA"
    assert dedup_final["close_reference_run_id"]=="CN-existing-final"
    assert dedup_final["close_completion_basis"]=="EXISTING_COMPLETE_FINAL_SNAPSHOT"
    assert dedup_scheduler._market_state("CN")["close_done"] is True

    print("TRIAID_CLOSE_TRANSITION_REGRESSION_PASS")
    print({
        "observation_version":engine.observations.version,
        "scheduler_version":scheduler.version,
        "mad_multiplier":scheduler.mad_multiplier,
        "state_confirmations":scheduler.state_confirmations,
        "min_recompute_seconds":scheduler.min_recompute_seconds,
        "allocation_action_l1_threshold":scheduler.allocation_action_l1_threshold,
    })
finally:
    shutil.rmtree(tmp,ignore_errors=True)
