"""Independent full-core shadow candidate.

The production lifecycle labels are deliberately ignored. Established audited
policies are re-admitted from the frozen T0 state when current hard constraints
pass; non-incumbent candidates remain shadow until they accumulate their own
prospective evidence.

V3 keeps the population optimizer's top group as an explanatory shortlist only.
Allocation sees the complete currently admissible established policy bank and
uses a proximal sparse state transition from the actual pre-decision portfolio.
Production-core weights are retained only for comparison diagnostics and do not
gate the candidate allocation.

This module has no storage, broker, runtime, or deployment imports and cannot
mutate production state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .contracts import StrategyState
from .strategy_population import StrategyPopulationModule
from .strategy_registry import POLICY_IDS
from .value_frontier_shadow_v2 import allocate_shadow


VERSION = "full-core-shadow@0.3.0"
LIFECYCLE_VERSION = "independent-lifecycle@0.1.0"
ESTABLISHED_POLICY_IDS = frozenset(POLICY_IDS)


@dataclass(frozen=True)
class FullCoreShadowDecision:
    market_id: str
    version: str
    lifecycle_version: str
    state_count: int
    lifecycle_counts: dict[str, int]
    lifecycle_changes_vs_production: dict[str, dict[str, str]]
    lifecycle_reasons: dict[str, str]
    candidate_group_members: list[str]
    candidate_group_weights: dict[str, float]
    candidate_group_diagnostics: dict[str, Any]
    allocation_member_ids: list[str]
    allocation_member_count: int
    shortlist_is_allocation_gate: bool
    production_weights_raw: dict[str, float]
    production_weights: dict[str, float]
    shadow_weights: dict[str, float]
    weight_delta: dict[str, float]
    allocation: dict[str, Any]
    configured_risk_budget: float
    allocation_risk_budget: float
    risk_budget_basis: str
    independent_lifecycle: bool = True
    ignores_production_lifecycle_labels: bool = True
    uses_full_frozen_t0_state_pool: bool = True
    reads_t1_for_allocation: bool = False
    production_mutation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hard_admissible(state: StrategyState) -> bool:
    return bool(
        state.eligible
        and not state.hard_failure
        and state.liquidity_ok
        and state.capacity_ok
        and state.risk_ok
        and state.concentration_ok
    )


def independent_lifecycle(
    states: Iterable[StrategyState],
) -> tuple[list[StrategyState], dict[str, str], dict[str, dict[str, str]]]:
    """Rebuild lifecycle without reading PopulationStateTracker memory."""
    out: list[StrategyState] = []
    reasons: dict[str, str] = {}
    changes: dict[str, dict[str, str]] = {}
    for raw in states:
        state=raw.model_copy(deep=True)
        old=str(raw.lifecycle)
        sid=str(raw.strategy_id)
        if not _hard_admissible(raw):
            new="frozen"
            reason="CURRENT_HARD_ADMISSIBILITY_FAILED"
        elif sid=="P28_CASH":
            new="active"
            reason="CASH_IS_EXPLICIT_FEASIBLE_POLICY"
        elif sid in ESTABLISHED_POLICY_IDS:
            new="active"
            reason="ESTABLISHED_AUDITED_POLICY_CURRENT_HARD_CONSTRAINTS_PASS"
        else:
            new="shadow"
            reason="NON_INCUMBENT_REQUIRES_INDEPENDENT_PROSPECTIVE_EVIDENCE"
        state.lifecycle=new
        out.append(state)
        reasons[sid]=reason
        if old!=new:
            changes[sid]={"production_lifecycle":old,"shadow_lifecycle":new}
    return out,reasons,changes


def _canonical_cash_weights(raw: Mapping[str, float] | None) -> dict[str, float] | None:
    """Make implicit unallocated capital explicit cash without changing exposure."""
    if raw is None:
        return None
    risky={
        str(k):float(v)
        for k,v in raw.items()
        if str(k)!="P28_CASH" and float(v)>0.0
    }
    risky_total=sum(risky.values())
    if risky_total>1.0+1e-12:
        raise ValueError("weights exceed 100%")
    cash=max(0.0,1.0-risky_total)
    if cash>1e-12:
        risky["P28_CASH"]=cash
    return risky


def run_full_core_shadow(
    market_id: str,
    states: Iterable[StrategyState],
    *,
    production_weights: Mapping[str, float],
    previous_weights: Mapping[str, float] | None = None,
    population: StrategyPopulationModule | None = None,
    max_members: int = 10,
    risk_budget: float = 1.0,
    max_strategy_weight: float = 0.28,
    modeled_cost_bps: float = 0.0,
    absolute_return_calibrated: bool = False,
) -> FullCoreShadowDecision:
    frozen_states=list(states)
    shadow_states,lifecycle_reasons,lifecycle_changes=independent_lifecycle(frozen_states)
    population=population or StrategyPopulationModule()

    group=population.select(
        str(market_id).upper(),
        shadow_states,
        max(1,int(max_members)),
        previous_group=None,
        base_cost_bps=max(0.0,float(modeled_cost_bps)),
        experiment_mode="FULL_CORE_SHADOW",
        max_weight_override=max_strategy_weight,
        allow_shadow_simulation=False,
    )

    allocation_members=sorted(
        {
            str(state.strategy_id)
            for state in shadow_states
            if _hard_admissible(state)
            and str(state.lifecycle) in {"active","reduced"}
            and (
                str(state.strategy_id)=="P28_CASH"
                or str(state.strategy_id) in ESTABLISHED_POLICY_IDS
            )
        }
    )

    production_raw={str(k):float(v) for k,v in production_weights.items()}
    production=_canonical_cash_weights(production_raw) or {}
    previous=_canonical_cash_weights(previous_weights)

    production_risky_exposure=sum(
        float(w) for sid,w in production.items() if sid!="P28_CASH"
    )
    predecision_risky_exposure=(
        sum(float(w) for sid,w in previous.items() if sid!="P28_CASH")
        if previous is not None else production_risky_exposure
    )

    if absolute_return_calibrated:
        allocation_risk_budget=float(risk_budget)
        risk_budget_basis="ABSOLUTE_RETURN_CALIBRATED_CONFIGURED_RISK_BUDGET"
    else:
        allocation_risk_budget=max(
            0.0,
            min(float(risk_budget),predecision_risky_exposure),
        )
        risk_budget_basis="RELATIVE_ONLY_PRESERVE_PREDECISION_RISKY_EXPOSURE"

    allocation=allocate_shadow(
        str(market_id).upper(),
        shadow_states,
        allocation_members,
        risk_budget=allocation_risk_budget,
        position_cap=float(max_strategy_weight),
        frozen_incumbent=production,
        previous_weights=previous,
        modeled_cost_bps=modeled_cost_bps,
        absolute_return_calibrated=absolute_return_calibrated,
    )
    shadow={str(k):float(v) for k,v in allocation.weights.items()}
    ids=sorted(set(production)|set(shadow))
    counts: dict[str,int]={}
    for state in shadow_states:
        counts[state.lifecycle]=counts.get(state.lifecycle,0)+1

    return FullCoreShadowDecision(
        market_id=str(market_id).upper(),
        version=VERSION,
        lifecycle_version=LIFECYCLE_VERSION,
        state_count=len(shadow_states),
        lifecycle_counts=counts,
        lifecycle_changes_vs_production=lifecycle_changes,
        lifecycle_reasons=lifecycle_reasons,
        candidate_group_members=list(group.members),
        candidate_group_weights={str(k):float(v) for k,v in group.weights.items()},
        candidate_group_diagnostics=dict(group.diagnostics or {}),
        allocation_member_ids=allocation_members,
        allocation_member_count=len(allocation_members),
        shortlist_is_allocation_gate=False,
        production_weights_raw=production_raw,
        production_weights=production,
        shadow_weights=shadow,
        weight_delta={sid:shadow.get(sid,0.0)-production.get(sid,0.0) for sid in ids},
        allocation=allocation.to_dict(),
        configured_risk_budget=float(risk_budget),
        allocation_risk_budget=allocation_risk_budget,
        risk_budget_basis=risk_budget_basis,
    )
