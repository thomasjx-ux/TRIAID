from __future__ import annotations

VERSION="triaid-fin-release-baseline@1.0.1"
ARCHITECTURE_VERSION="fin-evolution-lab@0.15.0"
ACTIVE_CORE_PARAMETER_VERSION="triaid-core-v2@0.2.0"
CORE_IMPLEMENTATION_VERSION="triaid-core-return-max@0.4.0"
OBJECTIVE_CONSTITUTION_VERSION="fin-objective-constitution@0.2.0"
GLOBAL_CONSTITUTION_VERSION="triaid-constitution@1.0.0"
FORWARD_VALIDATION_PROTOCOL_VERSION="gpt-forward-validation@1.1.0"
OUTCOME_AUTOMATION_VERSION="outcome-resolution-automation@1.0.0"

RELEASE_BASELINE={
    "version":VERSION,
    "architecture_version":ARCHITECTURE_VERSION,
    "active_core_parameter_version":ACTIVE_CORE_PARAMETER_VERSION,
    "core_implementation_version":CORE_IMPLEMENTATION_VERSION,
    "objective_constitution_version":OBJECTIVE_CONSTITUTION_VERSION,
    "global_constitution_version":GLOBAL_CONSTITUTION_VERSION,
    "forward_validation_protocol_version":FORWARD_VALIDATION_PROTOCOL_VERSION,
    "outcome_automation_version":OUTCOME_AUTOMATION_VERSION,
    "research_only":True,
    "broker_execution_enabled":False,
    "markets":["US","CN","HK"],
    "market_status_rule":"US_CN_HK_ARE_PEER_MARKETS_WITH_MARKET_SPECIFIC_CONSTRAINTS_AND_ONE_SHARED_PRIMARY_OBJECTIVE",
    "primary_objective":"MAXIMIZE_REALIZABLE_NET_RETURN",
    "supreme_objective":"MAXIMIZE_LONG_HORIZON_REALIZABLE_EVIDENCE_SUPPORTED_VALUE",
    "evidence_rule":"PROSPECTIVE_T0_T1_ONLY_FOR_FORMAL_VALIDATION_NO_POSTERIOR_RULE_CHANGES",
    "comparison_rule":"EVALUATE_TRIAID_INCREMENTAL_VALUE_VS_NO_INTERVENTION_BENCHMARK_CASH_AND_REGISTERED_SHADOW_CONTROLS_WHEN_AVAILABLE",
    "runtime_topology":{
        "production":{
            "role":"PRODUCTION",
            "persistence_scope":"OFFICIAL",
            "single_writer":True,
            "writer_activation_required":True,
            "release_audit_required":True,
        },
        "shadow":{
            "role":"SHADOW",
            "persistence_scope":"OFFICIAL",
            "single_writer":False,
            "runtime_read_only":True,
            "production_mutation":False,
        },
        "validation":{
            "role":"INDEPENDENT_PROSPECTIVE_VALIDATION",
            "production_mutation":False,
            "append_only_evidence_preferred":True,
        },
    },
    "architecture_rules":[
        "READ_PATHS_MUST_NOT_REQUIRE_OR_TRIGGER_OFFICIAL_PERSISTENCE_MUTATION",
        "OUTCOME_PUBLICATION_RUNS_ONLY_IN_WRITER_ACTIVATED_RUNTIME_AUTOMATION",
        "BUILD_AND_RUNTIME_AUDITS_USE_ISOLATED_STORAGE_AND_MUST_NOT_MUTATE_PRODUCTION",
        "ONLY_ONE_ACTIVATED_PRODUCTION_WRITER_MAY_MUTATE_OFFICIAL_PERSISTENCE",
        "SHADOW_CANDIDATE_BOOTSTRAP_AND_AUDIT_ROLES_MUST_FAIL_CLOSED_ON_OFFICIAL_WRITES",
        "MARKET_REGISTRY_DRIVES_COVERAGE_SO_NO_SUPPORTED_MARKET_IS_SILENTLY_OMITTED",
        "CORE_PROMOTION_REQUIRES_REPLAY_HOLDOUT_SHADOW_AND_AUDIT_EVIDENCE",
    ],
    "product_goal":"TRIAID_FIN_IS_A_FINANCIAL_SYSTEM_STATE_AND_INTERVENTION_RESEARCH_RUNTIME_NOT_A_BROKER_OR_UNVERIFIED_ALPHA_PRODUCT",
}

def release_baseline()->dict:
    return {
        **RELEASE_BASELINE,
        "runtime_topology":{
            key:dict(value)
            for key,value in RELEASE_BASELINE["runtime_topology"].items()
        },
        "architecture_rules":list(RELEASE_BASELINE["architecture_rules"]),
        "markets":list(RELEASE_BASELINE["markets"]),
    }
