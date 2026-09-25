from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import StrategyState


@dataclass(frozen=True)
class ValueFrontierResult:
    weights: dict[str,float]
    ranked_strategy_ids: list[str]
    excluded_strategy_ids: list[str]
    risk_budget: float
    position_cap: float
    objective: str = "MAXIMIZE_REALIZABLE_NET_RETURN_SUBJECT_TO_HARD_CONSTRAINTS"


class ValueFrontierAllocator:
    """Deterministic return-first candidate allocator.

    This allocator is intentionally simple: hard feasibility first, then rank by
    frozen T0 expected net return and greedily fill the available risk budget up
    to the per-strategy cap. It is a candidate/shadow policy, not an oracle and
    never reads realized T1 outcomes.
    """

    version="value-frontier-allocator@0.1.0"

    @staticmethod
    def allocate(
        states: Iterable[StrategyState],
        *,
        risk_budget: float=1.0,
        position_cap: float=0.28,
        allow_shadow: bool=False,
    )->ValueFrontierResult:
        budget=max(0.0,min(1.0,float(risk_budget)))
        cap=max(1e-9,min(1.0,float(position_cap)))
        ranked=[]
        excluded=[]
        cash_available=False
        for state in states:
            sid=str(state.strategy_id)
            if sid=="P28_CASH":
                cash_available=True
                continue
            admissible=(
                not state.hard_failure
                and state.liquidity_ok
                and state.capacity_ok
                and state.risk_ok
                and state.concentration_ok
                and (
                    state.lifecycle in {"active","reduced"}
                    or (allow_shadow and state.lifecycle=="shadow")
                )
                and state.eligible
            )
            if not admissible:
                excluded.append(sid)
                continue
            score=float(state.expected_net_return)-max(0.0,float(state.estimated_cost))
            ranked.append((sid,score))
        ranked.sort(key=lambda row:(-row[1],row[0]))

        remaining=budget
        weights={}
        for sid,_score in ranked:
            if remaining<=1e-12:
                break
            weight=min(cap,remaining)
            if weight>1e-12:
                weights[sid]=weight
                remaining-=weight

        # Risk budget below 100% or insufficient admissible capped capacity is
        # residual cash, never forced into a lower-ranked risky strategy.
        residual=max(0.0,1.0-sum(weights.values()))
        if cash_available and residual>1e-12:
            weights["P28_CASH"]=residual

        return ValueFrontierResult(
            weights=weights,
            ranked_strategy_ids=[sid for sid,_ in ranked],
            excluded_strategy_ids=sorted(excluded),
            risk_budget=budget,
            position_cap=cap,
        )
