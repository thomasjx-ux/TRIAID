from __future__ import annotations

from statistics import mean, median


class ExecutionCalibration:
    """Read-only calibration diagnostics for modeled cost/capacity assumptions.

    Compares frozen ex-ante execution proxies with future turnover-constrained
    simulated fills. It never mutates cost parameters automatically.
    """

    version="execution-calibration@0.1.0"
    min_decisions_for_recommendation=20

    @staticmethod
    def _ratio(observed:float,expected:float)->float|None:
        if expected<=0:
            return None
        return observed/expected

    @classmethod
    def _summarize(cls,rows:list[dict],market_id:str)->dict:
        usable=[r for r in rows if r.get("cost_ratio") is not None]
        ratios=[float(r["cost_ratio"]) for r in usable]
        fill_ratios=[float(r["fill_ratio"]) for r in rows if r.get("fill_ratio") is not None]
        delayed=[r for r in rows if int(r.get("actual_fill_days") or 0)>int(r.get("frozen_min_execution_days") or 0)]
        if len(usable)<cls.min_decisions_for_recommendation:
            recommendation="HOLD_PARAMETERS_INSUFFICIENT_PROSPECTIVE_EVIDENCE"
        else:
            med=median(ratios)
            if med>1.20:
                recommendation="REVIEW_COST_MODEL_POSSIBLY_OPTIMISTIC"
            elif med<0.80:
                recommendation="REVIEW_COST_MODEL_POSSIBLY_CONSERVATIVE"
            else:
                recommendation="HOLD_PARAMETERS_CALIBRATION_WITHIN_20_PERCENT"
        return {
            "version":cls.version,
            "market_id":market_id,
            "evaluated_sleeves":len(rows),
            "usable_cost_comparisons":len(usable),
            "median_observed_to_frozen_cost_ratio":median(ratios) if ratios else None,
            "mean_observed_to_frozen_cost_ratio":mean(ratios) if ratios else None,
            "mean_fill_ratio":mean(fill_ratios) if fill_ratios else None,
            "delayed_fill_cases":len(delayed),
            "recommendation":recommendation,
            "automatic_parameter_mutation":False,
            "discipline":"CALIBRATION_RECOMMENDATION_ONLY; PARAMETER CHANGES REQUIRE SEPARATE PROSPECTIVE VALIDATION",
            "rows":rows[-100:],
        }

    @classmethod
    def us(cls,ledger)->dict:
        rows=[]
        for decision in ledger.decisions(5000):
            review=ledger.review_decision(decision)
            frozen_by_id={
                str(x.get("sleeve_id")):x
                for x in ((decision.get("capital_capacity") or {}).get("sleeves") or [])
            }
            for realized in ((review.get("capital_sleeves") or {}).get("sleeves") or []):
                sid=str(realized.get("sleeve_id"))
                frozen=frozen_by_id.get(sid) or {}
                capital=float(realized.get("starting_capital_usd") or 0.0)
                expected=float(frozen.get("estimated_entry_cost_usd") or 0.0)
                observed=float(realized.get("total_execution_cost_usd") or 0.0)
                daily=realized.get("daily_path") or []
                actual_days=next(
                    (int(x.get("period_number") or 0) for x in daily if float(x.get("remaining_target_notional_usd") or 0.0)<=1e-6),
                    len(daily) if daily else 0,
                )
                if int(realized.get("observation_days") or 0)<=0:
                    continue
                rows.append({
                    "decision_id":decision.get("decision_id"),
                    "market_as_of":decision.get("market_as_of"),
                    "sleeve_id":sid,
                    "starting_capital":capital,
                    "frozen_estimated_entry_cost":expected,
                    "observed_simulated_execution_cost":observed,
                    "cost_ratio":cls._ratio(observed,expected),
                    "fill_ratio":realized.get("fill_ratio"),
                    "frozen_min_execution_days":frozen.get("minimum_execution_days"),
                    "actual_fill_days":actual_days,
                    "semantics":"OBSERVED_SIMULATED_COST_USES_FUTURE_REPORTED_TURNOVER; NOT_BROKER_FILL",
                })
        return cls._summarize(rows,"US")

    @classmethod
    def cn(cls,ledger)->dict:
        rows=[]
        for decision in ledger.decisions("CN",5000):
            review=ledger.review_decision(decision)
            frozen_by_id={
                str(x.get("sleeve_id")):x
                for x in ((decision.get("capital_capacity") or {}).get("sleeves") or [])
            }
            realized_block=review.get("capital_sleeves") or {}
            for realized in (realized_block.get("sleeves") or []):
                sid=str(realized.get("sleeve_id"))
                frozen=frozen_by_id.get(sid) or {}
                capital=float(realized.get("starting_capital_cny") or 0.0)
                expected=float(frozen.get("estimated_entry_cost_cny") or 0.0)
                observed=float(realized.get("total_execution_cost_cny") or 0.0)
                daily=realized.get("daily_path") or []
                actual_days=next(
                    (int(x.get("period_number") or 0) for x in daily if float(x.get("remaining_target_notional_cny") or 0.0)<=1e-6),
                    len(daily) if daily else 0,
                )
                if int(realized.get("observation_days") or 0)<=0:
                    continue
                rows.append({
                    "decision_id":decision.get("decision_id"),
                    "market_as_of":decision.get("market_as_of"),
                    "sleeve_id":sid,
                    "starting_capital":capital,
                    "frozen_estimated_entry_cost":expected,
                    "observed_simulated_execution_cost":observed,
                    "cost_ratio":cls._ratio(observed,expected),
                    "fill_ratio":realized.get("fill_ratio"),
                    "frozen_min_execution_days":frozen.get("minimum_execution_days"),
                    "actual_fill_days":actual_days,
                    "semantics":"OBSERVED_SIMULATED_COST_USES_FUTURE_REPORTED_TURNOVER; NOT_BROKER_FILL",
                })
        return cls._summarize(rows,"CN")

    @classmethod
    def status(cls,us_ledger,cn_ledger)->dict:
        return {
            "version":cls.version,
            "US":cls.us(us_ledger),
            "CN":cls.cn(cn_ledger),
        }
