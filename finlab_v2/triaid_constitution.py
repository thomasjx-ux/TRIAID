from __future__ import annotations

VERSION="triaid-constitution@1.0.0"
SUPREME_OBJECTIVE="MAXIMIZE_LONG_HORIZON_REALIZABLE_EVIDENCE_SUPPORTED_VALUE"
CONSTITUTIONAL_INVARIANT=(
    "VALUE_MAXIMIZATION_IS_THE_SUPREME_OBJECTIVE; "
    "DOMAIN_VALUE_FUNCTIONS_MAY_DIFFER_BUT_THE_OBJECTIVE_CLASS_MAY_NOT; "
    "RISK_SAFETY_LEGALITY_FAIRNESS_RESOURCE_AND_EXECUTION_LIMITS_ARE_CONSTRAINTS; "
    "DEFENSIVENESS_IS_NOT_SUCCESS_BY_ITSELF; "
    "INACTION_HAS_OPPORTUNITY_COST_AND_REQUIRES_EVIDENCE; "
    "INTERVENTIONS_MUST_BE_JUDGED_BY_INCREMENTAL_LONG_HORIZON_REALIZABLE_VALUE"
)

ANTI_INACTION_RULE=(
    "NON_INTERVENTION_IS_VALID_ONLY_WHEN_EVIDENCE_SUPPORTS_THAT_IT_PRESERVES_OR_INCREASES_"
    "EXPECTED_REALIZABLE_VALUE_RELATIVE_TO_FEASIBLE_ALTERNATIVES_UNDER_THE_SAME_CONSTRAINTS"
)

def constitution(domain:str)->dict:
    return {
        "version":VERSION,
        "domain":str(domain).strip().upper(),
        "supreme_objective":SUPREME_OBJECTIVE,
        "constitutional_invariant":CONSTITUTIONAL_INVARIANT,
        "anti_inaction_rule":ANTI_INACTION_RULE,
        "domain_value_function_may_differ":True,
        "objective_class_override_allowed":False,
        "defensiveness_is_terminal_objective":False,
        "inaction_requires_opportunity_cost_evaluation":True,
        "risk_and_safety_are_constraints":True,
    }
