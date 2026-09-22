from __future__ import annotations

from copy import deepcopy

from triaid_fin.contracts import BilingualText, StrategyGroup, StrategyState, TriaidDecision
from triaid_fin.market_lab import MarketPanel, MarketSpec
from triaid_fin.us_return_max import USReturnMaxLedger, USReturnMaxRoute


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
    "US","SPY",
    ("SPY","QQQ","IWM","TLT","GLD"),
    ("SPY","QQQ","IWM"),
    ("TLT","GLD"),
    "USD",10_000_000.0,1.5,45.0,0.03,
)

n=300
ts=list(range(1_700_000_000,1_700_000_000+n*86400,86400))
close={s:[100.0+i for _ in range(n)] for i,s in enumerate(spec.assets)}
volume={s:[100_000.0 for _ in range(n)] for s in spec.assets}
# Huge current intraday volume must not contaminate completed-bar ADV.
for s in spec.assets:
    volume[s][-1]=100_000_000.0

panel=MarketPanel(
    spec=spec,
    ts=ts,
    close=close,
    volume=volume,
    data_mode="DAILY",
    provider="smoke",
    quality="real",
)

states=[
    StrategyState(
        strategy_id="P00_BUY_HOLD",
        lifecycle="active",
        expected_net_return=0.10,
        risk=0.15,
        uncertainty=0.01,
        oos_marginal_value=0.10,
    ),
    StrategyState(
        strategy_id="P18_XMOM20",
        lifecycle="active",
        expected_net_return=0.30,
        risk=0.22,
        uncertainty=0.02,
        oos_marginal_value=0.30,
    ),
    StrategyState(
        strategy_id="P25_BALANCED",
        lifecycle="active",
        expected_net_return=0.16,
        risk=0.10,
        uncertainty=0.01,
        oos_marginal_value=0.16,
    ),
    StrategyState(
        strategy_id="P04_TREND50",
        lifecycle="active",
        expected_net_return=0.12,
        risk=0.12,
        uncertainty=0.01,
        oos_marginal_value=0.12,
    ),
    StrategyState(
        strategy_id="P28_CASH",
        lifecycle="active",
        expected_net_return=0.0,
        risk=0.0,
        uncertainty=0.0,
        oos_marginal_value=0.0,
    ),
]
reasons={
    "P18_XMOM20":BilingualText(zh="收益优先",en="return first"),
    "P25_BALANCED":BilingualText(zh="收益优先",en="return first"),
}
group=StrategyGroup(
    group_version="strategy-population@smoke",
    config_version="strategy-rules-us@smoke",
    market_id="US",
    members=["P18_XMOM20","P25_BALANCED"],
    weights={"P18_XMOM20":0.72,"P25_BALANCED":0.28},
    reasons=reasons,
    diagnostics={"selection_mode":"RETURN_FIRST_RESELECT","max_strategy_weight_constraint":0.28},
)
generic=TriaidDecision(
    core_version="triaid-core-smoke",
    weights_before=dict(group.weights),
    weights_after={"P18_XMOM20":0.40,"P25_BALANCED":0.30,"P28_CASH":0.30},
    reasons={},
)

route=USReturnMaxRoute()
tie_winner,tie_set=route._strict_max_strategy([
    StrategyState(strategy_id="P09_SHOCK_GUARD",lifecycle="active",expected_net_return=0.40,risk=0.20,uncertainty=0.02,oos_marginal_value=0.40),
    StrategyState(strategy_id="P00_BUY_HOLD",lifecycle="active",expected_net_return=0.40,risk=0.20,uncertainty=0.02,oos_marginal_value=0.40),
])
assert tie_set==["P00_BUY_HOLD","P09_SHOCK_GUARD"]
assert tie_winner.strategy_id=="P00_BUY_HOLD"

decision=route.decide(panel,group,generic,states,"OPEN")
assert decision["route_version"]=="us-return-max-route@0.5.0"
assert decision["decision_status"]=="PROVISIONAL_INTRADAY"
assert decision["objective"]=="MAXIMIZE_REALIZABLE_NET_RETURN"
assert decision["risk_used_as_secondary_objective"] is False
assert decision["uncertainty_used_as_secondary_objective"] is False
assert decision["selection_source"]=="ALL_ADMISSIBLE_ACTIVE_STRATEGIES_NET_OF_META_SWITCH_COST"
assert decision["strategy_selection_mode"]=="MAX_REALIZABLE_NET_RETURN_UNDER_HARD_CONCENTRATION_AND_EXECUTION_CONSTRAINTS"
assert decision["fixed_strategy_count_target"] is False
assert decision["selected_strategy_count"]==4
assert decision["max_strategy_weight_constraint"]==0.28
assert decision["selected_strategy_id"]=="P18_XMOM20"
assert decision["fast_challenger"]["shadow_only"] is True
assert decision["fast_challenger"]["applied_to_weights"] is False
assert decision["fast_challenger"]["windows_days"]==[1,3,5]
assert "pilot_execution_check" in decision["fast_challenger"]
assert decision["fast_challenger"]["pilot_execution_check"]["pilot_max_risk_budget"]==0.10
assert decision["max_return_tie_set"]==["P18_XMOM20"]
expected_route_weights={"P18_XMOM20":0.28,"P25_BALANCED":0.28,"P04_TREND50":0.28,"P00_BUY_HOLD":0.16}
assert set(decision["target_strategy_weights"])==set(expected_route_weights)
assert all(
    abs(float(decision["target_strategy_weights"][sid])-weight)<1e-12
    for sid,weight in expected_route_weights.items()
)
assert decision["return_first_population_control_weights"]==group.weights
assert abs(decision["return_first_population_projected_annualized_expected_net_return"]-0.2608)<1e-12
assert decision["generic_core_control_weights"]==generic.weights_after
assert decision["projected_annualized_expected_net_return"] > decision["generic_core_projected_annualized_expected_net_return"]
assert set(decision["target_asset_weights"]).issubset(set(spec.assets))
assert decision["capital_capacity"]["capital_sleeves_usd"]==[100000,1000000,10000000,100000000]
assert decision["capital_capacity"]["base_cost_bps"]==1.5
assert decision["capital_capacity"]["impact_coefficient_bps"]==45.0
assert decision["capital_capacity"]["max_participation_adv"]==0.03

# Completed ADV must exclude current intraday 100m-volume bar.
assert decision["capital_capacity"]["sleeves"][0]["products"]
spy_rows=[
    p for p in decision["capital_capacity"]["sleeves"][0]["products"]
    if p["symbol"]=="SPY"
]
if spy_rows:
    assert spy_rows[0]["adv20_notional_usd"] < 20_000_000.0

sleeves=decision["capital_capacity"]["sleeves"]
small=sleeves[0]
large=sleeves[-1]
assert small["starting_capital_usd"]==100000.0
assert large["starting_capital_usd"]==100000000.0
assert small["max_one_day_participation_adv"] < large["max_one_day_participation_adv"]
assert small["estimated_entry_cost_usd"] < large["estimated_entry_cost_usd"]
assert small["minimum_execution_days"] <= large["minimum_execution_days"]
assert large["minimum_execution_days"] > 1

store=MemoryStore()
ledger=USReturnMaxLedger(store)
frozen=ledger.freeze(decision,"US:SNAP:1","2026-09-18")
dup=ledger.freeze(decision,"US:SNAP:1","2026-09-18")
assert dup["decision_id"]==frozen["decision_id"]

strategy_day1={"P18_XMOM20":0.10,"P25_BALANCED":0.05,"P00_BUY_HOLD":0.08}
strategy_day2={"P18_XMOM20":0.02,"P25_BALANCED":0.01,"P00_BUY_HOLD":0.015}
product_day1={a:0.10 for a in spec.assets}
product_day2={a:0.02 for a in spec.assets}
turnover={a:10_000_000.0 for a in spec.assets}

o1=ledger.record_outcome(
    "2026-09-21","2026-09-18",
    strategy_day1,product_day1,turnover,"US:SNAP:2",
)
assert o1["recorded"] is True
o2=ledger.record_outcome(
    "2026-09-22","2026-09-21",
    strategy_day2,product_day2,turnover,"US:SNAP:3",
)
assert o2["recorded"] is True

review=ledger.review_decision(frozen)
assert review["observation_days"]==2
expected_route_day1=strategy_day1["P18_XMOM20"]
expected_route_day2=strategy_day2["P18_XMOM20"]
expected_route=(1.0+expected_route_day1)*(1.0+expected_route_day2)-1.0
expected_generic_day1=0.40*strategy_day1["P18_XMOM20"]+0.30*strategy_day1["P25_BALANCED"]
expected_generic_day2=0.40*strategy_day2["P18_XMOM20"]+0.30*strategy_day2["P25_BALANCED"]
expected_generic=(1.0+expected_generic_day1)*(1.0+expected_generic_day2)-1.0
expected_spy=(1.0+product_day1["SPY"])*(1.0+product_day2["SPY"])-1.0
assert abs(review["current_return_max_theoretical_return"]-expected_route)<1e-12
assert abs(review["current_generic_core_theoretical_return"]-expected_generic)<1e-12
assert abs(review["current_spy_buy_hold_return"]-expected_spy)<1e-12
assert review["capital_sleeves"]["review_discipline"].startswith("FILLS_USE_ONLY_FUTURE_OBSERVED_TURNOVER")
real={x["sleeve_id"]:x for x in review["capital_sleeves"]["sleeves"]}
small_real=real["USD_100000"]
large_real=real["USD_100000000"]

# No look-ahead: day-one fresh fills only pay execution cost and cannot earn the already-seen +10%.
assert small_real["daily_path"][0]["daily_net_return"] < 0
assert small_real["daily_path"][1]["daily_net_return"] > 0
assert small_real["fill_ratio"] >= large_real["fill_ratio"]
assert large_real["fill_ratio"] < 1.0
assert large_real["remaining_target_notional_usd"] > 0
assert small_real["total_execution_cost_usd"] < large_real["total_execution_cost_usd"]

integrity=ledger.verify_integrity()
assert integrity["passed"] is True
report=ledger.daily_report()
assert report["route_version"]=="us-return-max-route@0.5.0"
assert report["integrity"]["passed"] is True

print("TRIAID_US_RETURN_MAX_SMOKE_PASS")
print({
    "route":route.version,
    "ledger":ledger.version,
    "projected_return_max":decision["projected_annualized_expected_net_return"],
    "projected_generic":decision["generic_core_projected_annualized_expected_net_return"],
    "sleeves":[
        {
            "capital":x["starting_capital_usd"],
            "min_days":x["minimum_execution_days"],
            "entry_cost":x["estimated_entry_cost_usd"],
        }
        for x in sleeves
    ],
})
