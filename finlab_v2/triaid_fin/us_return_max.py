from __future__ import annotations

import math
from typing import Any

from .contracts import StrategyGroup, StrategyState, TriaidDecision
from .prediction_adjustment import build_market_prediction_adjustment
from . import us_return_max_legacy as _legacy_us
from .us_return_max_legacy import (
    USD_CAPITAL_SLEEVES,
    USReturnMaxLedger,
    USReturnMaxRoute as _LegacyUSReturnMaxRoute,
)

# Compatibility surface for internal/standalone regression hooks that patch
# triaid_fin.us_return_max.policy_positions directly.
policy_positions = _legacy_us.policy_positions


class USReturnMaxRoute(_LegacyUSReturnMaxRoute):
    """US return-max route upgraded with the common H20 opportunity gate."""

    version = "us-return-max-route@0.7.0"

    def _prediction_capacity(
        self,
        panel: Any,
        target_assets: dict[str, float],
        completed_i: int,
    ) -> dict:
        spec = panel.spec
        target_risk_weight = sum(target_assets.values())
        adv_by_symbol = {
            asset: self._adv_notional(panel, asset, completed_i)
            for asset in target_assets
        }
        sleeves = []
        for capital in self.sleeves:
            products = []
            total_entry_cost = 0.0
            target_notional = 0.0
            max_participation = 0.0
            max_days = 0
            missing_liquidity = False
            for asset, weight in target_assets.items():
                notional = float(capital) * float(weight)
                target_notional += notional
                adv = float(adv_by_symbol.get(asset) or 0.0)
                one_day_participation = notional / adv if notional > 0 and adv > 0 else 0.0
                max_participation = max(max_participation, one_day_participation)
                daily_capacity = adv * float(spec.max_participation_adv) if adv > 0 else 0.0
                min_days = (
                    int(math.ceil(notional / daily_capacity))
                    if notional > 0 and daily_capacity > 0
                    else (0 if notional <= 0 else None)
                )
                if isinstance(min_days, int):
                    max_days = max(max_days, min_days)
                elif notional > 0:
                    missing_liquidity = True
                planned = (
                    min(one_day_participation, float(spec.max_participation_adv))
                    if one_day_participation > 0
                    else 0.0
                )
                bps = self._execution_bps(
                    planned,
                    float(spec.base_cost_bps),
                    float(spec.impact_coefficient_bps),
                )
                entry_cost = notional * bps / 10000.0
                total_entry_cost += entry_cost
                products.append({
                    "symbol": asset,
                    "target_weight": float(weight),
                    "target_notional_usd": notional,
                    "adv20_notional_usd": adv,
                    "one_day_participation_adv": one_day_participation,
                    "planned_participation_adv": planned,
                    "daily_capacity_notional_usd": daily_capacity,
                    "minimum_execution_days": min_days,
                    "all_in_bps_per_side": bps,
                    "estimated_entry_cost_usd": entry_cost,
                })
            if target_notional <= 0:
                capacity_status = "NO_RISK_POSITION"
            elif missing_liquidity:
                capacity_status = "LIQUIDITY_DATA_UNAVAILABLE"
            elif max_days <= 1:
                capacity_status = "ONE_DAY_WITHIN_PARTICIPATION_CAP"
            else:
                capacity_status = "MULTI_DAY_EXECUTION_REQUIRED"
            sleeves.append({
                "sleeve_id": f"USD_{int(capital)}",
                "starting_capital_usd": float(capital),
                "starting_cash_only": True,
                "target_invested_notional_usd": target_notional,
                "target_cash_notional_usd": max(0.0, float(capital) - target_notional),
                "target_risk_weight": target_notional / float(capital) if capital > 0 else 0.0,
                "max_one_day_participation_adv": max_participation,
                "minimum_execution_days": max_days,
                "capacity_status": capacity_status,
                "liquidity_data_complete": not missing_liquidity,
                "estimated_entry_cost_usd": total_entry_cost,
                "estimated_round_trip_cost_proxy_usd": 2.0 * total_entry_cost,
                "products": products,
            })
        return {
            "version": self.capital_version,
            "currency": "USD",
            "capital_sleeves_usd": [int(x) for x in self.sleeves],
            "adv_lookback_days": self.adv_lookback,
            "adv_source": "REAL_REPORTED_VOLUME_X_CLOSE_NOTIONAL_PROXY",
            "completed_liquidity_index": completed_i,
            "base_cost_bps": float(spec.base_cost_bps),
            "impact_coefficient_bps": float(spec.impact_coefficient_bps),
            "max_participation_adv": float(spec.max_participation_adv),
            "impact_formula": "impact_bps = impact_coefficient_bps * sqrt(executed_notional / observed_ADV_notional)",
            "parameter_provenance": "EXISTING_US_MARKET_SPEC_REUSED_NOT_RETUNED_FOR_THIS_EXPERIMENT",
            "broker_specific_fees_included": False,
            "target_risk_weight": target_risk_weight,
            "sleeves": sleeves,
        }

    def decide(
        self,
        panel: Any,
        group: StrategyGroup,
        generic_decision: TriaidDecision,
        states: list[StrategyState],
        input_phase: str | None,
        previous_decision: dict | None = None,
    ) -> dict:
        original_policy_positions = _legacy_us.policy_positions
        _legacy_us.policy_positions = policy_positions
        try:
            legacy = super().decide(
                panel,
                group,
                generic_decision,
                states,
                input_phase,
                previous_decision,
            )
        finally:
            _legacy_us.policy_positions = original_policy_positions

        prediction_ready = any(
            str(state.strategy_id) != "P28_CASH" and len(state.recent_returns) >= 20
            for state in states
        )
        if not prediction_ready:
            legacy.update({
                "route_version": _LegacyUSReturnMaxRoute.version,
                "prediction_adjustment_ready": False,
                "prediction_adjustment_version": None,
                "prediction_framework_version": None,
                "prediction_decision_state": None,
                "market_opportunity_state": None,
                "prediction_selected_strategy_ids": [],
                "prediction_invalidation_contract": None,
            })
            return legacy

        hard_cap = max(
            1e-12,
            min(1.0, float(legacy.get("max_strategy_weight_constraint") or 1.0)),
        )
        adjustment = build_market_prediction_adjustment(
            "US",
            states,
            list(legacy.get("candidate_selection_scores") or []),
            hard_cap,
            input_phase,
        )
        route_weights = {
            str(k): float(v)
            for k, v in (adjustment.get("target_strategy_weights") or {}).items()
        }
        state_map = {str(s.strategy_id): s for s in states}
        visible_i = len(panel.ts) - 1
        completed_i = self._completed_index(panel, input_phase)
        target_assets = self._asset_targets(panel, visible_i, route_weights)
        target_risk_weight = sum(target_assets.values())
        route_expected = self._weighted_expected(route_weights, state_map)
        selected_ids = [
            sid for sid, weight in route_weights.items()
            if sid != "P28_CASH" and float(weight) > 1e-12
        ]
        selected_primary = str(adjustment.get("selected_strategy_id") or "P28_CASH")

        legacy.update({
            "route_version": self.version,
            "prediction_adjustment_ready": True,
            "selection_source": "H20_PREDICTION_ADJUSTMENT_NET_OF_META_SWITCH_COST",
            "strategy_selection_mode": "H20_PRIMARY_PLUS_MARKET_OPPORTUNITY_GATE_WITH_US_EXECUTION_CONSTRAINTS",
            "selected_strategy_id": selected_primary,
            "selected_strategy_ids": selected_ids,
            "selected_strategy_count": len(selected_ids),
            "selection_holding_horizon_days": 20,
            "target_strategy_weights": route_weights,
            "execution_target_source": "PREDICTION_ADJUSTED_FULL_FROZEN_STRATEGY_MIX",
            "projected_annualized_expected_net_return": route_expected,
            "target_asset_weights": target_assets,
            "cash_residual_weight": max(0.0, 1.0 - target_risk_weight),
            "capital_capacity": self._prediction_capacity(panel, target_assets, completed_i),
            "prediction_adjustment_version": adjustment.get("version"),
            "prediction_framework_version": adjustment.get("framework_version"),
            "prediction_decision_state": adjustment.get("decision_state"),
            "market_opportunity_state": adjustment.get("market_opportunity_state"),
            "prediction_selected_strategy_ids": adjustment.get("selected_strategy_ids"),
            "prediction_invalidation_contract": adjustment.get("invalidation_contract"),
            "selection_metric_semantics": (
                "US primary selection uses the isolated H20 prediction-adjustment layer when a complete H20 strategy history exists. "
                "Immediate meta-switch cost is subtracted on the same H20 return scale; H5 is an exact H20 tie-break only; "
                "H1 remains diagnostic-only. Opportunity Density can reduce exposure or produce an explicit No Trade state. "
                "Risk, liquidity, capacity and concentration remain feasibility constraints rather than co-equal objectives."
            ),
        })
        return legacy


__all__ = ["USD_CAPITAL_SLEEVES", "USReturnMaxRoute", "USReturnMaxLedger", "policy_positions"]
