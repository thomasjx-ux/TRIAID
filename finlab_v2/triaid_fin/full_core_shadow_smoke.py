from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

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
    max_members=1,
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
assert payload["shortlist_is_allocation_gate"] is False
assert payload["allocation_member_count"]==3
assert set(payload["allocation_member_ids"])=={"P00_BUY_HOLD","P16_REV5","P28_CASH"}
assert "C29_SIZE_REL20" not in payload["allocation_member_ids"]
# max_members=1 proves the diagnostic shortlist is not the allocation gate.
assert len([x for x in payload["candidate_group_members"] if x!="P28_CASH"])<=1
assert payload["shadow_weights"].get("P16_REV5",0)>0
assert payload["shadow_weights"] != payload["production_weights"]
assert abs(payload["allocation_risk_budget"]-0.28)<1e-12
assert payload["risk_budget_basis"]=="RELATIVE_ONLY_PRESERVE_INCUMBENT_RISKY_EXPOSURE"
assert abs(payload["production_weights"]["P28_CASH"]-0.72)<1e-12
assert "P28_CASH" not in payload["production_weights_raw"]
assert abs(sum(v for k,v in payload["shadow_weights"].items() if k!="P28_CASH")-0.28)<1e-12
assert payload["allocation"]["production_mutation"] is False
assert payload["allocation"]["reads_t1_for_allocation"] is False
assert payload["allocation"]["allocation_method"]=="CAPPED_ADAPTIVE_SOFTMAX"
print("TRIAID_FULL_CORE_SHADOW_V2_SMOKE_PASS")

# Implicit cash must not be charged as turnover simply because it is made
# explicit. Allocation can continuously split the matched risky sleeve.
rotation_states=[
    StrategyState(strategy_id="P00_BUY_HOLD",eligible=True,lifecycle="frozen",
                  expected_net_return=.05,risk=.1,liquidity_ok=True,capacity_ok=True,
                  risk_ok=True,concentration_ok=True,hard_failure=False,recent_returns=[.001]*30),
    StrategyState(strategy_id="P16_REV5",eligible=True,lifecycle="frozen",
                  expected_net_return=.20,risk=.1,liquidity_ok=True,capacity_ok=True,
                  risk_ok=True,concentration_ok=True,hard_failure=False,recent_returns=[.001,-.001]*15),
    StrategyState(strategy_id="P28_CASH",eligible=True,lifecycle="frozen",
                  expected_net_return=0.0,liquidity_ok=True,capacity_ok=True,
                  risk_ok=True,concentration_ok=True,hard_failure=False),
]
rot=run_full_core_shadow(
    "HK",rotation_states,
    production_weights={"P00_BUY_HOLD":.28},
    previous_weights={"P00_BUY_HOLD":.28},
    max_members=1,risk_budget=1.0,max_strategy_weight=.28,
    modeled_cost_bps=2.0,absolute_return_calibrated=False,
).to_dict()
risky=sum(v for k,v in rot["shadow_weights"].items() if k!="P28_CASH")
assert abs(risky-.28)<1e-12, rot
assert rot["shadow_weights"].get("P16_REV5",0)>rot["shadow_weights"].get("P00_BUY_HOLD",0), rot
assert abs(rot["shadow_weights"].get("P28_CASH",0)-.72)<1e-12, rot
assert rot["allocation"]["modeled_turnover"]>0, rot
assert rot["allocation"]["modeled_turnover"]<.56+1e-12, rot
