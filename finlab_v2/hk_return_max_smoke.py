from __future__ import annotations

from copy import deepcopy

from triaid_fin.contracts import BilingualText, StrategyGroup, StrategyState, TriaidDecision
from triaid_fin.hk_return_max import HKD_CAPITAL_SLEEVES, HKReturnMaxLedger, HKReturnMaxRoute
from triaid_fin.market_lab import MarketPanel, MarketSpec


class MemoryStore:
    def __init__(self):
        self.json={}
        self.lines={}
    def load_json(self,name,default=None):
        return deepcopy(self.json.get(name,{} if default is None else default))
    def save_json(self,name,payload):
        self.json[name]=deepcopy(payload)
    def append_jsonl(self,name,payload):
        self.lines.setdefault(name,[]).append(deepcopy(payload))
    def read_jsonl(self,name,limit=None):
        rows=deepcopy(self.lines.get(name,[]))
        return rows[-limit:] if limit else rows


spec=MarketSpec(
    "HK","2800.HK",
    ("2800.HK","2828.HK","3033.HK","2819.HK"),
    ("2800.HK","2828.HK","3033.HK"),
    ("2819.HK",),
    "HKD",50_000_000.0,2.0,55.0,0.02,
)
n=300
ts=list(range(1_700_000_000,1_700_000_000+n*86400,86400))
close={s:[100.0+i for _ in range(n)] for i,s in enumerate(spec.assets)}
volume={s:[200_000.0 for _ in range(n)] for s in spec.assets}
for s in spec.assets:
    volume[s][-1]=200_000_000.0
panel=MarketPanel(spec=spec,ts=ts,close=close,volume=volume,data_mode="DAILY",provider="smoke",quality="real")

states=[
    StrategyState(strategy_id="P00_BUY_HOLD",lifecycle="active",expected_net_return=0.10,risk=0.15,uncertainty=0.01,oos_marginal_value=0.10),
    StrategyState(strategy_id="P18_XMOM20",lifecycle="active",expected_net_return=0.22,risk=0.20,uncertainty=0.02,oos_marginal_value=0.22),
    StrategyState(strategy_id="P25_BALANCED",lifecycle="active",expected_net_return=0.16,risk=0.10,uncertainty=0.01,oos_marginal_value=0.16),
    StrategyState(strategy_id="P28_CASH",lifecycle="active",expected_net_return=0.0,risk=0.0,uncertainty=0.0,oos_marginal_value=0.0),
]
group=StrategyGroup(
    group_version="g",config_version="hk",market_id="HK",
    members=["P18_XMOM20","P25_BALANCED"],
    weights={"P18_XMOM20":0.60,"P25_BALANCED":0.40},
    reasons={
        "P18_XMOM20":BilingualText(zh="收益优先",en="return first"),
        "P25_BALANCED":BilingualText(zh="收益优先",en="return first"),
    },
)
decision=TriaidDecision(
    core_version="core",
    weights_before=dict(group.weights),
    weights_after={"P18_XMOM20":0.70,"P25_BALANCED":0.20,"P28_CASH":0.10},
    reasons={},
)
route=HKReturnMaxRoute()
row=route.decide(panel,group,decision,states,"OPEN")
assert row["route_version"]=="hk-return-max-route@0.2.0"
assert row["market_id"]=="HK"
assert row["decision_status"]=="PROVISIONAL_INTRADAY"
assert row["objective"]=="MAXIMIZE_REALIZABLE_NET_RETURN"
assert row["target_strategy_weights"]==decision.weights_after
assert row["baseline_strategy_weights"]==group.weights
assert row["capital_capacity"]["currency"]=="HKD"
assert row["capital_capacity"]["capital_sleeves_hkd"]==[int(x) for x in HKD_CAPITAL_SLEEVES]
assert len(row["capital_capacity"]["sleeves"])==4
assert set(row["target_asset_weights"]).issubset(set(spec.assets))
assert row["broker_execution_enabled"] is False

small=row["capital_capacity"]["sleeves"][0]
large=row["capital_capacity"]["sleeves"][-1]
assert small["starting_capital_hkd"]==100000.0
assert large["starting_capital_hkd"]==100000000.0
assert small["max_one_day_participation_adv"]<=large["max_one_day_participation_adv"]
assert small["estimated_entry_cost_hkd"]<=large["estimated_entry_cost_hkd"]

store=MemoryStore()
ledger=HKReturnMaxLedger(store)
frozen=ledger.freeze(row,"HK:SNAP:1","2026-09-22")
assert ledger.freeze(row,"HK:SNAP:1","2026-09-22")["decision_id"]==frozen["decision_id"]
strategy_returns={"P18_XMOM20":0.03,"P25_BALANCED":0.01,"P00_BUY_HOLD":0.015}
products={a:0.02 for a in spec.assets}
turnover={a:20_000_000.0 for a in spec.assets}
assert ledger.record_outcome("2026-09-23","2026-09-22",strategy_returns,products,turnover,"HK:SNAP:2")["recorded"] is True
review=ledger.review_decision(frozen)
assert review["observation_days"]==1
assert review["current_triaid_theoretical_return"]>review["current_baseline_theoretical_return"]
assert abs(review["current_benchmark_buy_hold_return"]-0.02)<1e-12
assert len(review["capital_sleeves"]["sleeves"])==4
assert ledger.verify_integrity()["passed"] is True
report=ledger.daily_report()
assert report["integrity"]["passed"] is True
assert report["route_version"]=="hk-return-max-route@0.2.0"
print("TRIAID_HK_RETURN_MAX_SMOKE_PASS")
