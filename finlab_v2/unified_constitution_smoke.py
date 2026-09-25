from __future__ import annotations

from triaid_constitution import (
    ANTI_INACTION_RULE,
    CONSTITUTIONAL_INVARIANT,
    SUPREME_OBJECTIVE,
    constitution,
)
from triaid_fin.objective import constitution as finance_constitution

for domain in ("FINANCE","MEDICINE","RESEARCH","INDUSTRY","PUBLIC_SYSTEMS"):
    row=constitution(domain)
    assert row["supreme_objective"]==SUPREME_OBJECTIVE
    assert row["objective_class_override_allowed"] is False
    assert row["defensiveness_is_terminal_objective"] is False
    assert row["inaction_requires_opportunity_cost_evaluation"] is True
    assert row["anti_inaction_rule"]==ANTI_INACTION_RULE

fin=finance_constitution("US")
assert fin["inherits_global_constitution"] is True
assert fin["supreme_objective"]==SUPREME_OBJECTIVE
assert fin["defensiveness_is_terminal_objective"] is False
assert fin["inaction_requires_opportunity_cost_evaluation"] is True
assert CONSTITUTIONAL_INVARIANT in fin["constitution"]
assert "RISK_LIQUIDITY_CAPACITY_CONCENTRATION_AND_EXECUTION_FEASIBILITY_ARE_CONSTRAINTS" in fin["constitution"]

print("TRIAID_UNIFIED_CONSTITUTION_SMOKE_PASS",{
    "supreme_objective":SUPREME_OBJECTIVE,
    "anti_inaction_rule":ANTI_INACTION_RULE,
})
