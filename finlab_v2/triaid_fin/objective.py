from __future__ import annotations

from triaid_constitution import CONSTITUTIONAL_INVARIANT, SUPREME_OBJECTIVE, ANTI_INACTION_RULE

VERSION="fin-objective-constitution@0.2.0"
PRIMARY_OBJECTIVE="MAXIMIZE_REALIZABLE_NET_RETURN"
OBJECTIVE_CONSTITUTION=(
    CONSTITUTIONAL_INVARIANT+"; "
    "FINANCE_INSTANTIATION_OF_GLOBAL_VALUE_OBJECTIVE; "
    "RETURN_IS_THE_ONLY_OPTIMIZATION_OBJECTIVE; "
    "RISK_LIQUIDITY_CAPACITY_CONCENTRATION_AND_EXECUTION_FEASIBILITY_ARE_CONSTRAINTS; "
    "REAL_SWITCHING_AND_EXECUTION_COSTS_ARE_DEDUCTED_FROM_RETURN; "
    "MARKET_SPECIFIC_RULES_MAY_CHANGE_CONSTRAINTS_BUT_MUST_NOT_CHANGE_THE_PRIMARY_OBJECTIVE"
)


def constitution(market_id:str)->dict:
    return {
        "version":VERSION,
        "market_id":str(market_id).upper(),
        "supreme_objective":SUPREME_OBJECTIVE,
        "primary_objective":PRIMARY_OBJECTIVE,
        "anti_inaction_rule":ANTI_INACTION_RULE,
        "constitution":OBJECTIVE_CONSTITUTION,
        "market_specific_objective_override_allowed":False,
        "market_specific_constraints_allowed":True,
        "inherits_global_constitution":True,
        "defensiveness_is_terminal_objective":False,
        "inaction_requires_opportunity_cost_evaluation":True,
    }
