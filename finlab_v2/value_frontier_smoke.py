from __future__ import annotations

from triaid_fin.contracts import StrategyState
from triaid_fin.value_frontier import ValueFrontierAllocator


def state(sid,ret,cost=0.0,**kw):
    return StrategyState(
        strategy_id=sid,
        eligible=kw.pop("eligible",True),
        lifecycle=kw.pop("lifecycle","active"),
        expected_net_return=ret,
        estimated_cost=cost,
        risk=kw.pop("risk",0.1),
        oos_marginal_value=kw.pop("oos_marginal_value",0.0),
        recent_returns=kw.pop("recent_returns",[0.0]*20),
        **kw,
    )

states=[
    state("A",0.20,0.01),
    state("B",0.15,0.01),
    state("C",0.10,0.01),
    state("D",0.05,0.01),
    state("E",0.40,0.01,liquidity_ok=False),
    state("P28_CASH",0.0,0.0),
]
result=ValueFrontierAllocator.allocate(
    states,
    risk_budget=1.0,
    position_cap=0.28,
)
assert result.ranked_strategy_ids[:4]==["A","B","C","D"]
assert result.excluded_strategy_ids==["E"]
assert abs(result.weights["A"]-0.28)<1e-12
assert abs(result.weights["B"]-0.28)<1e-12
assert abs(result.weights["C"]-0.28)<1e-12
assert abs(result.weights["D"]-0.16)<1e-12
assert abs(sum(result.weights.values())-1.0)<1e-12

# A lower risk budget is a hard constraint, not a score penalty.
limited=ValueFrontierAllocator.allocate(
    states,
    risk_budget=0.60,
    position_cap=0.28,
)
assert abs(limited.weights["A"]-0.28)<1e-12
assert abs(limited.weights["B"]-0.28)<1e-12
assert abs(limited.weights["C"]-0.04)<1e-12
assert abs(limited.weights["P28_CASH"]-0.40)<1e-12

# Shadow strategies remain excluded unless a shadow-only caller opts in.
shadow_states=states+[state("S",0.50,0.0,lifecycle="shadow")]
prod=ValueFrontierAllocator.allocate(shadow_states,risk_budget=1.0,position_cap=0.28)
shadow=ValueFrontierAllocator.allocate(shadow_states,risk_budget=1.0,position_cap=0.28,allow_shadow=True)
assert "S" not in prod.weights
assert abs(shadow.weights["S"]-0.28)<1e-12

print("TRIAID_VALUE_FRONTIER_SMOKE_PASS",{
    "version":ValueFrontierAllocator.version,
    "full_weights":result.weights,
    "risk_limited_weights":limited.weights,
    "shadow_opt_in_weight":shadow.weights["S"],
})
