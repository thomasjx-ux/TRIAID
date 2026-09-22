from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from .contracts import RunRecord
from .capital_capacity import CAPITAL_SLEEVES_CNY
from .us_return_max import USD_CAPITAL_SLEEVES


class ReviewModule:
    version="review@0.7.0"

    @staticmethod
    def _evidence_eligible(run:RunRecord)->bool:
        metadata=run.market.metadata or {}
        return (
            metadata.get("evidence_eligible") is not False
            and str(metadata.get("run_scope") or "OFFICIAL_EVIDENCE")!="MANUAL_PREVIEW"
            and run.status!="PREVIEW_READY"
        )

    @staticmethod
    def _primary_mode(market_id:str)->str:
        market_id=str(market_id).upper()
        return "CN_RETURN_MAX_CAPACITY" if market_id=="CN" else "US_RETURN_MAX_CAPACITY" if market_id=="US" else ""

    @classmethod
    def _primary_route(cls,run:RunRecord)->bool:
        expected=cls._primary_mode(run.market.market_id)
        metadata=run.market.metadata or {}
        return bool(
            expected
            and str(metadata.get("experiment_mode") or "").upper()==expected
            and metadata.get("primary_reference_superseded") is not True
            and run.status!="SUPERSEDED"
        )

    @staticmethod
    def _state_map(run:RunRecord)->Dict[str,object]:
        return {s.strategy_id:s for s in run.strategy_states}

    @staticmethod
    def _group_weights(run:RunRecord)->Dict[str,float]:
        return dict(run.strategy_group.weights) if run.strategy_group else {}

    @staticmethod
    def _decision_after(run:RunRecord)->Dict[str,float]:
        if run.triaid_decision:
            return dict(run.triaid_decision.weights_after)
        return ReviewModule._group_weights(run)

    @staticmethod
    def _reason(run:RunRecord,strategy_id:str)->Optional[dict]:
        if run.triaid_decision and strategy_id in run.triaid_decision.reasons:
            return run.triaid_decision.reasons[strategy_id].model_dump()
        if run.strategy_group and strategy_id in run.strategy_group.reasons:
            return run.strategy_group.reasons[strategy_id].model_dump()
        state=ReviewModule._state_map(run).get(strategy_id)
        if state and state.selection_reason:
            return state.selection_reason.model_dump()
        return None

    @staticmethod
    def _weighted_state_estimate(run:RunRecord,weights:Dict[str,float])->float:
        states=ReviewModule._state_map(run)
        return sum(
            float(weight)*float(states[strategy_id].expected_net_return)
            for strategy_id,weight in weights.items()
            if strategy_id in states
        )

    @classmethod
    def _return_comparison(cls,run:RunRecord)->dict:
        group_weights=cls._group_weights(run)
        triaid_weights=cls._decision_after(run)
        baseline_state_estimate=cls._weighted_state_estimate(run,group_weights)
        triaid_state_estimate=cls._weighted_state_estimate(run,triaid_weights)
        evaluation=run.evaluation
        payload={
            "run_id":run.run_id,
            "market_id":run.market.market_id,
            "as_of":run.market.as_of,
            "outcome_status":evaluation.status if evaluation else "PENDING_OUTCOME",
            "baseline_state_return_estimate":baseline_state_estimate,
            "triaid_state_return_estimate":triaid_state_estimate,
            "state_return_estimate_delta":triaid_state_estimate-baseline_state_estimate,
            "state_estimate_semantics":"Historical/model state-return estimate used for comparison; not a calibrated future-return forecast.",
            "baseline_realized_return":None,
            "triaid_realized_return":None,
            "realized_excess_return":None,
            "trading_cost":None,
            "strategy_realized_returns":{},
            "contribution_deltas":{},
        }
        if evaluation and evaluation.status=="EVALUATED":
            ids=set(evaluation.baseline_contributions)|set(evaluation.triaid_contributions)
            contribution_deltas={
                sid:float(evaluation.triaid_contributions.get(sid,0.0))
                -float(evaluation.baseline_contributions.get(sid,0.0))
                for sid in ids
            }
            payload.update({
                "baseline_realized_return":float(evaluation.baseline_return or 0.0),
                "triaid_realized_return":float(evaluation.triaid_return or 0.0),
                "realized_excess_return":float(evaluation.excess_return or 0.0),
                "trading_cost":float(evaluation.trading_cost or 0.0),
                "strategy_realized_returns":dict(evaluation.strategy_realized_returns),
                "contribution_deltas":dict(sorted(
                    contribution_deltas.items(),
                    key=lambda item:abs(item[1]),
                    reverse=True,
                )),
            })
        return payload

    @classmethod
    def _strategy_change(cls,current:RunRecord,previous:Optional[RunRecord])->dict:
        current_weights=cls._group_weights(current)
        previous_weights=cls._group_weights(previous) if previous else {}
        ids=sorted(set(current_weights)|set(previous_weights))
        changes=[]
        for strategy_id in ids:
            before=float(previous_weights.get(strategy_id,0.0))
            after=float(current_weights.get(strategy_id,0.0))
            delta=after-before
            if before<=1e-12 and after>1e-12:
                change_type="ADDED"
            elif before>1e-12 and after<=1e-12:
                change_type="REMOVED"
            elif delta>1e-12:
                change_type="INCREASED"
            elif delta<-1e-12:
                change_type="DECREASED"
            else:
                change_type="UNCHANGED"
            reason_source=current if after>1e-12 else previous
            changes.append({
                "strategy_id":strategy_id,
                "change_type":change_type,
                "weight_before":before,
                "weight_after":after,
                "weight_delta":delta,
                "reason":cls._reason(reason_source,strategy_id) if reason_source else None,
            })
        top_movements=sorted(
            [row for row in changes if row["change_type"]!="UNCHANGED"],
            key=lambda row:abs(row["weight_delta"]),
            reverse=True,
        )

        current_states=cls._state_map(current)
        members=set(current.strategy_group.members if current.strategy_group else [])
        excluded=[]
        for strategy_id,state in current_states.items():
            if strategy_id in members:
                continue
            excluded.append({
                "strategy_id":strategy_id,
                "expected_net_return":float(state.expected_net_return),
                "eligible":bool(state.eligible),
                "lifecycle":state.lifecycle,
                "estimated_cost":float(state.estimated_cost),
                "liquidity_ok":bool(state.liquidity_ok),
                "capacity_ok":bool(state.capacity_ok),
                "risk_ok":bool(state.risk_ok),
                "concentration_ok":bool(state.concentration_ok),
                "hard_failure":bool(state.hard_failure),
                "reason":state.selection_reason.model_dump() if state.selection_reason else None,
            })
        excluded.sort(key=lambda row:row["expected_net_return"],reverse=True)

        overlay_before=dict(current.triaid_decision.weights_before) if current.triaid_decision else current_weights
        overlay_after=dict(current.triaid_decision.weights_after) if current.triaid_decision else current_weights
        overlay_ids=set(overlay_before)|set(overlay_after)
        overlay_changes={
            sid:float(overlay_after.get(sid,0.0))-float(overlay_before.get(sid,0.0))
            for sid in overlay_ids
        }
        overlay_l1=sum(abs(value) for value in overlay_changes.values())

        previous_estimate=(
            cls._weighted_state_estimate(previous,previous_weights)
            if previous and previous.strategy_group else None
        )
        current_estimate=cls._weighted_state_estimate(current,current_weights)

        return {
            "market_id":current.market.market_id,
            "current_run_id":current.run_id,
            "previous_run_id":previous.run_id if previous else None,
            "as_of":current.market.as_of,
            "member_count_before":len(previous.strategy_group.members) if previous and previous.strategy_group else 0,
            "member_count_after":len(current.strategy_group.members) if current.strategy_group else 0,
            "added":[row["strategy_id"] for row in changes if row["change_type"]=="ADDED"],
            "removed":[row["strategy_id"] for row in changes if row["change_type"]=="REMOVED"],
            "top_weight_movements":top_movements[:10],
            "all_weight_changes":changes,
            "excluded_strategies":excluded,
            "selector_state_return_estimate_before":previous_estimate,
            "selector_state_return_estimate_after":current_estimate,
            "selector_state_return_estimate_delta":(
                current_estimate-previous_estimate if previous_estimate is not None else None
            ),
            "triaid_overlay_l1_change":overlay_l1,
            "triaid_overlay_changed":overlay_l1>1e-12,
            "triaid_overlay_weight_deltas":dict(sorted(
                overlay_changes.items(),
                key=lambda item:abs(item[1]),
                reverse=True,
            )),
            "change_semantics":{
                "selector":"Change in strategy-group membership or weights versus the immediately preceding official primary run.",
                "triaid_overlay":"Additional TRIAID decision-layer change relative to the current selector baseline.",
                "return_estimate":"Historical/model state-return estimate only; realized performance is reported separately.",
            },
        }



    @staticmethod
    def _capital_context(market_id:str)->tuple[str,tuple[float,...]]:
        market=str(market_id).upper()
        if market=="US":
            return "USD",tuple(float(x) for x in USD_CAPITAL_SLEEVES)
        if market=="CN":
            return "CNY",tuple(float(x) for x in CAPITAL_SLEEVES_CNY)
        return "NATIVE",()

    @classmethod
    def _capitalized_report(
        cls,
        current:RunRecord,
        previous:Optional[RunRecord],
        change:dict,
        comparison:dict,
    )->dict:
        currency,sleeves=cls._capital_context(current.market.market_id)
        current_weights=cls._group_weights(current)
        previous_weights=cls._group_weights(previous) if previous else {}
        current_return=comparison.get("current_run") or {}
        latest_realized=comparison.get("latest_evaluated_today") or current_return or {}

        state_before=change.get("selector_state_return_estimate_before")
        state_after=change.get("selector_state_return_estimate_after")
        realized_baseline=latest_realized.get("baseline_realized_return")
        realized_triaid=latest_realized.get("triaid_realized_return")
        realized_excess=latest_realized.get("realized_excess_return")
        trading_cost=latest_realized.get("trading_cost")

        rows=[]
        for capital in sleeves:
            allocation_rows=[]
            ids=sorted(set(previous_weights)|set(current_weights))
            for strategy_id in ids:
                before_w=float(previous_weights.get(strategy_id,0.0))
                after_w=float(current_weights.get(strategy_id,0.0))
                allocation_rows.append({
                    "strategy_id":strategy_id,
                    "weight_before":before_w,
                    "weight_after":after_w,
                    "weight_delta":after_w-before_w,
                    "amount_before":capital*before_w,
                    "amount_after":capital*after_w,
                    "amount_delta":capital*(after_w-before_w),
                })

            row={
                "currency":currency,
                "starting_capital":capital,
                "strategy_allocations":allocation_rows,
                "selector_state_return_estimate_before":state_before,
                "selector_state_return_estimate_after":state_after,
                "selector_state_return_estimate_delta":(
                    state_after-state_before
                    if state_before is not None and state_after is not None
                    else None
                ),
                "selector_state_return_amount_equivalent_before":(
                    capital*state_before if state_before is not None else None
                ),
                "selector_state_return_amount_equivalent_after":(
                    capital*state_after if state_after is not None else None
                ),
                "selector_state_return_amount_equivalent_delta":(
                    capital*(state_after-state_before)
                    if state_before is not None and state_after is not None
                    else None
                ),
                "baseline_realized_return":realized_baseline,
                "triaid_realized_return":realized_triaid,
                "realized_excess_return":realized_excess,
                "baseline_realized_pnl":(
                    capital*realized_baseline if realized_baseline is not None else None
                ),
                "triaid_realized_pnl":(
                    capital*realized_triaid if realized_triaid is not None else None
                ),
                "realized_excess_pnl":(
                    capital*realized_excess if realized_excess is not None else None
                ),
                "trading_cost_rate":trading_cost,
                "trading_cost_amount":(
                    capital*trading_cost if trading_cost is not None else None
                ),
            }
            rows.append(row)

        return {
            "currency":currency,
            "capital_sleeves":[float(x) for x in sleeves],
            "rows":rows,
            "display_requirements":{
                "show_amount":True,
                "show_percent":True,
                "show_absolute_difference":True,
                "show_weight_difference":True,
                "show_realized_pnl_difference":True,
            },
            "semantics":{
                "realized":"Realized return and P&L are based only on evaluated outcomes.",
                "state_amount_equivalent":"Capital multiplied by the historical/model state-return estimate. This is an analytical equivalent, not a calibrated future P&L forecast.",
            },
        }

    @classmethod
    def _investment_strategy_view(cls,current:RunRecord,previous:Optional[RunRecord],change:dict,comparison:dict)->dict:
        weights=cls._group_weights(current)
        states=cls._state_map(current)
        ranked=[
            {
                "strategy_id":sid,
                "weight":float(weight),
                "state_return_estimate":float(states[sid].expected_net_return) if sid in states else None,
                "risk":float(states[sid].risk) if sid in states else None,
                "uncertainty":float(states[sid].uncertainty) if sid in states else None,
                "reason":cls._reason(current,sid),
            }
            for sid,weight in weights.items()
            if float(weight)>1e-12
        ]
        ranked.sort(key=lambda row:row["weight"],reverse=True)
        leaders=ranked[:5]

        realized=comparison.get("latest_evaluated_today") or comparison.get("current_run") or {}
        realized_gap=realized.get("realized_excess_return")
        if realized_gap is None:
            performance_conclusion="REALIZED_OUTCOME_PENDING"
        elif realized_gap>0:
            performance_conclusion="TRIAID_OUTPERFORMED_BASELINE"
        elif realized_gap<0:
            performance_conclusion="TRIAID_UNDERPERFORMED_BASELINE"
        else:
            performance_conclusion="TRIAID_MATCHED_BASELINE"

        state_delta=change.get("selector_state_return_estimate_delta")
        if state_delta is None:
            selector_direction="NO_PRIOR_COMPARISON"
        elif state_delta>0:
            selector_direction="HIGHER_STATE_RETURN_ESTIMATE"
        elif state_delta<0:
            selector_direction="LOWER_STATE_RETURN_ESTIMATE"
        else:
            selector_direction="UNCHANGED_STATE_RETURN_ESTIMATE"

        market_regime=current.market.regime
        outlook={
            "regime":market_regime,
            "base_case":"Maintain the current return-maximizing mix while the leading strategies retain positive net state-return advantage after modeled switching cost.",
            "upside_condition":"Increase exposure only if higher-ranked strategies preserve their advantage across subsequent observations and the improvement remains larger than switching and execution cost.",
            "downside_condition":"Reduce or replace strategies whose net state-return advantage deteriorates, whose hard feasibility constraints fail, or whose realized contribution turns persistently negative.",
            "forecast_discipline":"Forward view is conditional and scenario-based. Historical/model state-return estimates are not calibrated future-return forecasts.",
        }

        review_points=[]
        for row in change.get("top_weight_movements",[])[:5]:
            review_points.append({
                "strategy_id":row["strategy_id"],
                "change_type":row["change_type"],
                "weight_before":row["weight_before"],
                "weight_after":row["weight_after"],
                "weight_delta":row["weight_delta"],
                "reason":row["reason"],
            })

        capitalized=cls._capitalized_report(current,previous,change,comparison)

        return {
            "report_type":"INVESTMENT_STRATEGY_DAILY",
            "market_id":current.market.market_id,
            "as_of":current.market.as_of,
            "strategy_thesis":{
                "objective":"Maximize realizable net return subject to hard feasibility constraints.",
                "current_leaders":leaders,
                "portfolio_shape":"Concentrated or diversified only as justified by net-return ranking and hard constraints; diversification is not an independent objective.",
            },
            "session_review":{
                "strategy_changes":review_points,
                "added":change.get("added",[]),
                "removed":change.get("removed",[]),
                "selector_direction":selector_direction,
                "selector_state_return_estimate_before":change.get("selector_state_return_estimate_before"),
                "selector_state_return_estimate_after":change.get("selector_state_return_estimate_after"),
                "selector_state_return_estimate_delta":state_delta,
            },
            "performance_review":{
                "conclusion":performance_conclusion,
                "baseline_realized_return":realized.get("baseline_realized_return"),
                "triaid_realized_return":realized.get("triaid_realized_return"),
                "realized_excess_return":realized_gap,
                "trading_cost":realized.get("trading_cost"),
                "largest_contribution_differences":list((realized.get("contribution_deltas") or {}).items())[:5],
                "note":"Realized performance is kept separate from model/state estimates.",
                "capitalized":capitalized,
            },
            "forward_view":{
                **outlook,
                "capitalized_state_return_equivalents":capitalized["rows"],
            },
            "replacement_watch":{
                "excluded_highest_state_return_candidates":change.get("excluded_strategies",[])[:5],
                "replace_when":[
                    "A challenger has a higher realizable net-return estimate after switching cost.",
                    "The incumbent loses its net-return advantage across repeated observations.",
                    "Liquidity, capacity, concentration or other hard feasibility constraints fail.",
                    "Prospective realized contribution persistently underperforms the current alternative.",
                ],
            },
            "technical_appendix_policy":{
                "default_visibility":"COLLAPSED",
                "show_when":"Only when data quality, audit, deployment or market-data integrity is abnormal.",
            },
        }

    def daily_summary(self,runs:Iterable[RunRecord])->dict:
        rows=sorted(
            [
                r for r in runs
                if r.market.snapshot_id!="PENDING"
                and self._evidence_eligible(r)
                and self._primary_route(r)
            ],
            key=lambda r:(r.market.as_of,r.created_at,r.run_id),
        )
        dates=sorted({r.market.as_of for r in rows if r.market.as_of})
        target_date=dates[-1] if dates else datetime.now(timezone.utc).date().isoformat()
        day=[r for r in rows if r.market.as_of==target_date]
        evaluated=[
            r for r in day
            if r.evaluation and r.evaluation.status=="EVALUATED"
            and (r.market.metadata or {}).get("daily_bar_complete") is not False
        ]
        excess=sum(float(r.evaluation.excess_return or 0.0) for r in evaluated)

        latest_by_market={}
        for run in day:
            latest_by_market[run.market.market_id]=run

        strategy_changes=[]
        return_comparisons={}
        investment_strategy_reports={}
        for market_id,current in latest_by_market.items():
            prior=[
                run for run in rows
                if run.market.market_id==market_id
                and (run.market.as_of,run.created_at,run.run_id)
                    <(current.market.as_of,current.created_at,current.run_id)
            ]
            previous=prior[-1] if prior else None
            strategy_changes.append(self._strategy_change(current,previous))
            current_return=self._return_comparison(current)
            evaluated_today=[
                run for run in evaluated
                if run.market.market_id==market_id
            ]
            return_comparisons[market_id]={
                "current_run":current_return,
                "latest_evaluated_today":(
                    self._return_comparison(evaluated_today[-1])
                    if evaluated_today else None
                ),
            }
            investment_strategy_reports[market_id]=self._investment_strategy_view(
                current,
                previous,
                strategy_changes[-1],
                return_comparisons[market_id],
            )

        analysis=[]
        analysis_zh=[]
        for r in evaluated:
            x=float(r.evaluation.excess_return or 0.0)
            if x>0:
                analysis.append(f"{r.market.market_id}: TRIAID posterior return exceeded its baseline by {x:+.4%}.")
                analysis_zh.append(f"{r.market.market_id}：TRIAID 实现收益较 baseline 高 {x:+.4%}。")
            elif x<0:
                analysis.append(f"{r.market.market_id}: TRIAID posterior return was below its baseline by {abs(x):.4%}; inspect weight-adjustment attribution.")
                analysis_zh.append(f"{r.market.market_id}：TRIAID 实现收益较 baseline 低 {abs(x):.4%}，需查看权重调整归因。")
            else:
                analysis.append(f"{r.market.market_id}: TRIAID posterior return matched its baseline for this evaluated period.")
                analysis_zh.append(f"{r.market.market_id}：本期 TRIAID 与 baseline 实现收益相同。")

        for change in strategy_changes:
            market_id=change["market_id"]
            movers=change["top_weight_movements"]
            if movers:
                lead=movers[0]
                analysis_zh.append(
                    f"{market_id}：本轮最大策略权重变化为 {lead['strategy_id']} "
                    f"{lead['weight_before']:.2%}→{lead['weight_after']:.2%}，"
                    f"变化 {lead['weight_delta']:+.2%}。"
                )
            if change["added"] or change["removed"]:
                analysis_zh.append(
                    f"{market_id}：策略成员变化，新增 {change['added'] or '无'}，"
                    f"退出 {change['removed'] or '无'}。"
                )
            elif change["previous_run_id"]:
                analysis_zh.append(f"{market_id}：策略成员未变，本轮主要是权重重排。")
            if not change["triaid_overlay_changed"]:
                analysis_zh.append(f"{market_id}：TRIAID 二次干预层本轮未进一步改变 selector 权重。")

        latest=day[-1] if day else None
        return {
            "date":target_date,
            "runs":len(day),
            "evaluated_runs":len(evaluated),
            "cumulative_excess_return_for_day":excess,
            "analysis":analysis,
            "analysis_zh":analysis_zh,
            "latest_market_regime":latest.market.regime if latest else None,
            "report_contract":{
                "report_type":"INVESTMENT_STRATEGY_DAILY",
                "body_priority":["strategy_analysis","session_review","change_reason","return_difference","forward_view"],
                "technical_runtime_report_default":False,
                "amount_percent_and_difference_required":True,
                "capital_sleeves_required":True,
                "strategy_change_reason_required":True,
                "before_after_weight_required":True,
                "selected_and_excluded_reason_required":True,
                "baseline_vs_triaid_return_required":True,
                "realized_vs_state_estimate_separated":True,
                "capital_and_cost_effects_preserved_in_underlying_run":True,
            },
            "strategy_changes":strategy_changes,
            "return_comparisons":return_comparisons,
            "investment_strategy_reports":investment_strategy_reports,
            "runs_detail":[self._detail(r) for r in day],
        }

    def _detail(self,r:RunRecord)->dict:
        states={
            s.strategy_id:{
                "lifecycle":s.lifecycle,
                "expected_net_return":s.expected_net_return,
                "risk":s.risk,
                "uncertainty":s.uncertainty,
                "metrics":s.metrics,
            }
            for s in r.strategy_states
        }
        return {
            "run_id":r.run_id,
            "market_id":r.market.market_id,
            "as_of":r.market.as_of,
            "snapshot_id":r.market.snapshot_id,
            "regime":r.market.regime,
            "status":r.status,
            "experiment_mode":(r.market.metadata or {}).get("experiment_mode"),
            "market_route":(r.market.metadata or {}).get("market_route"),
            "primary_reference_bootstrap":(r.market.metadata or {}).get("primary_reference_bootstrap"),
            "module_manifest":r.module_manifest,
            "strategy_states":states,
            "strategy_group":{
                "members":r.strategy_group.members,
                "weights":r.strategy_group.weights,
                "reasons":{k:v.model_dump() for k,v in r.strategy_group.reasons.items()},
            } if r.strategy_group else None,
            "triaid_decision":{
                "core_version":r.triaid_decision.core_version,
                "weights_before":r.triaid_decision.weights_before,
                "weights_after":r.triaid_decision.weights_after,
                "reasons":{k:v.model_dump() for k,v in r.triaid_decision.reasons.items()},
                "diagnostics":r.triaid_decision.diagnostics,
            } if r.triaid_decision else None,
            "evaluation":r.evaluation.model_dump() if r.evaluation else None,
            "diagnostic_summary":r.diagnostic_summary,
            "audit":r.audit.model_dump() if r.audit else None,
        }

    def curves(self,runs:Iterable[RunRecord])->List[dict]:
        ordered=sorted(
            [
                r for r in runs
                if r.evaluation and r.evaluation.status=="EVALUATED"
                and self._evidence_eligible(r)
                and self._primary_route(r)
                and (r.market.metadata or {}).get("daily_bar_complete") is not False
            ],
            key=lambda r:(r.market.as_of,r.created_at),
        )
        baseline_equity=1.0
        triaid_equity=1.0
        cumulative_excess=0.0
        points=[]
        for run in ordered:
            baseline_return=float(run.evaluation.baseline_return or 0.0)
            triaid_return=float(run.evaluation.triaid_return or 0.0)
            excess=float(run.evaluation.excess_return or 0.0)
            baseline_equity*=1.0+baseline_return
            triaid_equity*=1.0+triaid_return
            cumulative_excess+=excess
            points.append({
                "time":run.market.as_of or run.created_at,
                "run_id":run.run_id,
                "market_id":run.market.market_id,
                "baseline_equity":baseline_equity,
                "triaid_equity":triaid_equity,
                "cumulative_excess_return":cumulative_excess,
                "excess_equity_gap":triaid_equity-baseline_equity,
            })
        return points
