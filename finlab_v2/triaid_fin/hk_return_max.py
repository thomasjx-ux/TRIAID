from __future__ import annotations

from typing import Any

from .contracts import StrategyGroup, StrategyState, TriaidDecision
from .hk_return_max_legacy import (
    HKD_CAPITAL_SLEEVES,
    HKReturnMaxLedger,
    HKReturnMaxRoute as _LegacyHKReturnMaxRoute,
)


class HKReturnMaxRoute(_LegacyHKReturnMaxRoute):
    """HK return-max route surfaced through the common prediction opportunity gate."""

    version = "hk-return-max-route@0.3.0"

    def decide(
        self,
        panel: Any,
        group: StrategyGroup,
        triaid_decision: TriaidDecision,
        states: list[StrategyState],
        input_phase: str | None,
    ) -> dict:
        result = super().decide(
            panel,
            group,
            triaid_decision,
            states,
            input_phase,
        )
        diagnostics = dict(triaid_decision.diagnostics or {})
        prediction_version = diagnostics.get("prediction_adjustment_version")
        if not prediction_version:
            result.update({
                "prediction_adjustment_ready": False,
                "prediction_adjustment_version": None,
                "prediction_framework_version": None,
                "prediction_decision_state": None,
                "market_opportunity_state": None,
                "prediction_selected_strategy_ids": [],
                "prediction_invalidation_contract": None,
            })
            return result

        result.update({
            "route_version": self.version,
            "prediction_adjustment_ready": True,
            "selection_source": "HK_PREDICTION_ADJUSTED_TRIAID_CORE",
            "strategy_selection_mode": "H20_PRIMARY_PLUS_HK_MARKET_OPPORTUNITY_GATE_WITH_HK_EXECUTION_CONSTRAINTS",
            "prediction_adjustment_version": prediction_version,
            "prediction_framework_version": diagnostics.get("prediction_framework_version"),
            "prediction_decision_state": diagnostics.get("prediction_decision_state"),
            "market_opportunity_state": diagnostics.get("market_opportunity_state"),
            "prediction_selected_strategy_ids": diagnostics.get("prediction_selected_strategy_ids") or [],
            "prediction_invalidation_contract": diagnostics.get("prediction_invalidation_contract"),
            "selection_metric_semantics": (
                "HK strategy weights are produced by the common H20 prediction-adjustment layer inside TRIAID Core when a complete H20 history exists. "
                "H5 remains an exact H20 tie-break only and H1 remains diagnostic-only. The HK-specific Opportunity Density "
                "profile can reduce risk exposure or produce an explicit No Trade state before the existing HK execution and capacity layer is applied."
            ),
        })
        return result


__all__ = ["HKD_CAPITAL_SLEEVES", "HKReturnMaxRoute", "HKReturnMaxLedger"]
