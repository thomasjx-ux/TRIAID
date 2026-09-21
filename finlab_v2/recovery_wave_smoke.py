from __future__ import annotations

import math
from copy import deepcopy

from triaid_fin.evolution import CoreParameters
from triaid_fin.market_lab import MarketPanel, MarketSpec
from triaid_fin.recovery_core import RecoveryWaveCore
from triaid_fin.recovery_ledger import RecoveryWaveLedger


class MemoryStore:
    def __init__(self):
        self.json={}
        self.lines={}

    def load_json(self,name,default=None):
        if name not in self.json:
            return deepcopy({} if default is None else default)
        return deepcopy(self.json[name])

    def save_json(self,name,payload):
        self.json[name]=deepcopy(payload)

    def append_jsonl(self,name,payload):
        self.lines.setdefault(name,[]).append(deepcopy(payload))

    def read_jsonl(self,name,limit=None):
        rows=deepcopy(self.lines.get(name,[]))
        return rows[-limit:] if limit else rows


spec=MarketSpec(
    "CN","510300.SS",
    ("510300.SS","510500.SS","159915.SZ","512100.SS","511010.SS"),
    ("510300.SS","510500.SS","159915.SZ","512100.SS"),
    ("511010.SS",),
    "CNY",50_000_000.0,2.5,60.0,0.02,
)

n=430
ts=list(range(1_700_000_000,1_700_000_000+n*86400,86400))
assets=spec.assets
close={}
volume={}
for j,symbol in enumerate(assets):
    px=100.0+4*j
    xs=[]
    vs=[]
    for i in range(n):
        cyc=0.0018*math.sin((i+j*7)/13.0)+0.0011*math.cos((i+j*3)/29.0)
        shock=-0.012 if 360<=i<370 and j<4 else 0.0
        rebound=0.0045 if 390<=i<410 and j in {0,2} else 0.00015*(j-1)
        r=cyc+shock+rebound
        px=max(1.0,px*(1.0+r))
        xs.append(px)
        vs.append(1_000_000.0*(1.0+0.15*math.sin((i+j)/9.0))+10_000*j)
    close[symbol]=xs
    volume[symbol]=vs

panel=MarketPanel(
    spec=spec,
    ts=ts,
    close=close,
    volume=volume,
    data_mode="DAILY",
    provider="selftest",
    quality="research_daily",
)

core=RecoveryWaveCore(CoreParameters(version="selftest",risk_off_multiplier=0.55))
decision=core.decide(panel,"risk_off",None,"POSTCLOSE")
assert decision["core_version"]=="recovery-wave-core@0.4.0"
assert decision["research_only"] is True
assert decision["broker_execution_enabled"] is False
assert decision["data_scope"]["constituent_micro_available"] is False
assert decision["data_scope"]["micro_scope"]=="TRACKED_PRODUCT_PRICE_VOLUME_ONLY"
assert decision["execution_discipline"]["same_bar_execution_allowed"] is False
assert decision["execution_discipline"]["execution_rule"]=="DECISION_AT_T_APPLIES_FROM_NEXT_COMPLETE_TRADABLE_BAR"
assert set(decision["first_order_states"])==set(spec.risk_assets)
assert len(decision["trade_opinions"])==4
allocated=sum(x["target_weight"] for x in decision["trade_opinions"])
assert decision["second_order"]["base_risk_budget"]==0.55
assert 0.0<=decision["second_order"]["evidence_strength"]<=1.0
assert 0.0<=decision["second_order"]["effective_risk_budget"]<=0.55
assert allocated<=decision["second_order"]["effective_risk_budget"]+1e-8
assert abs(decision["cash_residual_weight"]-(1.0-allocated))<1e-8
assert decision["capital_capacity"]["enabled"] is True
assert decision["capital_capacity"]["version"]=="capital-capacity-layer@0.1.0"
assert decision["capital_capacity"]["capital_sleeves_cny"]==[100000,1000000,10000000,100000000]
assert len(decision["capital_capacity"]["sleeves"])==4
for row in decision["trade_opinions"]:
    assert row["action"] in {"INITIATE","ADD","HOLD","REDUCE","EXIT","WAIT"}
    assert row["analog_samples"]<=20
    if row["expected_reversal_horizon_days"] is not None:
        assert row["expected_reversal_horizon_days"] in {3,5,10,20}

store=MemoryStore()
ledger=RecoveryWaveLedger(store)
legacy_variant=deepcopy(decision)
legacy_variant["core_version"]="recovery-wave-core@0.1.1"
legacy_same_snapshot=ledger.freeze(legacy_variant,"CN:SNAP:1","2026-09-21")

first=ledger.freeze(decision,"CN:SNAP:1","2026-09-21")
dup=ledger.freeze(decision,"CN:SNAP:1","2026-09-21")
assert dup["decision_id"]==first["decision_id"]
assert dup["decision_hash"]==first["decision_hash"]
assert first["previous_decision_id"]==legacy_same_snapshot["decision_id"]
assert first["previous_decision_hash"]==legacy_same_snapshot["decision_hash"]
assert legacy_same_snapshot["decision_id"]!=first["decision_id"]
assert ledger.by_snapshot("CN","CN:SNAP:1","recovery-wave-core@0.4.0")["decision_id"]==first["decision_id"]
assert ledger.by_snapshot("CN","CN:SNAP:1","recovery-wave-core@0.1.1")["decision_id"]==legacy_same_snapshot["decision_id"]

products=[x["symbol"] for x in first["trade_opinions"]]
full_returns={s:0.01*(i+1) for i,s in enumerate(products)}
full_turnover={s:100_000_000.0 for s in products}
out1=ledger.record_outcome(
    "CN","2026-09-22","2026-09-21",full_returns,"CN:SNAP:2",full_turnover
)
assert out1["recorded"] is True
partial={s:0.02 for s in products[:-1]}
out2=ledger.record_outcome("CN","2026-09-23","2026-09-22",partial,"CN:SNAP:3")
assert out2["recorded"] is True
review=ledger.review_decision(first)
assert review["observation_days"]==1
assert "2026-09-23" in review["incomplete_outcome_dates"]
assert len(review["daily_path"])==1
assert review["capital_sleeves"] is not None
assert len(review["capital_sleeves"]["sleeves"])==4
assert review["capital_sleeves"]["sleeves"][0]["observation_days"]==1

second_decision=core.decide(panel,"risk_off",first,"POSTCLOSE")
second=ledger.freeze(second_decision,"CN:SNAP:2","2026-09-22")
assert second["previous_decision_id"]==first["decision_id"]
assert second["previous_decision_hash"]==first["decision_hash"]
integrity=ledger.verify_integrity("CN")
assert integrity["passed"] is True
assert integrity["decision_count"]==3
assert ledger.by_snapshot("CN","CN:SNAP:1")["decision_hash"]==first["decision_hash"]

duplicate_outcome=ledger.record_outcome("CN","2026-09-22","2026-09-21",{s:0.99 for s in products},"X")
assert duplicate_outcome["recorded"] is False
assert ledger.review_decision(first)["daily_path"][0]["product_returns"]==full_returns

report=ledger.daily_report("CN")
assert report is not None
assert report["integrity"]["passed"] is True
assert report["latest_decision"]["decision_id"]==second["decision_id"]
assert report["previous_decision_review"]["decision_id"]==first["decision_id"]

print("TRIAID_RECOVERY_WAVE_SMOKE_PASS")
print({
    "core":core.version,
    "ledger":ledger.version,
    "decision_id":first["decision_id"],
    "targets":{x["symbol"]:x["target_weight"] for x in first["trade_opinions"]},
})
