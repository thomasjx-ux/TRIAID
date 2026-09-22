from __future__ import annotations

VERSION="fin-objective-constitution@0.1.0"
PRIMARY_OBJECTIVE="MAXIMIZE_REALIZABLE_NET_RETURN"
OBJECTIVE_CONSTITUTION=(
    "RETURN_IS_THE_ONLY_OPTIMIZATION_OBJECTIVE; "
    "RISK_LIQUIDITY_CAPACITY_CONCENTRATION_AND_EXECUTION_FEASIBILITY_ARE_CONSTRAINTS; "
    "REAL_SWITCHING_AND_EXECUTION_COSTS_ARE_DEDUCTED_FROM_RETURN; "
    "MARKET_SPECIFIC_RULES_MAY_CHANGE_CONSTRAINTS_BUT_MUST_NOT_CHANGE_THE_PRIMARY_OBJECTIVE"
)


def constitution(market_id:str)->dict:
    return {
        "version":VERSION,
        "market_id":str(market_id).upper(),
        "primary_objective":PRIMARY_OBJECTIVE,
        "constitution":OBJECTIVE_CONSTITUTION,
        "market_specific_objective_override_allowed":False,
        "market_specific_constraints_allowed":True,
    }
