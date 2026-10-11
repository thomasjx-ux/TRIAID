from __future__ import annotations

from statistics import mean
from typing import Any

from .opportunity_state import build_opportunity_state_from_components
from .recovery_core_legacy import *
from .recovery_core_legacy import RecoveryWaveCore as _LegacyRecoveryWaveCore


class RecoveryWaveCore(_LegacyRecoveryWaveCore):
    """CN recovery route with market opportunity density and explicit No Trade gating."""

    version = "recovery-wave-core@0.5.0"

    @staticmethod
    def _persistence_value(state_direction: str) -> float:
        return {
            "IMPROVING_FAST": 1.0,
            "IMPROVING": 0.75,
            "MIXED": 0.40,
            "DETERIORATING": 0.10,
        }.get(str(state_direction or "").upper(), 0.0)

    def decide(
        self,
        panel: Any,
        regime: str | None,
        previous_decision: dict | None = None,
        input_phase: str | None = None,
    ) -> dict:
        decision = super().decide(panel, regime, previous_decision, input_phase)
        opinions = [dict(row) for row in decision.get("trade_opinions") or []]
        positive = [
            row for row in opinions
            if float(row.get("target_weight") or 0.0) > 0.0
            and float(row.get("expected_forward_return_edge") or 0.0) > 0.0
            and float(row.get("historical_recovery_edge") or 0.0) > 0.0
        ]
        admissible_count = len(opinions)
        positive_count = len(positive)
        breadth = positive_count / float(admissible_count) if admissible_count else 0.0
        edge_quality = mean([
            min(1.0, max(0.0, float(row.get("expected_forward_return_edge") or 0.0)) / 0.05)
            for row in positive
        ]) if positive else 0.0
        validation_target = max(1.0, float(self.analog_count) / 2.0)
        history_coverage = mean([
            min(1.0, max(0.0, float(row.get("analog_validation_samples") or 0.0)) / validation_target)
            for row in positive
        ]) if positive else 0.0
        persistence_quality = mean([
            self._persistence_value(str(row.get("state_direction") or ""))
            for row in positive
        ]) if positive else 0.0

        opportunity_state = build_opportunity_state_from_components(
            "CN",
            breadth,
            edge_quality,
            history_coverage,
            persistence_quality,
            positive_count,
            admissible_count,
            "CN_RECOVERY_NATIVE_ANALOG_HORIZONS",
        )
        multiplier = (
            float(opportunity_state.get("exposure_multiplier") or 0.0)
            if bool(opportunity_state.get("trade_enabled"))
            else 0.0
        )

        gated = []
        for row in opinions:
            new_row = dict(row)
            original_target = max(0.0, float(row.get("target_weight") or 0.0))
            target = original_target * multiplier
            previous = max(0.0, float(row.get("previous_target_weight") or 0.0))
            delta = round(target - previous, 8)
            if previous <= 0 and target > 0:
                action = "INITIATE"
            elif previous > 0 and target > previous:
                action = "ADD"
            elif previous > 0 and target == 0:
                action = "EXIT"
            elif target < previous:
                action = "REDUCE"
            elif target > 0:
                action = "HOLD"
            else:
                action = "WAIT"
            new_row.update({
                "pre_opportunity_target_weight": original_target,
                "target_weight": target,
                "suggested_weight_change": delta,
                "action": action,
                "opportunity_exposure_multiplier": multiplier,
            })
            gated.append(new_row)

        decision["core_version"] = self.version
        decision["trade_opinions"] = gated
        decision["capital_capacity"] = self.capital_capacity.build(panel, gated, input_phase)
        decision["cash_residual_weight"] = max(
            0.0,
            round(1.0 - sum(float(row.get("target_weight") or 0.0) for row in gated), 8),
        )
        decision["decision_state"] = "ACTIVE" if any(float(row.get("target_weight") or 0.0) > 1e-12 for row in gated) else "NO_TRADE"
        decision["market_opportunity_state"] = opportunity_state
        decision["prediction_invalidation_contract"] = {
            "soft_invalidation": "CN_MARKET_OPPORTUNITY_REGIME_LEVEL_LT_2",
            "hard_invalidation": "RECOVERY_EDGE_OR_ANALOG_VALIDATION_LOST",
            "latency_metric": "INVALIDATION_LATENCY_IN_COMPLETED_OBSERVATIONS",
            "future_outcome_requirement": "LATENCY_REQUIRES_SEQUENTIAL_POST_DECISION_EVIDENCE",
        }
        second_order = dict(decision.get("second_order") or {})
        second_order["pre_opportunity_risk_budget"] = float(second_order.get("risk_budget") or 0.0)
        second_order["opportunity_exposure_multiplier"] = multiplier
        second_order["risk_budget"] = sum(float(row.get("target_weight") or 0.0) for row in gated)
        second_order["market_opportunity_state"] = opportunity_state
        decision["second_order"] = second_order
        method = dict(decision.get("method") or {})
        method["market_gate"] = "MARKET_OPPORTUNITY_DENSITY_WITH_CN_SPECIFIC_THRESHOLDS_AND_NO_TRADE_STATE"
        method["persistence"] = "ROUTE_NATIVE_RECOVERY_STATE_DIRECTION"
        decision["method"] = method
        return decision


__all__ = ["RecoveryWaveCore"]
