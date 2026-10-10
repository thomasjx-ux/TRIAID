from __future__ import annotations

import math
from typing import Any

from .contracts import StrategyGroup, StrategyState, TriaidDecision
from .prediction_adjustment import (
    VERSION as PREDICTION_ADJUSTMENT_VERSION,
    build_market_horizon_state,
    build_us_prediction_adjustment,
)
from .risk_transition import apply_fast_brake, detect_state_break, enforce_asset_concentration
from .us_return_max import USReturnMaxRoute


class RiskAwareUSReturnMaxRoute(USReturnMaxRoute):
    """US route with isolated prediction horizons plus hard risk constraints.

    The base route remains the compatibility and control path. This overlay replaces
    promotion/ranking with an internal H20-primary forecast adjustment, keeps H5 as
    an exact tie-break only, keeps H1 diagnostic-only, then applies the existing
    state-break brake and underlying-exposure limits.
    """

    version = "us-return-max-route@0.8.0"
    overlay_version = "us-risk-overlay@0.2.0"

    def _rebuild_capacity(self, panel: Any, input_phase: str | None, target_assets: dict[str, float]) -> dict:
        completed_i = self._completed_index(panel, input_phase)
        adv_by_symbol = {
            asset: self._adv_notional(panel, asset, completed_i)
            for asset in target_assets
        }
        spec = panel.spec
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
                one_day_participation = notional / adv if notional > 0.0 and adv > 0.0 else 0.0
                max_participation = max(max_participation, one_day_participation)
                daily_capacity = adv * float(spec.max_participation_adv) if adv > 0.0 else 0.0
                min_days = (
                    int(math.ceil(notional / daily_capacity))
                    if notional > 0.0 and daily_capacity > 0.0
                    else (0 if notional <= 0.0 else None)
                )
                if isinstance(min_days, int):
                    max_days = max(max_days, min_days)
                elif notional > 0.0:
                    missing_liquidity = True
                planned = min(one_day_participation, float(spec.max_participation_adv)) if one_day_participation > 0.0 else 0.0
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
            if target_notional <= 0.0:
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
                "target_risk_weight": target_notional / float(capital) if capital else 0.0,
                "minimum_execution_days": max_days,
                "max_one_day_participation_adv": max_participation,
                "liquidity_data_complete": not missing_liquidity,
                "capacity_status": capacity_status,
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
            "parameter_provenance": "EXISTING_US_MARKET_SPEC_REUSED_NOT_RETUNED_FOR_PREDICTION_OR_RISK_OVERLAY",
            "broker_specific_fees_included": False,
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
        result = super().decide(
            panel,
            group,
            generic_decision,
            states,
            input_phase,
            previous_decision=previous_decision,
        )

        prediction = build_us_prediction_adjustment(
            states,
            list(result.get("candidate_selection_scores") or []),
            float(result.get("max_strategy_weight_constraint") or 1.0),
        )
        predicted_strategy_weights = dict(prediction.get("target_strategy_weights") or {"P28_CASH": 1.0})

        market_horizon_state = build_market_horizon_state(panel)
        transition = detect_state_break(panel)
        braked_strategy_weights, brake_diag = apply_fast_brake(
            predicted_strategy_weights,
            float(transition.get("risk_cap") or 1.0),
        )
        visible_i = len(panel.ts) - 1
        raw_assets = self._asset_targets(panel, visible_i, braked_strategy_weights)
        target_assets, exposure_diag = enforce_asset_concentration(
            raw_assets,
            risk_assets=getattr(panel.spec, "risk_assets", ()),
            max_single_asset_weight=0.60,
            max_risk_cluster_weight=0.90,
        )

        state_map = {s.strategy_id: s for s in states}
        selected_ids = [
            sid for sid, weight in predicted_strategy_weights.items()
            if sid != "P28_CASH" and float(weight) > 1e-12
        ]
        result["route_version"] = self.version
        result["base_route_version"] = USReturnMaxRoute.version
        result["risk_overlay_version"] = self.overlay_version
        result["prediction_adjustment_version"] = PREDICTION_ADJUSTMENT_VERSION
        result["selection_source"] = "ISOLATED_H20_PREDICTION_ADJUSTMENT_NET_OF_IMMEDIATE_SWITCH_COST"
        result["strategy_selection_mode"] = "H20_PRIMARY_H5_EXACT_TIE_BREAK_H1_DIAGNOSTIC_ONLY_UNDER_HARD_CONSTRAINTS"
        result["selected_strategy_id"] = str(prediction.get("selected_strategy_id") or "P28_CASH")
        result["selected_strategy_ids"] = selected_ids
        result["selected_strategy_count"] = len(selected_ids)
        result["tie_break_order"] = ["H20_NET_RETURN", "H5_NET_RETURN_EXACT_TIE_ONLY", "meta_switch_cost", "strategy_id"]
        result["prediction_adjustment"] = prediction
        result["market_horizon_state"] = market_horizon_state
        result["target_strategy_weights_before_fast_brake"] = predicted_strategy_weights
        result["target_strategy_weights"] = braked_strategy_weights
        result["projected_annualized_expected_net_return"] = self._weighted_expected(
            braked_strategy_weights, state_map
        )
        result["target_asset_weights"] = target_assets
        result["cash_residual_weight"] = max(0.0, 1.0 - sum(target_assets.values()))
        result["state_break"] = transition
        result["fast_brake"] = brake_diag
        result["underlying_exposure_guard"] = exposure_diag
        result["execution_target_source"] = "H20_PREDICTION_MIX_PLUS_STATE_BREAK_AND_UNDERLYING_EXPOSURE_GUARD"
        result["capital_capacity"] = self._rebuild_capacity(panel, input_phase, target_assets)
        if isinstance(result.get("fast_challenger"), dict):
            result["fast_challenger"]["selection_relation"] = "DIAGNOSTIC_ONLY_NOT_USED_BY_H20_PREDICTION_SELECTION"
        result["selection_metric_semantics"] = (
            "The internal prediction overlay no longer treats StrategyState.expected_net_return as a future forecast. "
            "For H20 and H5 it first rescales that legacy historical annualized state estimate to the matching horizon, "
            "blends it without fitted coefficients with same-horizon realized strategy momentum, and caps amplitude at "
            "two trailing daily-volatility sigmas with a fixed minimum noise floor. H20 alone controls promotion and "
            "cross-sectional ordering. H5 is consulted only when H20 net scores are equal to numerical precision. H1 is "
            "diagnostic-only and may not promote, reverse direction, or overwrite H20. Immediate switching cost is "
            "subtracted directly in horizon-return units and is not annualized. The fast state-break layer remains "
            "brake-only and can reduce risk after selection. No coefficient is retuned from realized post-decision outcomes."
        )
        result["projected_field_semantics"] = (
            "Legacy projected_annualized_expected_net_return fields remain for API compatibility and still contain "
            "weighted historical state-return estimates. Calibrated horizon outputs live only under prediction_adjustment."
        )
        return result
