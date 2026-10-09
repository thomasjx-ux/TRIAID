from __future__ import annotations

import math
from typing import Any

from .contracts import StrategyGroup, StrategyState, TriaidDecision
from .risk_transition import apply_fast_brake, detect_state_break, enforce_asset_concentration
from .us_return_max import USReturnMaxRoute


class RiskAwareUSReturnMaxRoute(USReturnMaxRoute):
    """US route overlay that adds state-break braking and underlying exposure limits.

    The parent route still owns the return ranking. This layer only adds hard
    execution constraints after that ranking, preserving the objective hierarchy.
    """

    version = "us-return-max-route@0.7.0"
    overlay_version = "us-risk-overlay@0.1.0"

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
            "parameter_provenance": "EXISTING_US_MARKET_SPEC_REUSED_NOT_RETUNED_FOR_RISK_OVERLAY",
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
        base_route_version = str(result.get("route_version") or USReturnMaxRoute.version)
        transition = detect_state_break(panel)

        braked_strategy_weights, brake_diag = apply_fast_brake(
            dict(result.get("target_strategy_weights") or {}),
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
        result["route_version"] = self.version
        result["base_route_version"] = base_route_version
        result["risk_overlay_version"] = self.overlay_version
        result["target_strategy_weights"] = braked_strategy_weights
        result["projected_annualized_expected_net_return"] = self._weighted_expected(
            braked_strategy_weights, state_map
        )
        result["target_asset_weights"] = target_assets
        result["cash_residual_weight"] = max(0.0, 1.0 - sum(target_assets.values()))
        result["state_break"] = transition
        result["fast_brake"] = brake_diag
        result["underlying_exposure_guard"] = exposure_diag
        result["execution_target_source"] = "FROZEN_STRATEGY_MIX_PLUS_STATE_BREAK_AND_UNDERLYING_EXPOSURE_GUARD"
        result["capital_capacity"] = self._rebuild_capacity(panel, input_phase, target_assets)
        result["selection_metric_semantics"] = (
            str(result.get("selection_metric_semantics") or "")
            + " Fast 1/3/5-day evidence is brake-only: it can reduce risk but cannot reverse "
              "direction or promote a strategy. Final execution weights also pass a hard "
              "underlying-asset concentration guard so multiple strategies cannot hide the "
              "same concentrated exposure."
        )
        return result
