from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone

from .market_data import session_phase
from .market_registry import MARKET_REGISTRY, normalize_market_id


VERSION="market-page-projection@1.2.0"
READY="READY"
WAITING="WAITING"
STALE="STALE"
NOT_APPLICABLE="NOT_APPLICABLE"
ERROR="ERROR"
VALID_STATES={READY,WAITING,STALE,NOT_APPLICABLE,ERROR}


def _section(
    state:str,
    data=None,
    *,
    reason:str|None=None,
    source:str|None=None,
    as_of:str|None=None,
    required:bool=False,
)->dict:
    state=str(state or ERROR).upper()
    if state not in VALID_STATES:
        raise ValueError(f"invalid UI projection state: {state}")
    if state!=READY and not reason:
        raise ValueError("non-ready UI projection section must include a reason")
    return {
        "state":state,
        "required":bool(required),
        "reason":reason,
        "source":source,
        "as_of":as_of,
        "data":deepcopy(data) if data is not None else {},
    }


def _error_section(source:str,exc:Exception,*,required:bool=False,data=None)->dict:
    return _section(
        ERROR,
        {} if data is None else data,
        reason=f"{type(exc).__name__}:{exc}",
        source=source,
        required=required,
    )


def _finite(value)->bool:
    return isinstance(value,(int,float)) and math.isfinite(float(value))


def _run_rows(engine,market_id:str,limit:int=100)->list[dict]:
    rows=[r for r in engine.all_runs() if r.market.market_id.upper()==market_id]
    return [
        {
            "run_id":r.run_id,
            "market_id":r.market.market_id,
            "as_of":r.market.as_of,
            "snapshot_id":r.market.snapshot_id,
            "status":r.status,
            "core_version":r.triaid_decision.core_version if r.triaid_decision else None,
            "experiment_mode":r.market.metadata.get("experiment_mode"),
            "run_scope":r.market.metadata.get("run_scope","OFFICIAL_EVIDENCE"),
            "evidence_eligible":r.market.metadata.get("evidence_eligible") is not False,
            "persistent_record":engine._evidence_eligible_run(r),
            "diagnostic_summary":r.diagnostic_summary,
            "evaluation":r.evaluation.model_dump() if r.evaluation else None,
        }
        for r in rows[-limit:]
    ]


def strategy_rows(engine,market_id:str,lang:str="zh",run_id:str|None=None)->list[dict]:
    market=normalize_market_id(market_id)
    if lang not in {"zh","en"}:
        raise ValueError("lang must be zh or en")
    latest_run=None
    if run_id:
        latest_run=engine.get_run(run_id)
        if latest_run.market.market_id.upper()!=market:
            raise ValueError("run_id market does not match market_id")
        if latest_run.strategy_group is None or latest_run.triaid_decision is None:
            raise ValueError("run decision is not ready")
    else:
        latest_run=engine.latest_decision_run(market)

    effective_market=latest_run.market.market_id if latest_run else market
    cards=engine.strategy_population.strategy_cards(lang,effective_market)
    state_map={s.strategy_id:s for s in latest_run.strategy_states} if latest_run else {}
    group=latest_run.strategy_group if latest_run else None
    decision=latest_run.triaid_decision if latest_run else None
    selected=set(group.members) if group else set()

    out=[]
    for card in cards:
        strategy_id=card["strategy_id"]
        state=state_map.get(strategy_id)
        row=dict(card)
        row.update({
            "market_id":latest_run.market.market_id if latest_run else market,
            "as_of":latest_run.market.as_of if latest_run else None,
            "run_id":latest_run.run_id if latest_run else None,
            "run_scope":(
                (latest_run.market.metadata or {}).get("run_scope","OFFICIAL_EVIDENCE")
                if latest_run else None
            ),
            "evidence_eligible":(
                (latest_run.market.metadata or {}).get("evidence_eligible") is not False
                if latest_run else None
            ),
            "lifecycle":state.lifecycle if state else None,
            "expected_net_return":state.expected_net_return if state else None,
            "risk":state.risk if state else None,
            "uncertainty":state.uncertainty if state else None,
            "metrics":state.metrics if state else {},
            "selected":strategy_id in selected,
            "baseline_weight":group.weights.get(strategy_id,0.0) if group else 0.0,
            "triaid_weight":decision.weights_after.get(strategy_id,0.0) if decision else 0.0,
            "selection_reason":(
                getattr(group.reasons[strategy_id],lang)
                if group and strategy_id in group.reasons
                else None
            ),
            "triaid_reason":(
                getattr(decision.reasons[strategy_id],lang)
                if decision and strategy_id in decision.reasons
                else None
            ),
        })
        out.append(row)
    return out


def _strategy_section(engine,market:str,lang:str,run_id:str|None)->dict:
    try:
        rows=strategy_rows(engine,market,lang,run_id)
    except Exception as exc:
        return _error_section(
            "strategy_population+latest_decision_run",
            exc,
            required=True,
            data=[],
        )
    if not rows:
        return _section(
            ERROR,
            [],
            reason="STRATEGY_TABLE_EMPTY",
            source="strategy_population+latest_decision_run",
            required=True,
        )
    malformed=[]
    for row in rows:
        if not isinstance(row,dict) or not row.get("strategy_id"):
            malformed.append(None)
            continue
        if not all(
            _finite(row.get(field))
            for field in ("expected_net_return","risk","baseline_weight","triaid_weight")
        ):
            malformed.append(row.get("strategy_id"))
    if malformed:
        return _section(
            ERROR,
            rows,
            reason="STRATEGY_NUMERIC_FIELDS_INCOMPLETE:"+",".join(
                str(x) for x in malformed[:10]
            ),
            source="strategy_population+latest_decision_run",
            as_of=next((x.get("as_of") for x in rows if x.get("as_of")),None),
            required=True,
        )
    return _section(
        READY,
        rows,
        source="strategy_population+latest_decision_run",
        as_of=next((x.get("as_of") for x in rows if x.get("as_of")),None),
        required=True,
    )


def _route_section(market:str,daily:dict,daily_state:str=READY)->dict:
    if daily_state!=READY:
        return _section(
            ERROR,
            {},
            reason="DEPENDENCY_DAILY_SUMMARY_NOT_READY",
            source="market_route_projection",
            required=True,
        )
    if market=="US":
        report=daily.get("us_return_max")
        return _section(
            READY if report else WAITING,
            report,
            reason=None if report else "NO_FROZEN_US_RETURN_MAX_DECISION",
            source="us_return_max_ledger",
            as_of=((report or {}).get("latest_decision") or {}).get("market_as_of"),
            required=True,
        )
    if market=="HK":
        report=daily.get("hk_return_max")
        return _section(
            READY if report else WAITING,
            report,
            reason=None if report else "NO_FROZEN_HK_RETURN_MAX_DECISION",
            source="hk_return_max_ledger",
            as_of=((report or {}).get("latest_decision") or {}).get("market_as_of"),
            required=True,
        )
    report={
        "recovery_wave":daily.get("recovery_wave"),
        "prospective_experiment":daily.get("prospective_experiment"),
        "prospective_experiment_status":daily.get("prospective_experiment_status"),
    }
    ready=bool(report["recovery_wave"])
    return _section(
        READY if ready else WAITING,
        report,
        reason=None if ready else "NO_CURRENT_CN_RECOVERY_WAVE_DECISION",
        source="cn_return_max_projection",
        as_of=((report["recovery_wave"] or {}).get("latest_decision") or {}).get("market_as_of"),
        required=True,
    )


def _posterior_section(market:str,route:dict,runs_section:dict,curves_section:dict)->dict:
    if route.get("state") not in {READY,WAITING}:
        return _section(
            WAITING,
            {},
            reason="POSTERIOR_WAITING_FOR_ROUTE_DEPENDENCY",
            source="realized_posterior",
        )
    if market in {"US","HK"}:
        report=route.get("data") or {}
        review=report.get("previous_decision_review") or report.get("latest_decision_review") or {}
        days=int(review.get("observation_days") or 0)
        if days>0:
            path=review.get("daily_path") or []
            return _section(
                READY,
                review,
                source=f"{market.lower()}_route_realized_posterior",
                as_of=(path[-1].get("as_of") if path else None),
            )
        return _section(
            WAITING,
            {},
            reason="WAITING_FOR_NEXT_COMPLETE_TRADING_DAY_OUTCOME",
            source=f"{market.lower()}_route_realized_posterior",
        )

    runs=(runs_section.get("data") or []) if runs_section.get("state") in {READY,WAITING} else []
    curves=(curves_section.get("data") or []) if curves_section.get("state") in {READY,WAITING} else []
    evaluated=[
        row for row in runs
        if isinstance(row,dict)
        and isinstance(row.get("evaluation"),dict)
        and row["evaluation"].get("status")=="EVALUATED"
    ]
    if evaluated:
        return _section(
            READY,
            evaluated[-1],
            source="primary_route_run_evaluation",
            as_of=evaluated[-1].get("as_of"),
        )
    if curves:
        return _section(
            READY,
            curves[-1],
            source="posterior_curve",
            as_of=curves[-1].get("as_of"),
        )
    return _section(
        WAITING,
        {},
        reason="WAITING_FOR_FIRST_REALIZED_POSTERIOR",
        source="primary_route_run_evaluation",
    )


def _live_sections(automation,scheduler,market:str)->dict:
    try:
        fallback_phase=str(session_phase(market) or "").upper()
    except Exception:
        fallback_phase=""

    try:
        live=automation.live_indicators(market)
        live_exc=None
    except Exception as exc:
        live={
            "market_id":market,
            "session_phase":fallback_phase,
            "available":False,
            "instruments":[],
        }
        live_exc=exc

    try:
        activity=automation.activity(market,80)
        activity_exc=None
    except Exception as exc:
        activity={
            "market_id":market,
            "session_phase":fallback_phase,
            "refresh_plan":{},
            "schedule_text":"",
            "events":[],
        }
        activity_exc=exc

    phase=str(
        live.get("session_phase")
        or activity.get("session_phase")
        or fallback_phase
        or ""
    ).upper()
    available=bool(live.get("available"))
    freshness=live.get("freshness_seconds")
    stale=bool(
        available
        and phase=="OPEN"
        and _finite(freshness)
        and float(freshness)>180.0
    )
    if live_exc is not None:
        live_section=_error_section(
            "market_data_automation.live_indicators",
            live_exc,
            required=phase=="OPEN",
            data=live,
        )
    elif available and not stale:
        live_section=_section(
            READY,
            live,
            source="market_data_automation.live_indicators",
            as_of=live.get("source_time_utc"),
            required=phase=="OPEN",
        )
    elif stale:
        live_section=_section(
            STALE,
            live,
            reason="OPEN_SESSION_LIVE_DATA_STALE_GT_180S",
            source="market_data_automation.live_indicators",
            as_of=live.get("source_time_utc"),
            required=True,
        )
    elif phase=="OPEN":
        live_section=_section(
            ERROR,
            live,
            reason="OPEN_SESSION_LIVE_DATA_UNAVAILABLE",
            source="market_data_automation.live_indicators",
            required=True,
        )
    else:
        live_section=_section(
            NOT_APPLICABLE,
            live,
            reason="MARKET_NOT_IN_OPEN_SESSION",
            source="market_data_automation.live_indicators",
        )

    if activity_exc is not None:
        activity_section=_error_section(
            "market_data_automation.activity",
            activity_exc,
            data=activity,
        )
    else:
        events=activity.get("events") or []
        if events:
            activity_section=_section(
                READY,
                activity,
                source="market_data_automation.activity",
            )
        elif phase in {"PREOPEN","OPEN","BREAK"}:
            activity_section=_section(
                WAITING,
                activity,
                reason="NO_ACTIVITY_EVENTS_IN_CURRENT_WINDOW",
                source="market_data_automation.activity",
            )
        else:
            activity_section=_section(
                NOT_APPLICABLE,
                activity,
                reason="ACTIVITY_NOT_EXPECTED_OUTSIDE_ACTIVE_SESSION",
                source="market_data_automation.activity",
            )

    try:
        scheduler_status=scheduler.status()
        scheduler_state=((scheduler_status.get("markets") or {}).get(market) or {})
        scheduler_events=scheduler.events(market,120)
        baseline_required=phase in {"PREOPEN","OPEN"}
        baseline_ready=bool(
            not baseline_required
            or (
                scheduler_state.get("baseline_done") is True
                and scheduler_state.get("baseline_fresh") is True
            )
        )
        if baseline_ready:
            scheduler_section=_section(
                READY,
                {
                    "state":scheduler_state,
                    "events":scheduler_events,
                    "version":scheduler_status.get("version"),
                    "enabled":scheduler_status.get("enabled"),
                },
                source="decision_scheduler",
                required=baseline_required,
            )
        else:
            scheduler_section=_section(
                STALE,
                {
                    "state":scheduler_state,
                    "events":scheduler_events,
                    "version":scheduler_status.get("version"),
                    "enabled":scheduler_status.get("enabled"),
                },
                reason="ACTIVE_SESSION_BASELINE_NOT_FRESH",
                source="decision_scheduler",
                required=True,
            )
        session_date=str(scheduler_state.get("session_date") or "")
        session_events=[
            row for row in scheduler_events
            if not session_date or str(row.get("session_date") or "")==session_date
        ]
        decisions=[
            row for row in session_events
            if row.get("event_type")=="TRANSITION_RESEARCH_DECISION"
        ]
        intraday_payload={
            "state":scheduler_state,
            "events":session_events,
            "decision_events":decisions,
            "latest_decision":decisions[-1] if decisions else None,
            "decision_count":int(scheduler_state.get("decision_count") or len(decisions)),
            "allocation_action_count":int(
                scheduler_state.get("allocation_action_count") or 0
            ),
        }
        if decisions:
            intraday_section=_section(
                READY,
                intraday_payload,
                source="decision_scheduler.intraday",
            )
        elif phase=="OPEN":
            intraday_section=_section(
                WAITING,
                intraday_payload,
                reason="OPEN_SESSION_MONITORING_NO_RECOMPUTE_YET",
                source="decision_scheduler.intraday",
            )
        else:
            intraday_section=_section(
                NOT_APPLICABLE,
                intraday_payload,
                reason="INTRADAY_RECOMPUTE_NOT_EXPECTED_OUTSIDE_OPEN",
                source="decision_scheduler.intraday",
            )
    except Exception as exc:
        scheduler_section=_error_section(
            "decision_scheduler",
            exc,
            required=phase in {"PREOPEN","OPEN"},
            data={"state":{},"events":[]},
        )
        intraday_section=_error_section(
            "decision_scheduler.intraday",
            exc,
            data={
                "state":{},
                "events":[],
                "decision_events":[],
                "latest_decision":None,
                "decision_count":0,
                "allocation_action_count":0,
            },
        )

    return {
        "live":live_section,
        "activity":activity_section,
        "scheduler":scheduler_section,
        "intraday":intraday_section,
    }


def _validate_projection(market:str,sections:dict,strict_live:bool=False)->dict:
    errors=[]
    warnings=[]
    unexplained=[]
    for name,section in sections.items():
        state=section.get("state")
        if state!=READY and not section.get("reason"):
            unexplained.append(name)
        if section.get("required") and state!=READY:
            message=f"{name}:{state}:{section.get('reason')}"
            if name in {"live","activity","scheduler"} and not strict_live:
                warnings.append(message)
            else:
                errors.append(message)
        elif state in {WAITING,STALE,ERROR}:
            warnings.append(f"{name}:{state}:{section.get('reason')}")

    strategies=(sections.get("strategies") or {}).get("data") or []
    if (sections.get("strategies") or {}).get("state")==READY:
        malformed_strategy_rows=[]
        for row in strategies:
            if not isinstance(row,dict) or not row.get("strategy_id"):
                malformed_strategy_rows.append(None)
                continue
            for field in ("expected_net_return","risk","baseline_weight","triaid_weight"):
                if not _finite(row.get(field)):
                    malformed_strategy_rows.append(row.get("strategy_id"))
                    break
        if malformed_strategy_rows:
            errors.append("strategies:NUMERIC_FIELDS_INCOMPLETE")

    route=(sections.get("route") or {}).get("data") or {}
    if market=="US" and (sections.get("route") or {}).get("state")==READY:
        latest=route.get("latest_decision") or {}
        if not latest.get("decision_id"):
            errors.append("route:US_DECISION_ID_MISSING")
        if not latest.get("frozen_at"):
            errors.append("route:US_FROZEN_AT_MISSING")

        strategy_weights=latest.get("target_strategy_weights")
        generic_weights=latest.get("generic_core_control_weights")
        if not isinstance(strategy_weights,dict) or not strategy_weights:
            errors.append("route:US_STRATEGY_WEIGHTS_MISSING")
        elif any(not _finite(v) for v in strategy_weights.values()):
            errors.append("route:US_STRATEGY_WEIGHTS_NON_NUMERIC")
        if not isinstance(generic_weights,dict) or not generic_weights:
            errors.append("route:US_GENERIC_CORE_WEIGHTS_MISSING")
        elif any(not _finite(v) for v in generic_weights.values()):
            errors.append("route:US_GENERIC_CORE_WEIGHTS_NON_NUMERIC")

        asset_weights=latest.get("target_asset_weights")
        if not isinstance(asset_weights,dict) or len(asset_weights)<5:
            errors.append("route:US_ASSET_WEIGHTS_INCOMPLETE")
        elif any(not _finite(v) for v in asset_weights.values()):
            errors.append("route:US_ASSET_WEIGHTS_NON_NUMERIC")

        for field in (
            "projected_annualized_expected_net_return",
            "generic_core_projected_annualized_expected_net_return",
            "buy_hold_projected_annualized_expected_net_return",
            "cash_residual_weight",
        ):
            if not _finite(latest.get(field)):
                errors.append(f"route:US_{field.upper()}_MISSING")

        cap=latest.get("capital_capacity") or {}
        for field in ("max_participation_adv","base_cost_bps","impact_coefficient_bps"):
            if not _finite(cap.get(field)):
                errors.append(f"route:US_CAPITAL_{field.upper()}_MISSING")
        sleeves=cap.get("sleeves") or []
        if len(sleeves)!=4:
            errors.append("route:US_FOUR_CAPITAL_SLEEVES_REQUIRED")
        sleeve_fields=(
            "starting_capital_usd",
            "target_invested_notional_usd",
            "max_one_day_participation_adv",
            "minimum_execution_days",
            "estimated_round_trip_cost_proxy_usd",
        )
        for index,sleeve in enumerate(sleeves):
            for field in sleeve_fields:
                if not _finite(sleeve.get(field)):
                    errors.append(f"route:US_SLEEVE_{index}_{field.upper()}_MISSING")

    if market=="HK" and (sections.get("route") or {}).get("state")==READY:
        latest=route.get("latest_decision") or {}
        if not latest.get("decision_id"):
            errors.append("route:HK_DECISION_ID_MISSING")
        if not latest.get("target_strategy_weights"):
            errors.append("route:HK_STRATEGY_WEIGHTS_MISSING")
        asset_weights=latest.get("target_asset_weights")
        if not isinstance(asset_weights,dict) or len(asset_weights)<4:
            errors.append("route:HK_ASSET_WEIGHTS_INCOMPLETE")
        sleeves=((latest.get("capital_capacity") or {}).get("sleeves") or [])
        if len(sleeves)!=4:
            errors.append("route:HK_FOUR_CAPITAL_SLEEVES_REQUIRED")

    live_section=sections.get("live") or {}
    if live_section.get("state")==READY:
        live_data=live_section.get("data") or {}
        instruments=live_data.get("instruments") or []
        expected_assets=set(MARKET_REGISTRY.get(market).assets)
        actual_assets={
            str(row.get("symbol") or "")
            for row in instruments
            if isinstance(row,dict)
        }
        if expected_assets and not expected_assets.issubset(actual_assets):
            message="live:INSTRUMENT_COVERAGE_INCOMPLETE"
            if strict_live:
                errors.append(message)
            else:
                warnings.append(message)
        malformed_live=[
            str(row.get("symbol") or "")
            for row in instruments
            if not isinstance(row,dict)
            or not _finite(row.get("close"))
            or not _finite(row.get("change_pct"))
        ]
        if malformed_live:
            message="live:NUMERIC_FIELDS_INCOMPLETE:"+",".join(malformed_live)
            if strict_live:
                errors.append(message)
            else:
                warnings.append(message)

    posterior=sections.get("posterior") or {}
    if posterior.get("state")==READY and market=="US":
        review=posterior.get("data") or {}
        realized=((review.get("capital_sleeves") or {}).get("sleeves") or [])
        realized_fields=(
            "starting_capital_usd",
            "fill_ratio",
            "current_equity_usd",
            "current_net_pnl_usd",
            "current_net_return",
            "total_execution_cost_usd",
        )
        for index,row in enumerate(realized):
            for field in realized_fields:
                if not _finite(row.get(field)):
                    errors.append(f"posterior:US_SLEEVE_{index}_{field.upper()}_MISSING")
        for index,row in enumerate(review.get("daily_path") or []):
            if not row.get("as_of"):
                errors.append(f"posterior:US_PATH_{index}_AS_OF_MISSING")
            for field in (
                "return_max_cumulative_return",
                "generic_core_cumulative_return",
                "spy_buy_hold_cumulative_return",
            ):
                if not _finite(row.get(field)):
                    errors.append(f"posterior:US_PATH_{index}_{field.upper()}_MISSING")

    if unexplained:
        errors.append(
            "UNEXPLAINED_NON_READY_SECTIONS:"+",".join(sorted(unexplained))
        )

    dedup_errors=list(dict.fromkeys(errors))
    dedup_warnings=list(dict.fromkeys(warnings))
    return {
        "passed":not dedup_errors,
        "status":(
            "BLOCKED"
            if dedup_errors
            else ("DEGRADED" if dedup_warnings else "READY")
        ),
        "errors":dedup_errors,
        "warnings":dedup_warnings,
        "unexplained_non_ready_sections":unexplained,
        "unexplained_empty_count":len(unexplained),
        "frontend_safe":not dedup_errors,
        "rule":"NO_UNEXPLAINED_EMPTY_SURFACES; FRONTEND_CONSUMES_ONE_MARKET_PAGE_CONTRACT; READY_SECTIONS_MUST_SATISFY_NUMERIC_CONTRACTS; ACTIVE_SESSION_LIVE_AND_BASELINE_DATA_ARE_REQUIRED",
    }


class MarketPageProjection:
    version=VERSION

    def __init__(self,engine,automation,scheduler)->None:
        self.engine=engine
        self.automation=automation
        self.scheduler=scheduler

    @staticmethod
    def _contract()->dict:
        return {
            "frontend_must_not_infer_availability":True,
            "blank_without_reason_forbidden":True,
            "intraday_is_not_formal_posterior":True,
            "single_market_page_source_of_truth":True,
            "allowed_states":sorted(VALID_STATES),
        }

    def live(self,market_id:str)->dict:
        market=normalize_market_id(market_id)
        sections=_live_sections(self.automation,self.scheduler,market)
        live_data=(sections.get("live") or {}).get("data") or {}
        phase=str(live_data.get("session_phase") or "").upper()
        integrity=_validate_projection(
            market,
            sections,
            strict_live=phase in {"PREOPEN","OPEN"},
        )
        return {
            "contract_version":self.version,
            "projection_scope":"LIVE",
            "market_id":market,
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "contract":self._contract(),
            "sections":sections,
            "integrity":integrity,
        }

    def full(self,market_id:str,lang:str="zh",run_id:str|None=None)->dict:
        market=normalize_market_id(market_id)

        try:
            daily=self.engine.daily_summary(market,compact=True)
            daily_section=_section(
                READY,
                daily,
                source="engine.daily_summary.compact",
                as_of=daily.get("date") if isinstance(daily,dict) else None,
                required=True,
            )
        except Exception as exc:
            daily={}
            daily_section=_error_section(
                "engine.daily_summary.compact",
                exc,
                required=True,
            )

        strategies_section=_strategy_section(
            self.engine,market,lang,run_id
        )

        try:
            curves=self.engine.curves(market)
            curves_section=_section(
                READY if curves else WAITING,
                curves,
                reason=None if curves else "WAITING_FOR_FIRST_REALIZED_POSTERIOR",
                source="engine.curves",
                as_of=(curves[-1].get("as_of") if curves else None),
            )
        except Exception as exc:
            curves=[]
            curves_section=_error_section("engine.curves",exc,data=[])

        try:
            runs=_run_rows(self.engine,market,100)
            runs_section=_section(
                READY if runs else WAITING,
                runs,
                reason=None if runs else "NO_RUN_HISTORY_YET",
                source="engine.all_runs",
                as_of=(runs[-1].get("as_of") if runs else None),
            )
        except Exception as exc:
            runs=[]
            runs_section=_error_section("engine.all_runs",exc,data=[])

        try:
            evolution=self.engine.evolution_status()
            evolution_section=_section(
                READY,
                evolution,
                source="engine.evolution_status",
            )
        except Exception as exc:
            evolution={}
            evolution_section=_error_section(
                "engine.evolution_status",
                exc,
                data={},
            )

        route=_route_section(market,daily,daily_section.get("state"))
        posterior=_posterior_section(
            market,route,runs_section,curves_section
        )

        preview_data={}
        if run_id:
            try:
                preview_data=self.engine.get_run(run_id).model_dump()
                preview_section=_section(
                    READY,
                    preview_data,
                    source="engine.get_run.preview",
                    as_of=(preview_data.get("market") or {}).get("as_of"),
                )
            except Exception as exc:
                preview_section=_error_section(
                    "engine.get_run.preview",
                    exc,
                    data={},
                )
        else:
            preview_section=_section(
                NOT_APPLICABLE,
                {},
                reason="NO_PREVIEW_REQUESTED",
                source="engine.get_run.preview",
            )

        live_sections=_live_sections(
            self.automation,self.scheduler,market
        )
        sections={
            "daily":daily_section,
            "strategies":strategies_section,
            "curves":curves_section,
            "runs":runs_section,
            "evolution":evolution_section,
            "route":route,
            "posterior":posterior,
            "preview":preview_section,
            **live_sections,
        }

        integrity=_validate_projection(market,sections,strict_live=False)
        spec=MARKET_REGISTRY.get(market)
        return {
            "contract_version":self.version,
            "projection_scope":"FULL",
            "market_id":market,
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "contract":self._contract(),
            "core":{
                "version":self.engine.core.version,
                "architecture_version":self.engine.architecture_version,
            },
            "market":{
                "benchmark":spec.benchmark,
                "currency":spec.currency,
                "timezone":spec.timezone,
                "assets":list(spec.assets),
                "primary_experiment_mode":str(
                    spec.metadata.get("primary_experiment_mode") or ""
                ),
            },
            "sections":sections,
            "integrity":integrity,
        }
