from triaid_fin.decision_scheduler import DecisionScheduler


class Store:
    def __init__(self):
        self.data={}
        self.lines=[]
    def load_json(self,name,default=None):
        return self.data.get(name,default)
    def save_json(self,name,value):
        self.data[name]=value
    def append_jsonl(self,name,row):
        self.lines.append((name,row))
    def read_jsonl(self,name,limit=100):
        return [row for key,row in self.lines if key==name][-limit:]


class Engine:
    def __init__(self):
        self.store=Store()
        self.regime="intraday_risk_on"
        self.l1=0.4
    def market_transitions(self,market,mode,limit):
        return []
    def recompute_transition_research(self,market,transition,mode):
        return {
            "transition_regime":self.regime,
            "weight_change_l1_vs_reference":self.l1,
            "weights_before":{},
            "weights_after":{},
        }


engine=Engine()
scheduler=DecisionScheduler(engine)

scheduler.assess_transition=lambda market,mode,transition:{
    "trigger":True,
    "urgent":True,
    "reason":"ROBUST_SALIENCE",
    "confirmed_state_change":False,
}
row=scheduler._transition_decision("CN","REALTIME",{"source_latest_ts":100})
decision=row["decision"]
assert decision["raw_allocation_candidate"] is True
assert decision["risk_increase_candidate"] is True
assert decision["persistence_gate_pass"] is False
assert decision["allocation_change_recommended"] is False
assert decision["decision_layer"]=="STATE_ONLY_RISK_INCREASE_AWAITING_CONFIRMATION"

scheduler.assess_transition=lambda market,mode,transition:{
    "trigger":True,
    "urgent":False,
    "reason":"CONFIRMED_STATE_CHANGE",
    "confirmed_state_change":True,
}
row=scheduler._transition_decision("CN","REALTIME",{"source_latest_ts":500})
decision=row["decision"]
assert decision["persistence_gate_pass"] is True
assert decision["allocation_change_recommended"] is True

engine.regime="intraday_risk_off"
scheduler.assess_transition=lambda market,mode,transition:{
    "trigger":True,
    "urgent":True,
    "reason":"ROBUST_SALIENCE",
    "confirmed_state_change":False,
}
row=scheduler._transition_decision("CN","REALTIME",{"source_latest_ts":900})
decision=row["decision"]
assert decision["risk_increase_candidate"] is False
assert decision["persistence_gate_pass"] is True
assert decision["allocation_change_recommended"] is True
print("TRIAID_TRANSITION_TRIAGE_GATE_SMOKE_PASS")
