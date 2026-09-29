from triaid_fin.contracts import StrategyState
from triaid_fin.full_core_shadow import (
    VERSION,
    independent_lifecycle,
    run_full_core_shadow,
)

states=[
    StrategyState(
        strategy_id="P00_BUY_HOLD",
        eligible=True,
        lifecycle="frozen",
        expected_net_return=-0.08,
        risk=0.10,
        liquidity_ok=True,
        capacity_ok=True,
        risk_ok=True,
        concentration_ok=True,
        hard_failure=False,
        recent_returns=[0.001]*30,
    ),
    StrategyState(
        strategy_id="P16_REV5",
        eligible=True,
        lifecycle="frozen",
        expected_net_return=0.12,
        risk=0.08,
        liquidity_ok=True,
        capacity_ok=True,
        risk_ok=True,
        concentration_ok=True,
        hard_failure=False,
        recent_returns=[0.001,-0.001]*15,
    ),
    StrategyState(
        strategy_id="C29_SIZE_REL20",
        eligible=True,
        lifecycle="shadow",
        expected_net_return=0.50,
        risk=0.10,
        liquidity_ok=True,
        capacity_ok=True,
        risk_ok=True,
        concentration_ok=True,
        hard_failure=False,
        recent_returns=[0.002]*30,
    ),
    StrategyState(
        strategy_id="P28_CASH",
        eligible=True,
        lifecycle="frozen",
        expected_net_return=0.0,
        liquidity_ok=True,
        capacity_ok=True,
        risk_ok=True,
        concentration_ok=True,
        hard_failure=False,
    ),
]
rebuilt,reasons,changes=independent_lifecycle(states)
by_id={s.strategy_id:s for s in rebuilt}
assert by_id["P00_BUY_HOLD"].lifecycle=="active"
assert by_id["P16_REV5"].lifecycle=="active"
assert by_id["C29_SIZE_REL20"].lifecycle=="shadow"
assert by_id["P28_CASH"].lifecycle=="active"
assert changes["P16_REV5"]["production_lifecycle"]=="frozen"
assert reasons["C29_SIZE_REL20"]=="NON_INCUMBENT_REQUIRES_INDEPENDENT_PROSPECTIVE_EVIDENCE"

decision=run_full_core_shadow(
    "CN",
    states,
    production_weights={"P00_BUY_HOLD":0.28},
    previous_weights={"P00_BUY_HOLD":0.28},
    max_members=10,
    risk_budget=1.0,
    max_strategy_weight=0.28,
    modeled_cost_bps=2.5,
    absolute_return_calibrated=False,
)
payload=decision.to_dict()
assert payload["version"]==VERSION
assert payload["independent_lifecycle"] is True
assert payload["ignores_production_lifecycle_labels"] is True
assert payload["uses_full_frozen_t0_state_pool"] is True
assert payload["reads_t1_for_allocation"] is False
assert payload["production_mutation"] is False
assert "P16_REV5" in payload["candidate_group_members"]
assert "C29_SIZE_REL20" not in payload["candidate_group_members"]
assert payload["shadow_weights"].get("P16_REV5",0)>0
assert payload["shadow_weights"] != payload["production_weights"]
assert payload["allocation"]["production_mutation"] is False
assert payload["allocation"]["reads_t1_for_allocation"] is False
print("TRIAID_FULL_CORE_SHADOW_SMOKE_PASS")
