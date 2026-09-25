from __future__ import annotations

from triaid_fin.contracts import BilingualText, MarketSnapshot, StrategyGroup, StrategyState
from triaid_fin.value_frontier_research import ValueFrontierResearchCore


def state(sid,ret,**kw):
    return StrategyState(
        strategy_id=sid,
        lifecycle=kw.pop("lifecycle","active"),
        expected_net_return=ret,
        estimated_cost=kw.pop("estimated_cost",0.0),
        eligible=kw.pop("eligible",True),
        hard_failure=kw.pop("hard_failure",False),
        liquidity_ok=kw.pop("liquidity_ok",True),
        capacity_ok=kw.pop("capacity_ok",True),
        risk_ok=kw.pop("risk_ok",True),
        concentration_ok=kw.pop("concentration_ok",True),
        **kw,
    )

market=MarketSnapshot(
    market_id="US",
    as_of="2026-09-25",
    snapshot_id="VF-RESEARCH-SMOKE",
    regime="risk_on",
    metadata={"account_risk_budget":0.60},
)
group=StrategyGroup(
    group_version="vf-smoke",
    config_version="vf-smoke",
    market_id="US",
    members=["A","B","C","P28_CASH"],
    weights={"A":0.4,"B":0.3,"C":0.3},
    reasons={sid:BilingualText(zh=sid,en=sid) for sid in ["A","B","C"]},
    diagnostics={"max_strategy_weight_constraint":0.28},
)
states=[
    state("A",0.20),
    state("B",0.15),
    state("C",0.10),
    state("P28_CASH",0.0),
]
preview=ValueFrontierResearchCore.evaluate(market,group,states)
assert preview["mode"]=="RESEARCH_ONLY"
assert preview["production_mutation_allowed"] is False
assert preview["allocator_version"]=="value-frontier-allocator@0.1.0"
assert abs(preview["weights"]["A"]-0.28)<1e-12
assert abs(preview["weights"]["B"]-0.28)<1e-12
assert abs(preview["weights"]["C"]-0.04)<1e-12
assert abs(preview["weights"]["P28_CASH"]-0.40)<1e-12
assert preview["uses_frozen_t0_information_only"] is True
assert preview["reads_realized_t1_to_choose_weights"] is False
print("TRIAID_VALUE_FRONTIER_RESEARCH_SMOKE_PASS",preview)
