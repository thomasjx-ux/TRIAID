from __future__ import annotations
from datetime import datetime, timedelta, timezone
from triaid_fin.hazard_prospective import HazardProspectiveLedger

start=datetime(2020,1,1,tzinfo=timezone.utc)
series={
    "ts":[int((start+timedelta(days=i)).timestamp()) for i in range(400)],
    "close":[100.0+i*0.10 for i in range(400)],
}
m=HazardProspectiveLedger._future_metrics(series,"2020-01-10",20)
assert m is not None
assert m["horizon_trading_days"]==20
assert m["return"]>0
assert m["max_loss_from_start"]>0
assert m["max_gain_from_start"]>0

class FakeStore:
    def __init__(self):
        self.json={}
        self.journal={}
    def load_json(self,name,default=None):
        return self.json.get(name,default)
    def save_json(self,name,payload):
        self.json[name]=payload
        return payload
    def read_jsonl(self,name,limit=100):
        return list(self.journal.get(name,[]))[-int(limit):]
    def append_jsonl(self,name,payload):
        self.journal.setdefault(name,[]).append(payload)
        return payload

store=FakeStore()
ledger=HazardProspectiveLedger(store)
hazard={
    "as_of":"2026-09-21",
    "experiment_id":"LATENT-TEST",
    "experiment_hash":"abc123",
    "current_state":{
        "state_label":"SUPPORTED_HAZARD_ACTIVE",
        "statistically_supported_composites_triggered":["POLICY_REPRICING_STRESS"],
    },
    "statistically_supported_composite_rows":[],
}
curve={
    "as_of":"2026-09-22",
    "snapshot_id":"CURVE-TEST",
    "metrics":{"fed_funds":{"available":True,"contracts":12}},
}
frozen=ledger.freeze(hazard,curve)
assert frozen["as_of"]=="2026-09-22"
assert frozen["hazard_signal_as_of"]=="2026-09-21"
assert frozen["policy_curve_as_of"]=="2026-09-22"
assert frozen["component_lag_calendar_days"]==1
assert frozen["evidence_eligible"] is True
assert "never backdated" in frozen["time_alignment_guard"]

# Legacy mismatched record must be invalidated before a new evidence-bearing freeze.
legacy={
    **frozen,
    "ledger_id":"LEGACY",
    "version":"hazard-prospective-ledger@0.2.0",
    "as_of":"2026-09-20",
    "policy_curve_as_of":"2026-09-21",
    "evidence_eligible":True,
    "source_experiment_hash":"legacy-hash",
    "policy_curve_snapshot_id":"legacy-curve",
}
store.save_json(ledger.state_file,{"version":"old","rows":[legacy]})
hazard2={**hazard,"as_of":"2026-09-22","experiment_hash":"new-hash"}
curve2={**curve,"as_of":"2026-09-22","snapshot_id":"new-curve"}
newrow=ledger.freeze(hazard2,curve2)
rows=ledger.rows(100)
assert rows[0]["evidence_eligible"] is False
assert rows[0]["invalidated_reason"]=="PRE_V0_3_MIXED_TIMESTAMP_LOOKAHEAD_LABEL"
assert newrow["as_of"]=="2026-09-22"
assert newrow["evidence_eligible"] is True

print("TRIAID_HAZARD_PROSPECTIVE_SMOKE_PASS",{
    "future_metrics":m,
    "aligned_as_of":frozen["as_of"],
    "legacy_invalidated":rows[0]["invalidated_reason"],
})
