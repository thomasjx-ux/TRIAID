from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone

from .market_registry import MARKET_REGISTRY, normalize_market_id


VERSION="market-page-projection@1.0.0"
READY="READY"
WAITING="WAITING"
STALE="STALE"
NOT_APPLICABLE="NOT_APPLICABLE"
ERROR="ERROR"


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


def _route_section(market:str,daily:dict)->dict:
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


def _posterior_section(market:str,route:dict,runs:list[dict],curves:list[dict])->dict:
    if market in {"US","HK"}:
        report=route.get("data") or {}
        review=report.get("previous_decision_review") or report.get("latest_decision_review") or {}
        days=int(review.get("observation_days") or 0)
        if days>0:
            return _section(
                READY,
                review,
                source=f"{market.lower()}_route_realized_posterior",
                as_of=(review.get("daily_path") or [{}])[-1].get("as_of") if review.get("daily_path") else None,
            )
        return _section(
            WAITING,
            {},
            reason="WAITING_FOR_NEXT_COMPLETE_TRADING_DAY_OUTCOME",
            source=f"{market.lower()}_route_realized_posterior",
        )

    evaluated=[
        row for row in runs
        if isinstance(row.get("evaluation"),dict)
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


def _live_sections(automation,scheduler,market:str)->tuple[dict,dict,dict]:
    live=automation.live_indicators(market)
    activity=automation.activity(market,80)
    scheduler_status=scheduler.status()
    scheduler_state=((scheduler_status.get("markets") or {}).get(market) or {})
    scheduler_events=scheduler.events(market,120)

    phase=str(live.get("session_phase") or activity.get("session_phase") or "").upper()
    available=bool(live.get("available"))
    freshness=live.get("freshness_seconds")
    stale=bool(
        available
        and phase=="OPEN"
        and _finite(freshness)
        and float(freshness)>180.0
    )
    if available and not stale:
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
            required=False,
        )

    activity_section=_section(
        READY,
        activity,
        source="market_data_automation.activity",
        required=phase=="OPEN",
    )
    scheduler_section=_section(
        READY,
        {
            "state":scheduler_state,
            "events":scheduler_events,
            "version":scheduler_status.get("version"),
            "enabled":scheduler_status.get("enabled"),
        },
        source="decision_scheduler",
        required=phase=="OPEN",
    )
    return live_section,activity_section,scheduler_section


def _validate_projection(market:str,sections:dict)->dict:
    errors=[]
    warnings=[]
    unexplained=[]
    for name,section in sections.items():
        if section.get("state")!=READY and not section.get("reason"):
            unexplained.append(name)
        if section.get("required") and section.get("state")!=READY:
            errors.append(f"{name}:{section.get('state')}:{section.get('reason')}")

    strategies=(sections.get("strategies") or {}).get("data") or []
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
    if market=="US" and route:
        latest=route.get("latest_decision") or {}
        if not latest.get("decision_id"):
            errors.append("route:US_DECISION_ID_MISSING")
        if not latest.get("target_strategy_weights"):
            errors.append("route:US_STRATEGY_WEIGHTS_MISSING")
        asset_weights=latest.get("target_asset_weights")
        if not isinstance(asset_weights,dict) or len(asset_weights)<5:
            errors.append("route:US_ASSET_WEIGHTS_INCOMPLETE")
        for field in (
            "projected_annualized_expected_net_return",
            "generic_core_projected_annualized_expected_net_return",
            "buy_hold_projected_annualized_expected_net_return",
        ):
            if not _finite(latest.get(field)):
                errors.append(f"route:US_{field.upper()}_MISSING")
        sleeves=((latest.get("capital_capacity") or {}).get("sleeves") or [])
        if len(sleeves)!=4:
            errors.append("route:US_FOUR_CAPITAL_SLEEVES_REQUIRED")

    posterior=sections.get("posterior") or {}
    if posterior.get("state")==WAITING:
        warnings.append(str(posterior.get("reason")))

    if unexplained:
        errors.append("UNEXPLAINED_NON_READY_SECTIONS:"+",".join(sorted(unexplained)))

    return {
        "passed":not errors,
        "status":"READY" if not errors and not warnings else ("DEGRADED" if not errors else "BLOCKED"),
        "errors":errors,
        "warnings":warnings,
        "unexplained_non_ready_sections":unexplained,
        "rule":"NO_UNEXPLAINED_EMPTY_SURFACES; READY_SECTIONS_MUST_SATISFY_NUMERIC_CONTRACTS; OPEN_SESSION_LIVE_DATA_IS_REQUIRED",
    }


class MarketPageProjection:
    version=VERSION

    def __init__(self,engine,automation,scheduler)->None:
        self.engine=engine
        self.automation=automation
        self.scheduler=scheduler

    def live(self,market_id:str)->dict:
        market=normalize_market_id(market_id)
        live,activity,scheduler=_live_sections(self.automation,self.scheduler,market)
        sections={
            "live":live,
            "activity":activity,
            "scheduler":scheduler,
        }
        integrity=_validate_projection(market,sections)
        return {
            "contract_version":self.version,
            "projection_scope":"LIVE",
            "market_id":market,
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "sections":sections,
            "integrity":integrity,
        }

    def full(self,market_id:str,lang:str="zh",run_id:str|None=None)->dict:
        market=normalize_market_id(market_id)
        daily=self.engine.daily_summary(market,compact=True)
        strategies=strategy_rows(self.engine,market,lang,run_id)
        curves=self.engine.curves(market)
        runs=_run_rows(self.engine,market,100)
        route=_route_section(market,daily)
        posterior=_posterior_section(market,route,runs,curves)
        live,activity,scheduler=_live_sections(self.automation,self.scheduler,market)

        strategy_as_of=next((x.get("as_of") for x in strategies if x.get("as_of")),None)
        sections={
            "daily":_section(
                READY if isinstance(daily,dict) else ERROR,
                daily,
                reason=None if isinstance(daily,dict) else "DAILY_SUMMARY_UNAVAILABLE",
                source="engine.daily_summary.compact",
                as_of=daily.get("date") if isinstance(daily,dict) else None,
                required=True,
            ),
            "strategies":_section(
                READY if strategies else ERROR,
                strategies,
                reason=None if strategies else "STRATEGY_TABLE_EMPTY",
                source="strategy_population+latest_decision_run",
                as_of=strategy_as_of,
                required=True,
            ),
            "curves":_section(
                READY,
                curves,
                source="engine.curves",
                as_of=(curves[-1].get("as_of") if curves else None),
            ),
            "runs":_section(
                READY,
                runs,
                source="engine.all_runs",
                as_of=(runs[-1].get("as_of") if runs else None),
            ),
            "route":route,
            "posterior":posterior,
            "live":live,
            "activity":activity,
            "scheduler":scheduler,
        }
        integrity=_validate_projection(market,sections)
        spec=MARKET_REGISTRY.get(market)
        return {
            "contract_version":self.version,
            "projection_scope":"FULL",
            "market_id":market,
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "core":{
                "version":self.engine.core.version,
                "architecture_version":self.engine.architecture_version,
            },
            "market":{
                "benchmark":spec.benchmark,
                "currency":spec.currency,
                "timezone":spec.timezone,
                "assets":list(spec.assets),
                "primary_experiment_mode":str(spec.metadata.get("primary_experiment_mode") or ""),
            },
            "sections":sections,
            "integrity":integrity,
        }
