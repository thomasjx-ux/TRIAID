from __future__ import annotations

import math
from typing import Any

from .contracts import StrategyGroup, StrategyState, TriaidDecision
from .exposure_control import assess_exposure_guard, cluster_weights
from .us_return_max import USReturnMaxRoute


class StateAwareUSReturnMaxRoute(USReturnMaxRoute):
    """US primary route with a fast brake and post-strategy exposure guard.

    The parent route still owns return ranking and strategy selection. This layer may only
    scale aggregate risk downward. It cannot change strategy ordering or create a bearish
    direction from short-horizon evidence.
    """

    version = "us-return-max-route@0.7.0"
    guard_version = "us-route-risk-guard@0.1.0"

    @staticmethod
    def _scale_risky(weights: dict[str, float], factor: float) -> dict[str, float]:
        factor = max(0.0, min(1.0, float(factor)))
        out: dict[str, float] = {}
        risky_total = 0.0
        for sid, weight in weights.items():
            value = max(0.0, float(weight))
            if sid == "P28_CASH":
                continue
            risky_total += value
            if value > 1e-15:
                out[str(sid)] = value * factor
        cash_before = max(0.0, float(weights.get("P28_CASH", 0.0)))
        cash_after = cash_before + risky_total * (1.0 - factor)
        if cash_after > 1e-15:
            out["P28_CASH"] = cash_after
        return out

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

        state_break = dict((generic_decision.diagnostics or {}).get("state_break") or {})
        brake_factor = max(0.0, min(1.0, float(state_break.get("brake_factor", 1.0) or 1.0)))
        parent_weights = {
            str(k): max(0.0, float(v))
            for k, v in dict(result.get("target_strategy_weights") or {}).items()
        }
        guarded_weights = self._scale_risky(parent_weights, brake_factor)

        visible_i = len(panel.ts) - 1
        assets_after_brake = self._asset_targets(panel, visible_i, guarded_weights)
        asset_cap = (group.diagnostics or {}).get("max_underlying_asset_weight")
        cluster_cap = (group.diagnostics or {}).get("max_factor_cluster_weight")
        exposure_guard = assess_exposure_guard(
            "US",
            assets_after_brake,
            asset_cap=float(asset_cap) if asset_cap is not None else None,
            cluster_cap=float(cluster_cap) if cluster_cap is not None else None,
        )
        if exposure_guard.scale < 1.0:
            guarded_weights = self._scale_risky(guarded_weights, exposure_guard.scale)

        target_assets = self._asset_targets(panel, visible_i, guarded_weights)
        target_risk_weight = sum(float(v) for v in target_assets.values())
        final_clusters = cluster_weights("US", target_assets)
        state_map = {state.strategy_id: state for state in states}

        result["route_version"] = self.version
        result["guard_version"] = self.guard_version
        result["target_strategy_weights"] = guarded_weights
        result["target_asset_weights"] = target_assets
        result["cash_residual_weight"] = max(0.0, 1.0 - target_risk_weight)
        result["selected_strategy_ids"] = [
            sid for sid, weight in guarded_weights.items()
            if sid != "P28_CASH" and float(weight) > 1e-12
        ]
        result["selected_strategy_count"] = len(result["selected_strategy_ids"])
        result["projected_annualized_expected_net_return"] = self._weighted_expected(
            guarded_weights,
            state_map,
        )
        result["capital_capacity"] = self._rebuild_capacity(panel, input_phase, target_assets)
        result["execution_target_source"] = "FULL_FROZEN_STRATEGY_MIX_WITH_FAST_BRAKE_AND_EXPOSURE_GUARD"
        result["state_break_guard"] = {
            **state_break,
            "applied_to_primary_route": brake_factor < 1.0,
            "risk_scale": brake_factor,
            "authority": "RISK_REDUCTION_ONLY",
        }
        result["underlying_exposure_guard"] = {
            **exposure_guard.to_dict(),
            "applied": exposure_guard.scale < 1.0,
            "final_asset_weights": target_assets,
            "final_cluster_weights": final_clusters,
            "final_risk_weight": target_risk_weight,
        }
        result["selection_metric_semantics"] = (
            str(result.get("selection_metric_semantics") or "")
            + " Long-horizon return ranking still owns direction. After ranking, fast state-break "
              "evidence may scale risk downward only, and underlying asset/factor concentration "
              "may further scale the whole risky mix without changing relative strategy ranking."
        )
        return result
