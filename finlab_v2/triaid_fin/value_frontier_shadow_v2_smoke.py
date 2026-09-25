"""Offline research-only test suite. No data, network or deployment writes."""
from dataclasses import dataclass
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
for sid,expected in [("A",.28),("B",.28),("C",.28),("D",.16)]: near(r.weights[sid],expected)
near(sum(r.weights.values()),1)

budget=allocate_shadow("CN",rows,member,risk_budget=.6)
near(budget.weights["P28_CASH"],.4)
near(sum(w for k,w in budget.weights.items() if k!="P28_CASH"),.6)

negative=[State("X",-.05),State("Y",-.10),State("P28_CASH",0)]
relative=allocate_shadow("HK",negative,[x.strategy_id for x in negative])
assert relative.absolute_cash_gate_applied is False and relative.weights["X"]>0
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
assert not move.kept_incumbent and move.weights["D"]==.28

# Infeasible incumbent cannot bypass present-day risk caps or eligibility.
forced=allocate_shadow("CN",rows,member,risk_budget=.5,
                       frozen_incumbent=incumbent,previous_weights=incumbent)
assert not forced.kept_incumbent
near(sum(w for k,w in forced.weights.items() if k!="P28_CASH"),.5)

# Scope isolation, missing states, tie-break determinism, fail-closed inputs.
ties=[State("B",.1),State("A",.1),State("P28_CASH",0)]
t=allocate_shadow("US",ties,["B","A","P28_CASH"])
assert t.ranked_strategy_ids==["A","B"]
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

print("TRIAID_FRONTIER_SHADOW_V2_SMOKE_PASS",{
    "tests":10,"production_mutation":False,"mode":r.mode,
    "negative_calibrated_cash_gate":calibrated.weights,
    "risk_budget_capped":budget.weights,
    "incumbent_hold":churn.kept_incumbent,
})
