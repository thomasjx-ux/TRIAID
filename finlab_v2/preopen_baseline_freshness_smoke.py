from types import SimpleNamespace

from triaid_fin.decision_scheduler import DecisionScheduler


class Store:
    def __init__(self):
        self.state={}
        self.events=[]
    def load_json(self,name,default=None):
        return self.state.get(name,default)
    def save_json(self,name,value):
        self.state[name]=value
    def append_jsonl(self,name,row):
        self.events.append(row)
    def read_jsonl(self,name,limit=100):
        return self.events[-limit:]


def ref(run_id,as_of,weight=1.0):
    return SimpleNamespace(
        run_id=run_id,
        market=SimpleNamespace(as_of=as_of),
        triaid_decision=SimpleNamespace(weights_after={"P00_BUY_HOLD":weight}),
    )


class Engine:
    def __init__(self,refresh_succeeds=True):
        self.store=Store()
        self.reference=ref("old","2026-09-22",1.0)
        self.refresh_succeeds=refresh_succeeds
        self.run_calls=0
    def latest_decision_run(self,market):
        return self.reference
    def run_live_research(self,market):
        self.run_calls+=1
        if self.refresh_succeeds:
            self.reference=ref("fresh","2026-09-23",0.8)
        return SimpleNamespace(
            run_id="attempt",
            status="DECISION_READY_AWAITING_OUTCOME" if self.refresh_succeeds else "NO_NEW_DATA",
        )
    def market_transitions(self,*args,**kwargs):
        return []
    def recompute_transition_research(self,*args,**kwargs):
        return {}


engine=Engine(True)
scheduler=DecisionScheduler(engine)
scheduler._previous_trading_day=lambda market:"2026-09-23"
state=scheduler._market_state("US")
state["baseline_done"]=True
state["baseline_event_id"]="legacy-event"
state["baseline_fresh"]=False
state["baseline_reference_as_of"]="2026-09-22"
state["baseline_refresh_attempt_epoch"]=0

row=scheduler._ensure_baseline("US","PREOPEN")
assert row is not None
assert row["event_type"]=="PREOPEN_BASELINE_REFRESHED"
assert row["expected_reference_as_of"]=="2026-09-23"
assert row["reference_as_of"]=="2026-09-23"
assert row["reference_fresh"] is True
assert row["reference_run_id"]=="fresh"
assert engine.run_calls==1
state=scheduler._market_state("US")
assert state["baseline_done"] is True
assert state["baseline_fresh"] is True
assert state["baseline_reference_as_of"]=="2026-09-23"

again=scheduler._ensure_baseline("US","PREOPEN")
assert again is None
assert engine.run_calls==1

failed_engine=Engine(False)
failed=DecisionScheduler(failed_engine)
failed._previous_trading_day=lambda market:"2026-09-23"
failed_state=failed._market_state("US")
failed_state["baseline_done"]=True
failed_state["baseline_reference_as_of"]="2026-09-22"
failed_state["baseline_refresh_attempt_epoch"]=0
row=failed._ensure_baseline("US","PREOPEN")
assert row["event_type"]=="PREOPEN_BASELINE_STALE"
assert row["reference_fresh"] is False
assert failed._market_state("US")["baseline_done"] is False
assert failed._market_state("US")["baseline_fresh"] is False

print("TRIAID_PREOPEN_BASELINE_FRESHNESS_SMOKE_PASS")
