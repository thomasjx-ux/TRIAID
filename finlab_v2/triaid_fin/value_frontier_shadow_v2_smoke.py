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
    recent_returns: list[float] = field(default_factory=list)


def near(a, b):
    assert abs(a-b)<1e-10, (a,b)


rows=[State("A",.20,.01),State("B",.15,.01),State("C",.10,.01),
      State("D",.05,.01),State("E",.9,liquidity_ok=False),
      State("S",1.0,lifecycle="shadow"),State("P28_CASH",0)]
member=[x.strategy_id for x in rows]
r=allocate_shadow("US",rows,member)
assert r.mode=="SHADOW_ONLY" and not r.production_mutation and r.frozen_t0_only
assert r.ranked_strategy_ids==["A","B","C","D"]
assert r.exclusions=={"E":"INELIGIBLE_OR_HARD_CONSTRAINT","S":"INELIGIBLE_OR_HARD_CONSTRAINT"}
assert r.allocation_method=="CAPPED_ADAPTIVE_SOFTMAX"
assert 1.0 <= r.allocation_temperature <= 1.75
assert r.weights["A"]>=r.weights["B"]>=r.weights["C"]>=r.weights["D"]>0
assert all(w<=.28+1e-12 for sid,w in r.weights.items() if sid!="P28_CASH")
near(sum(r.weights.values()),1)
assert r.effective_positions>3.0

budget=allocate_shadow("CN",rows,member,risk_budget=.6)
near(budget.weights["P28_CASH"],.4)
near(sum(w for k,w in budget.weights.items() if k!="P28_CASH"),.6)

negative=[State("X",-.05),State("Y",-.10),State("P28_CASH",0)]
relative=allocate_shadow("HK",negative,[x.strategy_id for x in negative])
assert relative.absolute_cash_gate_applied is False and relative.weights["X"]>=relative.weights["Y"]>0
calibrated=allocate_shadow("HK",negative,[x.strategy_id for x in negative],absolute_return_calibrated=True)
assert calibrated.weights=={"P28_CASH":1.0}
assert calibrated.exclusions["X"]=="NOT_ABOVE_CASH_AFTER_COST"
assert calibrated.exclusions["Y"]=="NOT_ABOVE_CASH_AFTER_COST"

# Matched turnover-cost comparison must not churn a feasible incumbent.
incumbent={"A":.28,"B":.28,"C":.28,"D":.16}
churn=allocate_shadow("US",rows,member,previous_weights=incumbent,
                      frozen_incumbent=incumbent,modeled_cost_bps=100)
assert churn.kept_incumbent and churn.weights==incumbent

# A material T0 edge can still trigger a feasible new allocation.
changed=[State("A",.05),State("B",.15),State("C",.10),State("D",.20),State("P28_CASH",0)]
move=allocate_shadow("US",changed,[x.strategy_id for x in changed],
                     frozen_incumbent=incumbent,previous_weights=incumbent,modeled_cost_bps=10)
assert not move.kept_incumbent
assert move.weights["D"]>=move.weights["B"]>=move.weights["C"]>=move.weights["A"]>0
assert move.weights["D"]<=.28+1e-12

# Small score changes must produce continuous weight changes, not a rank cliff.
base=[State("A",.20,recent_returns=[.001]*63),State("B",.19,recent_returns=[.001]*63),
      State("C",.18,recent_returns=[.001]*63),State("P28_CASH",0)]
pert=[State("A",.20,recent_returns=[.001]*63),State("B",.181,recent_returns=[.001]*63),
      State("C",.189,recent_returns=[.001]*63),State("P28_CASH",0)]
b=allocate_shadow("US",base,[x.strategy_id for x in base],risk_budget=.6)
p=allocate_shadow("US",pert,[x.strategy_id for x in pert],risk_budget=.6)
assert b.ranked_strategy_ids==["A","B","C"]
assert p.ranked_strategy_ids==["A","C","B"]
assert abs(p.weights["B"]-b.weights["B"])<.12
assert abs(p.weights["C"]-b.weights["C"])<.12

# More complete T0 history can express score differences more strongly, while
# sparse history stays flatter. This is T0 uncertainty control, not leverage.
sparse=[State("A",.20),State("B",.10),State("C",.05),State("P28_CASH",0)]
deep=[State("A",.20,recent_returns=[.001]*63),
      State("B",.10,recent_returns=[.001]*63),
      State("C",.05,recent_returns=[.001]*63),State("P28_CASH",0)]
sp=allocate_shadow("US",sparse,[x.strategy_id for x in sparse],risk_budget=.6)
dp=allocate_shadow("US",deep,[x.strategy_id for x in deep],risk_budget=.6)
assert sp.allocation_temperature>dp.allocation_temperature
assert (dp.weights["A"]-dp.weights["C"]) >= (sp.weights["A"]-sp.weights["C"])-1e-12

# Infeasible incumbent cannot bypass present-day risk caps or eligibility.
forced=allocate_shadow("CN",rows,member,risk_budget=.5,
                       frozen_incumbent=incumbent,previous_weights=incumbent)
assert not forced.kept_incumbent
near(sum(w for k,w in forced.weights.items() if k!="P28_CASH"),.5)

# Scope isolation, missing states, tie determinism, fail-closed inputs.
ties=[State("B",.1),State("A",.1),State("P28_CASH",0)]
t=allocate_shadow("US",ties,["B","A","P28_CASH"])
assert t.ranked_strategy_ids==["A","B"]
near(t.weights["A"],t.weights["B"])
assert t.market_id=="US" and budget.market_id=="CN" and calibrated.market_id=="HK"
missing=allocate_shadow("HK",ties,["A","MISSING","P28_CASH"])
assert missing.exclusions["MISSING"]=="MISSING_FROZEN_T0_STATE"
for params in ({"risk_budget":float("nan")},{"position_cap":2.0},
               {"modeled_cost_bps":-1.0}):
    try: allocate_shadow("US",ties,["A","B"],**params)
    except ValueError: pass
    else: raise AssertionError(f"invalid parameter accepted: {params}")
try: allocate_shadow("US",ties+[ties[0]],["A","B"])
except ValueError: pass
else: raise AssertionError("duplicate strategy state accepted")

print("TRIAID_FRONTIER_SHADOW_V3_SMOKE_PASS",{
    "tests":13,"production_mutation":False,"mode":r.mode,
    "method":r.allocation_method,
    "temperature":r.allocation_temperature,
    "effective_positions":r.effective_positions,
    "risk_budget_capped":budget.weights,
    "incumbent_hold":churn.kept_incumbent,
})
