from __future__ import annotations

from typing import Iterable

from .contracts import MarketSnapshot, StrategyGroup, StrategyState
from .value_frontier import ValueFrontierAllocator


class ValueFrontierResearchCore:
    """Research-only wrapper around the value-frontier allocator.

    This module never mutates the production Core, Evolution state, run ledger,
    strategy lifecycle, or live account configuration. It exists to run the
    candidate on frozen decision-time inputs for prospective comparison.
    """

    version="value-frontier-research-core@0.1.0"
    production_mutation_allowed=False

    @classmethod
    def evaluate(
        cls,
        market: MarketSnapshot,
        group: StrategyGroup,
        states: Iterable[StrategyState],
    )->dict:
        member_ids=set(group.members)
        relevant=[s for s in states if s.strategy_id in member_ids]
        position_cap=float((group.diagnostics or {}).get("max_strategy_weight_constraint",0.28) or 0.28)
        risk_budget=float((market.metadata or {}).get("account_risk_budget",1.0) or 1.0)
        result=ValueFrontierAllocator.allocate(
            relevant,
            risk_budget=risk_budget,
            position_cap=position_cap,
            allow_shadow=False,
        )
        return {
            "version":cls.version,
            "allocator_version":ValueFrontierAllocator.version,
            "mode":"RESEARCH_ONLY",
            "production_mutation_allowed":False,
            "market_id":market.market_id,
            "as_of":market.as_of,
            "snapshot_id":market.snapshot_id,
            "weights":dict(result.weights),
            "ranked_strategy_ids":list(result.ranked_strategy_ids),
            "excluded_strategy_ids":list(result.excluded_strategy_ids),
            "risk_budget":result.risk_budget,
            "position_cap":result.position_cap,
            "objective":result.objective,
            "uses_frozen_t0_information_only":True,
            "reads_realized_t1_to_choose_weights":False,
        }
