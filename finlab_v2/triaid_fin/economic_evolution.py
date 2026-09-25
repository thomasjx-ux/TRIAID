from __future__ import annotations

import math
from typing import Iterable

from .contracts import RunRecord
from .market_registry import MARKET_REGISTRY
from .value_frontier import ValueFrontierAllocator


class EconomicEvolutionModule:
    """Read-only, cost-aligned longitudinal diagnostics on frozen formal decisions.

    Do not optimize or promote a core from these ex-post diagnostics.  Strictly
    exclude preview, non-global, unevaluated, unaudited and provisional runs.
    Report modeled portfolio-level turnover separately from broker execution.
    """

    version = "economic-evolution@1.1.0"
    minimum_decision_grade_sessions = 20

    @staticmethod
    def _eligible(run: RunRecord, market_id: str, primary_mode: str | None) -> bool:
        meta = run.market.metadata or {}
        evaluation = run.evaluation
        if (
            run.market.market_id.upper() != market_id
            or run.account_id != "GLOBAL"
            or run.strategy_pool_id != "GLOBAL"
            or run.status != "VERIFIED"
            or not run.audit
            or not run.audit.passed
            or not evaluation
            or evaluation.status != "EVALUATED"
            or not run.strategy_group
            or not run.triaid_decision
            or meta.get("evidence_eligible") is False
            or meta.get("daily_bar_complete") is False
            or str(meta.get("run_scope") or "OFFICIAL_EVIDENCE") == "MANUAL_PREVIEW"
            or not run.market.as_of
        ):
            return False
        if primary_mode and str(meta.get("experiment_mode") or "").upper() != primary_mode:
            return False
        return True

    @staticmethod
    def _turnover(before: dict[str, float], after: dict[str, float]) -> float:
        keys = set(before) | set(after)
        return sum(abs(float(after.get(k, 0.0)) - float(before.get(k, 0.0))) for k in keys)

    @staticmethod
    def _growth(returns: list[float]) -> float:
        wealth = 1.0
        for value in returns:
            wealth *= 1.0 + value
        return wealth - 1.0

    @staticmethod
    def _max_drawdown(returns: list[float]) -> float:
        wealth = peak = 1.0
        worst = 0.0
        for value in returns:
            wealth *= 1.0 + value
            peak = max(peak, wealth)
            worst = min(worst, wealth / peak - 1.0)
        return worst

    @staticmethod
    def _cap(run: RunRecord) -> float | None:
        group = run.strategy_group
        raw = (group.diagnostics or {}).get("max_strategy_weight_constraint") if group else None
        if raw is None:
            raw = (run.market.metadata or {}).get("account_max_strategy_weight")
        try:
            cap = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
        return cap if cap is not None and math.isfinite(cap) and 0 < cap <= 1 else None

    def report(
        self,
        all_runs: Iterable[RunRecord],
        market_id: str,
        *,
        primary_mode: str | None = None,
    ) -> dict:
        market = str(market_id).upper()
        if market not in MARKET_REGISTRY.ids():
            raise KeyError(f"unsupported_market:{market}")
        target_mode = str(primary_mode).upper() if primary_mode else None
        selected: dict[str, RunRecord] = {}
        for run in sorted(all_runs, key=lambda x: (x.market.as_of, x.created_at, x.run_id)):
            if self._eligible(run, market, target_mode):
                # At most one completed frozen result per decision date.  The
                # latest audited revision wins, never a duplicated compounding day.
                selected[run.market.as_of] = run

        valid: list[tuple[RunRecord, dict[str, float]]] = []
        skipped = []
        for decision_date, run in sorted(selected.items()):
            ev = run.evaluation
            try:
                values = [float(ev.baseline_return), float(ev.triaid_return), float(ev.trading_cost)]
                outcomes = {str(k): float(v) for k, v in ev.strategy_realized_returns.items()}
                if (
                    not all(math.isfinite(v) for v in values)
                    or values[2] < 0
                    or values[0] <= -1
                    or values[1] <= -1
                    or not outcomes
                    or any(not math.isfinite(v) or v <= -1 for v in outcomes.values())
                ):
                    raise ValueError("invalid_or_incomplete_evaluation")
                needed = {
                    sid
                    for sid, weight in {
                        **run.strategy_group.weights,
                        **run.triaid_decision.weights_after,
                    }.items()
                    if sid != "P28_CASH" and float(weight) > 1e-12
                }
                if not needed.issubset(outcomes):
                    raise ValueError("missing_allocated_strategy_outcomes")
                valid.append((run, outcomes))
            except (TypeError, ValueError) as exc:
                skipped.append({"decision_date": decision_date, "reason": str(exc)})

        if not valid:
            return {
                "version": self.version,
                "market_id": market,
                "status": "WAITING_FOR_MATCHED_FORMAL_OUTCOMES",
                "observations": 0,
                "skipped": skipped,
                "objective": "MAXIMIZE_LONG_HORIZON_REALIZABLE_NET_COMPOUND_GROWTH",
                "risk_role": "HARD_FEASIBILITY_CONSTRAINT_AND_GROWTH_PROTECTION",
                "data_policy": "GLOBAL_PRIMARY_AUDITED_FROZEN_OUTCOMES_ONLY",
            }

        all_ids = set.intersection(*(set(outcomes) for _, outcomes in valid))
        # Cash is always an admissible no-return diagnostic alternative; it is
        # not an observed strategy outcome or a calibrated cash yield.
        fixed_candidates = sorted(all_ids)
        fixed_wealth = {
            sid: math.prod(1.0 + outcomes[sid] for _, outcomes in valid)
            for sid in fixed_candidates
        }
        fixed_wealth["CASH_REFERENCE"] = 1.0
        best_fixed_id, best_fixed_wealth = max(
            fixed_wealth.items(), key=lambda pair: (pair[1], pair[0])
        )

        caps = [self._cap(run) for run, _ in valid]
        stable_cap = (
            caps[0]
            if caps[0] is not None
            and all(x is not None and abs(x - caps[0]) < 1e-10 for x in caps)
            else None
        )
        effective_cap = stable_cap
        if effective_cap is not None:
            risk_budgets = [
                float((run.market.metadata or {}).get("account_risk_budget", 1.0))
                for run, _ in valid
            ]
            if all(math.isfinite(x) and 0 < x <= 1 for x in risk_budgets) and max(risk_budgets) - min(risk_budgets) < 1e-10:
                effective_cap = min(effective_cap, risk_budgets[0])
            else:
                effective_cap = None
        capped_single = None
        if effective_cap is not None:
            capped_wealth = {
                sid: math.prod(1.0 + effective_cap * outcomes[sid] for _, outcomes in valid)
                for sid in fixed_candidates
            }
            capped_wealth["CASH_REFERENCE"] = 1.0
            capped_id, capped_end = max(capped_wealth.items(), key=lambda x: (x[1], x[0]))
            capped_single = {
                "strategy_id": capped_id,
                "constant_strategy_weight": effective_cap if capped_id != "CASH_REFERENCE" else 0.0,
                "cash_weight": 1.0 - effective_cap if capped_id != "CASH_REFERENCE" else 1.0,
                "gross_compound_return": capped_end - 1.0,
                "cost_status": "NOT_MODELED_NOT_EQUAL_TO_REALIZABLE_OPTIMUM",
                "is_full_optimal_portfolio": False,
            }

        daily = []
        prev = None
        frontier_prev = None
        baseline_net = []
        triaid_net = []
        frontier_net = []
        baseline_gross = []
        triaid_gross = []
        frontier_gross = []
        oracle_gross = []
        for run, outcomes in valid:
            ev = run.evaluation
            rate = (run.market.metadata or {}).get("base_cost_bps")
            rate_origin = "FROZEN_MARKET_METADATA"
            if rate is None:
                rate = MARKET_REGISTRY.get(market).base_cost_bps
                rate_origin = "CURRENT_MARKET_CONFIG_FALLBACK"
            try:
                bps = float(rate)
                if not math.isfinite(bps) or bps < 0:
                    raise ValueError("invalid_cost_rate")
            except (TypeError, ValueError):
                skipped.append({"decision_date": run.market.as_of, "reason": "invalid_cost_rate"})
                # Refuse to fabricate comparable net returns.
                return {
                    "version": self.version,
                    "market_id": market,
                    "status": "COST_MODEL_UNAVAILABLE",
                    "observations": len(valid),
                    "skipped": skipped,
                    "objective": "MAXIMIZE_LONG_HORIZON_REALIZABLE_NET_COMPOUND_GROWTH",
                }
            base_weights = dict(run.strategy_group.weights)
            tri_weights = dict(run.triaid_decision.weights_after)
            risk_budget=float((run.market.metadata or {}).get("account_risk_budget",1.0) or 1.0)
            position_cap=self._cap(run) or 0.28
            frontier=ValueFrontierAllocator.allocate(
                run.strategy_states,
                risk_budget=risk_budget,
                position_cap=position_cap,
                allow_shadow=False,
            )
            frontier_weights=dict(frontier.weights)
            frontier_needed={
                sid for sid,weight in frontier_weights.items()
                if sid!="P28_CASH" and float(weight)>1e-12
            }
            if not frontier_needed.issubset(outcomes):
                skipped.append({
                    "decision_date":run.market.as_of,
                    "reason":"missing_value_frontier_strategy_outcomes",
                })
                continue
            base_turnover = self._turnover(prev[0], base_weights) if prev else 0.0
            tri_turnover = self._turnover(prev[1], tri_weights) if prev else 0.0
            frontier_turnover = self._turnover(frontier_prev,frontier_weights) if frontier_prev else 0.0
            base_cost = base_turnover * bps / 10000.0
            tri_cost = tri_turnover * bps / 10000.0
            frontier_cost = frontier_turnover * bps / 10000.0
            base_gross = float(ev.baseline_return)
            tri_before_account_cost = float(ev.triaid_return) + float(ev.trading_cost)
            frontier_before_cost=sum(
                float(weight)*(0.0 if sid=="P28_CASH" else float(outcomes[sid]))
                for sid,weight in frontier_weights.items()
            )
            base_after_modeled_cost = base_gross - base_cost
            tri_after_modeled_cost = tri_before_account_cost - tri_cost
            frontier_after_modeled_cost = frontier_before_cost - frontier_cost
            if base_after_modeled_cost <= -1.0 or tri_after_modeled_cost <= -1.0 or frontier_after_modeled_cost <= -1.0:
                return {
                    "version": self.version,
                    "market_id": market,
                    "status": "INVALID_MODELED_NET_RETURN",
                    "observations": len(valid),
                    "skipped": skipped,
                    "objective": "MAXIMIZE_LONG_HORIZON_REALIZABLE_NET_COMPOUND_GROWTH",
                }
            oracle = max(0.0, *outcomes.values())
            daily.append({
                "decision_date": run.market.as_of,
                "outcome_date": (run.diagnostic_summary or {}).get("outcome_as_of"),
                "run_id": run.run_id,
                "baseline_gross_return": base_gross,
                "triaid_reported_return_after_overlay_cost": float(ev.triaid_return),
                "reported_overlay_cost": float(ev.trading_cost),
                "baseline_full_turnover_proxy": base_turnover,
                "triaid_full_turnover_proxy": tri_turnover,
                "value_frontier_full_turnover_proxy":frontier_turnover,
                "baseline_modeled_cost": base_cost,
                "triaid_modeled_cost": tri_cost,
                "value_frontier_modeled_cost":frontier_cost,
                "baseline_net_proxy": base_after_modeled_cost,
                "triaid_net_proxy": tri_after_modeled_cost,
                "value_frontier_net_proxy":frontier_after_modeled_cost,
                "net_proxy_difference": tri_after_modeled_cost - base_after_modeled_cost,
                "value_frontier_minus_triaid_net_proxy":frontier_after_modeled_cost-tri_after_modeled_cost,
                "value_frontier_weights":frontier_weights,
                "value_frontier_ranked_strategy_ids":frontier.ranked_strategy_ids,
                "hindsight_daily_gross_ceiling": oracle,
                "best_daily_observed_strategy_ids": [
                    sid for sid, ret in outcomes.items() if abs(ret - oracle) < 1e-12
                ],
                "cost_rate_origin": rate_origin,
                "initial_positions_cost_unknown": prev is None,
            })
            baseline_net.append(base_after_modeled_cost)
            triaid_net.append(tri_after_modeled_cost)
            frontier_net.append(frontier_after_modeled_cost)
            baseline_gross.append(base_gross)
            triaid_gross.append(tri_before_account_cost)
            frontier_gross.append(frontier_before_cost)
            oracle_gross.append(oracle)
            prev = (base_weights, tri_weights)
            frontier_prev=frontier_weights

        baseline_growth = self._growth(baseline_net)
        triaid_growth = self._growth(triaid_net)
        frontier_growth = self._growth(frontier_net)
        baseline_gross_growth = self._growth(baseline_gross)
        triaid_gross_growth = self._growth(triaid_gross)
        frontier_gross_growth = self._growth(frontier_gross)
        oracle_growth = self._growth(oracle_gross)
        numerator = triaid_gross_growth - baseline_gross_growth
        denominator = oracle_growth - baseline_gross_growth
        opportunity_capture = numerator / denominator if denominator > 1e-8 else None
        base_dd = self._max_drawdown(baseline_net)
        tri_dd = self._max_drawdown(triaid_net)
        frontier_dd = self._max_drawdown(frontier_net)
        return {
            "version": self.version,
            "market_id": market,
            "status": "EVALUABLE_DESCRIPTIVE_EVIDENCE" if len(valid) >= self.minimum_decision_grade_sessions else "EARLY_EVIDENCE_NOT_DECISION_GRADE",
            "objective": "MAXIMIZE_LONG_HORIZON_REALIZABLE_NET_COMPOUND_GROWTH",
            "risk_role": "HARD_FEASIBILITY_CONSTRAINT_AND_GROWTH_PROTECTION",
            "observations": len(valid),
            "first_decision_date": valid[0][0].market.as_of,
            "last_decision_date": valid[-1][0].market.as_of,
            "skipped": skipped,
            "matched_cost_model": {
                "method": "PER_ROUTE_FULL_WEIGHT_TURNOVER_TIMES_FROZEN_BASE_COST_BPS",
                "same_cost_convention_for_both": True,
                "initial_entry_cost_known": False,
                "drift_slippage_capacity_and_internal_strategy_fees_included": False,
                "broker_fills_observed": False,
                "reported_triaid_overlay_cost_replaced_for_cost_aligned_proxy": True,
            },
            "net_compound_growth_proxy": {
                "baseline": baseline_growth,
                "triaid": triaid_growth,
                "value_frontier_candidate":frontier_growth,
                "triaid_minus_baseline": triaid_growth - baseline_growth,
                "value_frontier_minus_triaid":frontier_growth-triaid_growth,
                "value_frontier_minus_baseline":frontier_growth-baseline_growth,
                "baseline_max_drawdown": base_dd,
                "triaid_max_drawdown": tri_dd,
                "value_frontier_max_drawdown":frontier_dd,
                "drawdown_difference": tri_dd - base_dd,
                "value_frontier_drawdown_minus_triaid":frontier_dd-tri_dd,
                "baseline_loss_sessions": sum(x < 0 for x in baseline_net),
                "triaid_loss_sessions": sum(x < 0 for x in triaid_net),
                "negative_baseline_sessions_improved": sum(b < 0 and t > b for b, t in zip(baseline_net, triaid_net)),
                "positive_baseline_sessions_with_return_drag": sum(b > 0 and t < b for b, t in zip(baseline_net, triaid_net)),
                "upside_return_drag_sum": sum(max(0.0, b - t) for b, t in zip(baseline_net, triaid_net) if b > 0),
                "downside_loss_reduction_sum": sum(max(0.0, t - b) for b, t in zip(baseline_net, triaid_net) if b < 0),
            },
            "hindsight_diagnostics_not_decision_time_targets": {
                "best_full_period_fixed_strategy_id": best_fixed_id,
                "best_fixed_strategy_gross_compound_return": best_fixed_wealth - 1.0,
                "strictly_common_observed_strategy_count": len(all_ids),
                "best_capped_fixed_single_plus_cash": capped_single,
                "daily_clairvoyant_gross_compound_ceiling": oracle_growth,
                "gross_opportunity_capture_ratio": opportunity_capture,
                "fixed_strategy_selected_with_future_information": True,
                "daily_clairvoyant_uses_future_information": True,
                "costs_and_feasibility_not_aligned_for_unrestricted_fixed_oracle": True,
            },
            "latest_five_completed_sessions": daily[-5:],
            "next_gate": {
                "minimum_matched_formal_sessions": self.minimum_decision_grade_sessions,
                "additional_requirements": [
                    "PREDECLARED_HARD_FEASIBILITY_CONSTRAINTS",
                    "PROSPECTIVE_SHADOW_HOLDOUT_VS_UNCHANGED_PARENT",
                    "CAPACITY_AND_BROKER_EXECUTION_COST_VERIFICATION",
                    "NO_AUTOMATIC_CORE_PROMOTION_FROM_HINDSIGHT_DIAGNOSTICS",
                ],
                "production_core_changed": False,
                "candidate_allocator":ValueFrontierAllocator.version,
                "candidate_status":"SHADOW_ONLY_PENDING_PROSPECTIVE_VALIDATION",
            },
            "value_frontier_candidate": {
                "version":ValueFrontierAllocator.version,
                "objective":"MAXIMIZE_REALIZABLE_NET_RETURN_SUBJECT_TO_HARD_CONSTRAINTS",
                "uses_frozen_t0_information_only":True,
                "reads_realized_t1_to_choose_weights":False,
                "allocation_rule":"GREEDY_NET_RETURN_RANK_WITH_POSITION_CAP_AND_RISK_BUDGET",
                "gross_compound_growth":frontier_gross_growth,
                "net_compound_growth_proxy":frontier_growth,
                "net_minus_current_triaid":frontier_growth-triaid_growth,
                "max_drawdown":frontier_dd,
                "promotion_policy":"PROSPECTIVE_SHADOW_HOLDOUT_REQUIRED_BEFORE_PRODUCTION",
            },
            "frontier_baseline": {
                "reference": "Cover 1991 Universal Portfolios: best constant rebalanced portfolio (BCRP) is an ex-post growth benchmark.",
                "doi": "10.1111/j.1467-9965.1991.tb00002.x",
                "implemented_scope": "BEST_FIXED_SINGLE_AND_CAPPED_SINGLE_DIAGNOSTICS_NOT_BCRP_OPTIMIZER",
            },
        }
