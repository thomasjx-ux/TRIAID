from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from triaid_fin.contracts import StrategyState
from triaid_fin.full_core_shadow import (
    VERSION,
    independent_lifecycle,
    run_full_core_shadow,
)


def state(strategy_id,score,lifecycle="frozen",eligible=True,**kwargs):
    return StrategyState(
        strategy_id=strategy_id,
        eligible=eligible,
        lifecycle=lifecycle,
        expected_net_return=score,
        risk=kwargs.pop("risk",0.10),
        liquidity_ok=kwargs.pop("liquidity_ok",True),
        capacity_ok=kwargs.pop("capacity_ok",True),
        risk_ok=kwargs.pop("risk_ok",True),
        concentration_ok=kwargs.pop("concentration_ok",True),
        hard_failure=kwargs.pop("hard_failure",False),
        recent_returns=kwargs.pop("recent_returns",[0.001,-0.001]*32),
        **kwargs,
    )


states=[
    state("P00_BUY_HOLD",-.08),
    state("P16_REV5",.12),
    state("C29_SIZE_REL20",.50,lifecycle="shadow"),
    state("P28_CASH",0.0,risk=0.0),
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
assert payload["version"]==VERSION=="full-core-shadow@0.3.0"
assert payload["independent_lifecycle"] is True
assert payload["ignores_production_lifecycle_labels"] is True
assert payload["uses_full_frozen_t0_state_pool"] is True
assert payload["reads_t1_for_allocation"] is False
assert payload["production_mutation"] is False
assert payload["shortlist_is_allocation_gate"] is False
assert payload["allocation_member_count"]==3
assert set(payload["allocation_member_ids"])=={"P00_BUY_HOLD","P16_REV5","P28_CASH"}
assert "C29_SIZE_REL20" not in payload["allocation_member_ids"]

# max_members=1 proves the population shortlist is explanatory, not an
# allocation boundary. P16 can receive weight regardless of shortlist count.
assert len([x for x in payload["candidate_group_members"] if x!="P28_CASH"])<=1
assert payload["shadow_weights"].get("P16_REV5",0)>0
assert payload["shadow_weights"].get("P00_BUY_HOLD",0)>0
assert abs(payload["allocation_risk_budget"]-.28)<1e-12
assert payload["risk_budget_basis"]=="RELATIVE_ONLY_PRESERVE_PREDECISION_RISKY_EXPOSURE"
assert abs(payload["production_weights"]["P28_CASH"]-.72)<1e-12
assert "P28_CASH" not in payload["production_weights_raw"]
assert abs(sum(v for k,v in payload["shadow_weights"].items() if k!="P28_CASH")-.28)<1e-12
assert abs(payload["shadow_weights"].get("P28_CASH",0)-.72)<1e-12

allocation=payload["allocation"]
assert allocation["allocation_method"]=="SPARSE_FRONTIER_CONTINUOUS_TRANSITION"
assert allocation["rationale"]=="SPARSE_FRONTIER_CONTINUOUS_STATE_TRANSITION"
assert 0<allocation["transition_strength"]<1
assert allocation["frontier_target_position_count"]>=1
assert allocation["production_mutation"] is False
assert allocation["reads_t1_for_allocation"] is False

print("TRIAID_FULL_CORE_SHADOW_V3_SMOKE_PASS")

# Production-core weights are diagnostics only. Changing the old production
# decision must not change the independent candidate when T0 states and the
# actual pre-decision portfolio are unchanged.
alt=run_full_core_shadow(
    "CN",
    states,
    production_weights={"P16_REV5":0.28},
    previous_weights={"P00_BUY_HOLD":0.28},
    max_members=1,
    risk_budget=1.0,
    max_strategy_weight=.28,
    modeled_cost_bps=2.5,
    absolute_return_calibrated=False,
).to_dict()
for sid in set(payload["shadow_weights"])|set(alt["shadow_weights"]):
    assert abs(payload["shadow_weights"].get(sid,0)-alt["shadow_weights"].get(sid,0))<1e-12

# A changed real pre-decision state must change the transition path even under
# the same current T0 score evidence.
moved=run_full_core_shadow(
    "CN",
    states,
    production_weights={"P00_BUY_HOLD":0.28},
    previous_weights={"P16_REV5":0.28},
    max_members=1,
    risk_budget=1.0,
    max_strategy_weight=.28,
    modeled_cost_bps=2.5,
    absolute_return_calibrated=False,
).to_dict()
assert moved["shadow_weights"]!=payload["shadow_weights"]

# Hard-inadmissible production legacy cannot survive independent lifecycle.
hard_states=[
    state("P00_BUY_HOLD",.20,liquidity_ok=False),
    state("P16_REV5",.10),
    state("P28_CASH",0.0,risk=0.0),
]
hard=run_full_core_shadow(
    "US",hard_states,
    production_weights={"P00_BUY_HOLD":.28},
    previous_weights={"P00_BUY_HOLD":.28},
    max_members=10,risk_budget=1.0,max_strategy_weight=.28,
    modeled_cost_bps=2.0,absolute_return_calibrated=False,
).to_dict()
assert "P00_BUY_HOLD" not in hard["allocation_member_ids"]
assert hard["shadow_weights"].get("P00_BUY_HOLD",0)==0
assert hard["shadow_weights"].get("P28_CASH",0)>.72
