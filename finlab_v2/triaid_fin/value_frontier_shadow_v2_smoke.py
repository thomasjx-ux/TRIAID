"""Offline research-only test suite. No data, network or deployment writes."""
from dataclasses import dataclass, field
from value_frontier_shadow_v2 import allocate_shadow


@dataclass(frozen=True)
class State:
    strategy_id: str
    expected_net_return: float
    estimated_cost: float = 0.0
    lifecycle: str = "active"
    eligible: bool = True
    hard_failure: bool = False
    liquidity_ok: bool = True
    capacity_ok: bool = True
    risk_ok: bool = True
    concentration_ok: bool = True
    recent_returns: list[float] = field(default_factory=lambda:[0.001]*63)


def near(a,b,tol=1e-10):
    assert abs(a-b)<tol,(a,b)


rows=[
    State("A",.20,.01),State("B",.15,.01),State("C",.10,.01),
    State("D",.05,.01),State("E",.00,.01),State("F",-.05,.01),
    State("BAD",.90,liquidity_ok=False),
    State("NEW",.80,lifecycle="shadow"),
    State("P28_CASH",0),
]
members=[x.strategy_id for x in rows]
previous={"A":.10,"B":.10,"C":.10,"D":.10,"E":.10,"F":.10,"P28_CASH":.40}

r=allocate_shadow(
    "US",rows,members,risk_budget=.60,
    previous_weights=previous,position_cap=.28,modeled_cost_bps=2,
)
assert r.mode=="SHADOW_ONLY" and not r.production_mutation and r.frozen_t0_only
assert r.allocation_method=="SPARSE_FRONTIER_CONTINUOUS_TRANSITION"
assert r.ranked_strategy_ids[:3]==["A","B","C"]
assert r.exclusions["BAD"]=="INELIGIBLE_OR_HARD_CONSTRAINT"
assert r.exclusions["NEW"]=="INELIGIBLE_OR_HARD_CONSTRAINT"
assert 0<r.transition_strength<1
assert r.frontier_target_position_count<len([x for x in rows if x.strategy_id in "ABCDEF"])
assert all(w<=.28+1e-12 for sid,w in r.weights.items() if sid!="P28_CASH")
near(sum(r.weights.values()),1)
near(sum(w for sid,w in r.weights.items() if sid!="P28_CASH"),.60)
near(r.weights["P28_CASH"],.40)
assert r.weights["A"]>r.weights["F"]
assert r.modeled_turnover>0

# The frontier target may be sparse, while Transition moves only part-way from
# the true T0 portfolio. This is intentional state continuity, not a top-K gate.
assert r.frontier_target_position_count>=2
assert r.effective_positions>r.frontier_target_position_count

# Higher modeled execution cost continuously weakens the transition.
high_cost=allocate_shadow(
    "US",rows,members,risk_budget=.60,
    previous_weights=previous,position_cap=.28,modeled_cost_bps=500,
)
assert high_cost.cost_factor<r.cost_factor
assert high_cost.transition_strength<r.transition_strength

# Sparse/no history weakens transition confidence without changing hard rules.
sparse_history=[
    State(x.strategy_id,x.expected_net_return,x.estimated_cost,x.lifecycle,
          x.eligible,x.hard_failure,x.liquidity_ok,x.capacity_ok,x.risk_ok,
          x.concentration_ok,[])
    for x in rows
]
uncertain=allocate_shadow(
    "US",sparse_history,members,risk_budget=.60,
    previous_weights=previous,position_cap=.28,modeled_cost_bps=2,
)
assert uncertain.state_confidence==0
assert uncertain.transition_strength==0
for sid,w in previous.items():
    near(uncertain.weights.get(sid,0),w)

# Flat evidence means no transition; the current state is preserved.
ties=[State("A",.1),State("B",.1),State("C",.1),State("P28_CASH",0)]
tie_prev={"A":.20,"B":.10,"C":.10,"P28_CASH":.60}
tie=allocate_shadow("US",ties,[x.strategy_id for x in ties],
                    risk_budget=.40,previous_weights=tie_prev)
assert tie.dominance==0 and tie.transition_strength==0
for sid,w in tie_prev.items():
    near(tie.weights.get(sid,0),w)

# A small rank swap produces a bounded continuous portfolio response.
base=[
    State("A",.20),State("B",.1905),State("C",.1895),
    State("D",.17),State("E",.16),State("F",.15),State("P28_CASH",0),
]
pert=[
    State("A",.20),State("B",.1895),State("C",.1905),
    State("D",.17),State("E",.16),State("F",.15),State("P28_CASH",0),
]
ids=[x.strategy_id for x in base]
p0={"A":.10,"B":.10,"C":.10,"D":.10,"E":.10,"F":.10,"P28_CASH":.40}
b=allocate_shadow("US",base,ids,risk_budget=.60,previous_weights=p0)
p=allocate_shadow("US",pert,ids,risk_budget=.60,previous_weights=p0)
assert b.ranked_strategy_ids[:3]==["A","B","C"]
assert p.ranked_strategy_ids[:3]==["A","C","B"]
assert abs(p.weights["B"]-b.weights["B"])<.03
assert abs(p.weights["C"]-b.weights["C"])<.03

# Relative-only negative scores can still rank risky policies; calibrated
# absolute-return mode may gate them to cash.
negative=[State("X",-.05),State("Y",-.10),State("P28_CASH",0)]
negative_ids=[x.strategy_id for x in negative]
relative=allocate_shadow("HK",negative,negative_ids,risk_budget=.50,
                         previous_weights=None)
assert relative.absolute_cash_gate_applied is False
assert sum(w for k,w in relative.weights.items() if k!="P28_CASH")>0
calibrated=allocate_shadow("HK",negative,negative_ids,risk_budget=.50,
                           previous_weights=None,absolute_return_calibrated=True)
assert calibrated.weights=={"P28_CASH":1.0}
assert calibrated.exclusions["X"]=="NOT_ABOVE_CASH_AFTER_COST"
assert calibrated.exclusions["Y"]=="NOT_ABOVE_CASH_AFTER_COST"

# frozen_incumbent never acts as a binary hold gate.
incumbent={"A":.28,"B":.28,"C":.04,"P28_CASH":.40}
free=allocate_shadow(
    "US",rows,members,risk_budget=.60,
    previous_weights=previous,frozen_incumbent=incumbent,
    position_cap=.28,modeled_cost_bps=2,
)
assert free.weights!=incumbent
assert free.rationale=="SPARSE_FRONTIER_CONTINUOUS_STATE_TRANSITION"

# Fail closed on invalid inputs and duplicate states.
for params in ({"risk_budget":float("nan")},{"position_cap":2.0},
               {"modeled_cost_bps":-1.0}):
    try:
        allocate_shadow("US",ties,ids[:3],**params)
    except ValueError:
        pass
    else:
        raise AssertionError(f"invalid parameter accepted: {params}")
try:
    allocate_shadow("US",ties+[ties[0]],["A","B"])
except ValueError:
    pass
else:
    raise AssertionError("duplicate strategy state accepted")

print("TRIAID_FRONTIER_SHADOW_V5_SMOKE_PASS",{
    "method":r.allocation_method,
    "transition_strength":r.transition_strength,
    "frontier_target_positions":r.frontier_target_position_count,
    "effective_positions":r.effective_positions,
    "turnover":r.modeled_turnover,
    "production_mutation":False,
})
