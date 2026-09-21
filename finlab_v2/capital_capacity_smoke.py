from __future__ import annotations

from copy import deepcopy

from triaid_fin.capital_capacity import CapitalCapacityLayer
from triaid_fin.market_lab import MarketPanel, MarketSpec


spec=MarketSpec(
    "CN","510300.SS",
    ("510300.SS","510500.SS","159915.SZ","512100.SS","511010.SS"),
    ("510300.SS","510500.SS","159915.SZ","512100.SS"),
    ("511010.SS",),
    "CNY",50_000_000.0,2.5,60.0,0.02,
)

n=80
ts=list(range(1_700_000_000,1_700_000_000+n*86400,86400))
close={}
volume={}
for j,s in enumerate(spec.assets):
    close[s]=[100.0+j for _ in range(n)]
    volume[s]=[500_000.0 for _ in range(n)]
# Current intraday bar is deliberately huge; BREAK logic must exclude it from ADV.
for s in spec.assets:
    volume[s][-1]=50_000_000.0

panel=MarketPanel(spec=spec,ts=ts,close=close,volume=volume,data_mode="DAILY",provider="smoke",quality="real")
opinions=[
    {
        "symbol":"510300.SS",
        "target_weight":0.20,
        "expected_reversal_horizon_days":5,
        "expected_forward_return":0.03,
    },
    {
        "symbol":"510500.SS",
        "target_weight":0.10,
        "expected_reversal_horizon_days":10,
        "expected_forward_return":0.04,
    },
    {"symbol":"159915.SZ","target_weight":0.0,"expected_reversal_horizon_days":None,"expected_forward_return":None},
    {"symbol":"512100.SS","target_weight":0.0,"expected_reversal_horizon_days":None,"expected_forward_return":None},
]

layer=CapitalCapacityLayer()
cap=layer.build(panel,opinions,"BREAK")
assert cap["enabled"] is True
assert cap["capital_sleeves_cny"]==[100000,1000000,10000000,100000000]
assert cap["model"]["base_cost_bps"]==2.5
assert cap["model"]["impact_coefficient_bps"]==60.0
assert cap["model"]["max_participation_adv"]==0.02
assert cap["model"]["parameter_provenance"]=="EXISTING_TRIAID_MARKET_SPEC_REUSED_NOT_RETUNED_FOR_THIS_EXPERIMENT"

# ADV must use completed bars: 100 * 500k, not the huge current intraday volume.
assert abs(cap["adv20_notional_by_symbol_cny"]["510300.SS"]-50_000_000.0)<1e-6

sleeves=cap["sleeves"]
assert [int(x["starting_capital_cny"]) for x in sleeves]==[100000,1000000,10000000,100000000]
assert all(abs(x["target_risk_weight"]-0.30)<1e-9 for x in sleeves)

small=sleeves[0]
large=sleeves[-1]
assert small["max_one_day_participation_adv"] < large["max_one_day_participation_adv"]
assert small["estimated_round_trip_cost_proxy_cny"] < large["estimated_round_trip_cost_proxy_cny"]
assert small["expected_wave_net_return_before_timing_delay"] > large["expected_wave_net_return_before_timing_delay"]
assert small["minimum_execution_days"]==1
assert large["minimum_execution_days"]>1
assert large["capacity_status"]=="MULTI_DAY_EXECUTION_REQUIRED"

decision={
    "market_id":"CN",
    "market_as_of":"2026-09-21",
    "capital_capacity":deepcopy(cap),
}
# Day 1 has a very strong return, but fills occur after that return and may not capture it.
outcomes=[
    {
        "as_of":"2026-09-22",
        "product_returns":{"510300.SS":0.10,"510500.SS":0.10,"159915.SZ":0.0,"512100.SS":0.0},
        "product_turnover":{
            "510300.SS":50_000_000.0,
            "510500.SS":50_500_000.0,
            "159915.SZ":51_000_000.0,
            "512100.SS":51_500_000.0,
        },
    },
    {
        "as_of":"2026-09-23",
        "product_returns":{"510300.SS":0.02,"510500.SS":0.01,"159915.SZ":0.0,"512100.SS":0.0},
        "product_turnover":{
            "510300.SS":50_000_000.0,
            "510500.SS":50_500_000.0,
            "159915.SZ":51_000_000.0,
            "512100.SS":51_500_000.0,
        },
    },
]
review=CapitalCapacityLayer.realized_review(decision,outcomes)
assert review is not None
assert review["review_discipline"].startswith("FILLS_USE_ONLY_FUTURE_OBSERVED_TURNOVER")
rr={x["sleeve_id"]:x for x in review["sleeves"]}
small_real=rr["CNY_100000"]
large_real=rr["CNY_100000000"]

# No look-ahead: first day can only contain execution cost, not +10% return on freshly filled cash.
assert small_real["daily_path"][0]["daily_net_return"] < 0
assert small_real["daily_path"][1]["daily_net_return"] > 0
assert small_real["fill_ratio"]==1.0
assert large_real["fill_ratio"] < 1.0
assert large_real["remaining_target_notional_cny"] > 0
assert small_real["total_execution_cost_cny"] < large_real["total_execution_cost_cny"]

print("TRIAID_CAPITAL_CAPACITY_SMOKE_PASS")
print({
    "version":layer.version,
    "sleeves":[
        {
            "capital":x["starting_capital_cny"],
            "target_risk_weight":x["target_risk_weight"],
            "min_days":x["minimum_execution_days"],
            "expected_net_return":x["expected_wave_net_return_before_timing_delay"],
        }
        for x in sleeves
    ],
})
