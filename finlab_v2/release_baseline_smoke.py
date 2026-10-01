from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine
from triaid_fin.release_manifest import (
    ARCHITECTURE_VERSION,
    ACTIVE_CORE_PARAMETER_VERSION,
    CORE_IMPLEMENTATION_VERSION,
    RELEASE_BASELINE,
    release_baseline,
)

baseline=release_baseline()

assert baseline["architecture_version"]==ARCHITECTURE_VERSION
assert EvolutionLabEngine.architecture_version==ARCHITECTURE_VERSION
assert baseline["active_core_parameter_version"]==ACTIVE_CORE_PARAMETER_VERSION
assert baseline["core_implementation_version"]==CORE_IMPLEMENTATION_VERSION
assert baseline["markets"]==["US","CN","HK"]
assert baseline["research_only"] is True
assert baseline["broker_execution_enabled"] is False
assert baseline["runtime_topology"]["production"]["single_writer"] is True
assert baseline["runtime_topology"]["production"]["writer_activation_required"] is True
assert baseline["runtime_topology"]["shadow"]["runtime_read_only"] is True
assert baseline["runtime_topology"]["shadow"]["production_mutation"] is False
assert "READ_PATHS_MUST_NOT_REQUIRE_OR_TRIGGER_OFFICIAL_PERSISTENCE_MUTATION" in baseline["architecture_rules"]
assert "CORE_PROMOTION_REQUIRES_REPLAY_HOLDOUT_SHADOW_AND_AUDIT_EVIDENCE" in baseline["architecture_rules"]

print({
    "passed":True,
    "baseline_version":RELEASE_BASELINE["version"],
    "architecture_version":ARCHITECTURE_VERSION,
    "active_core_parameter_version":ACTIVE_CORE_PARAMETER_VERSION,
    "core_implementation_version":CORE_IMPLEMENTATION_VERSION,
})
