from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from .market_registry import MARKET_REGISTRY, normalize_market_id
from .trading_calendar import official_session_phase, trading_day_info

VERSION="market-page-snapshot@1.0.0"
SECTION_STATES={"READY","PENDING","STALE","ERROR","NOT_APPLICABLE"}


def _section(state:str,reason:str,data=None,**meta)->dict:
    state=str(state or "ERROR").upper()
    if state not in SECTION_STATES:
        state="ERROR"
        reason=f"INVALID_SECTION_STATE:{reason}"
    return {
        "state":state,
        "reason":str(reason or "UNSPECIFIED"),
        "data":data,
        **meta,
    }


def _finite(value)->bool:
    return isinstance(value,(int,float)) and isfinite(float(value))


class MarketPageSnapshot:
    """Single backend-owned UI contract for all market pages.

    The frontend must not infer data availability from missing fields. Every
    independently rendered surface is represented by a section with an
    explicit READY/PENDING/STALE/ERROR/NOT_APPLICABLE state and reason.
    """

    def __init__(self,engine,automation,scheduler)->None:
        self.engine=engine
        self.automation=automation
        self.scheduler=scheduler

    def _run_summary(self,run)->dict:
        return {
            "run_id":run.run_id,
            "market_id":run.market.market_id,
            "as_of":run.market.as_of,
            "snapshot_id":run.market.snapshot_id,
            "status":run.status,
            "core_version":run.triaid_decision.core_version if run.triaid_decision else None,
            "experiment_mode":(run.market.metadata or {}).get("experiment_mode"),
            "run_scope":(run.market.metadata or {}).get("run_scope","OFFICIAL_EVIDENCE"),
            "evidence_eligible":(run.market.metadata or {}).get("evidence_eligible") is not False,
            "persistent_record":self.engine._evidence_eligible_run(run),
            "diagnostic_summary":run.diagnostic_summary,
            "evaluation":run.evaluation.model_dump() if run.evaluation else None,
        }

    def _strategy_rows(self,market:str,lang:str,run_id:str|None)->tuple[list[dict],object|None]:
        latest_run=None
        if run_id:
            latest_run=self.engine.get_run(run_id)
            if latest_run.market.market_id.upper()!=market:
                raise ValueError("run_id market does not match market_id")
        else:
            latest_run=self.engine.latest_decision_run(market)

        cards=self.engine.strategy_population.strategy_cards(lang,market)
        state_map={s.strategy_id:s for s in latest_run.strategy_states} if latest_run else {}
        group=latest_run.strategy_group if latest_run else None
        decision=latest_run.triaid_decision if latest_run else None
        selected=set(group.members) if group else set()
        rows=[]
        for card in cards:
            sid=card["strategy_id"]
            state=state_map.get(sid)
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
                "selected":sid in selected,
                "baseline_weight":group.weights.get(sid,0.0) if group else 0.0,
                "triaid_weight":decision.weights_after.get(sid,0.0) if decision else 0.0,
                "selection_reason":(
                    getattr(group.reasons[sid],lang)
                    if group and sid in group.reasons else None
                ),
                "triaid_reason":(
                    getattr(decision.reasons[sid],lang)
                    if decision and sid in decision.reasons else None
                ),
            })
            rows.append(row)
        return rows,latest_run

    @staticmethod
    def _posterior_state(market:str,daily:dict,runs:list[dict])->tuple[str,str]:
        evaluated=[
            row for row in runs
            if isinstance(row,dict)
            and isinstance(row.get("evaluation"),dict)
            and row["evaluation"].get("status")=="EVALUATED"
        ]
        if market=="US":
            report=(daily or {}).get("us_return_max") or {}
            prior=report.get("previous_decision_review") or {}
            if int(prior.get("observation_days") or 0)>0:
                return "READY","US_PREVIOUS_DECISION_POSTERIOR_AVAILABLE"
        elif market=="HK":
            report=(daily or {}).get("hk_return_max") or {}
            prior=report.get("previous_decision_review") or {}
            if int(prior.get("observation_days") or 0)>0:
                return "READY","HK_PREVIOUS_DECISION_POSTERIOR_AVAILABLE"
        elif market=="CN":
            report=(daily or {}).get("recovery_wave") or {}
            prior=report.get("previous_decision_review") or report.get("previous_review") or {}
            if int(prior.get("observation_days") or 0)>0:
                return "READY","CN_PREVIOUS_DECISION_POSTERIOR_AVAILABLE"
        if evaluated:
            return "READY","EVALUATED_RUN_AVAILABLE"
        return "PENDING","AWAITING_COMPLETE_REALIZED_OUTCOME"

    def _live_bundle(self,market:str,phase:str)->tuple[dict,dict,dict,dict]:
        sections={}
        try:
            live=self.automation.live_indicators(market)
            available=bool(live.get("available"))
            freshness=live.get("freshness_seconds")
            fresh=available and _finite(freshness) and float(freshness)<180.0
            if fresh:
                state,reason="READY","LIVE_DATA_FRESH"
            elif available:
                state,reason="STALE","LIVE_DATA_STALE"
            elif phase=="OPEN":
                state,reason="ERROR","MARKET_OPEN_BUT_LIVE_DATA_UNAVAILABLE"
            else:
                state,reason="PENDING","LIVE_DATA_NOT_REQUIRED_OUTSIDE_OPEN_SESSION"
            sections["live_market"]=_section(
                state,reason,live,
                required_now=phase=="OPEN",
                source_latest_ts=live.get("source_latest_ts"),
                freshness_seconds=freshness,
            )
        except Exception as exc:
            live={"market_id":market,"session_phase":phase,"available":False,"instruments":[]}
            sections["live_market"]=_section(
                "ERROR",f"LIVE_DATA_EXCEPTION:{type(exc).__name__}",live,required_now=phase=="OPEN"
            )

        try:
            activity=self.automation.activity(market,80)
            events=activity.get("events") or []
            if events:
                state,reason="READY","ACTIVITY_EVENTS_AVAILABLE"
            elif phase=="OPEN":
                state,reason="ERROR","MARKET_OPEN_BUT_ACTIVITY_EMPTY"
            else:
                state,reason="PENDING","NO_ACTIVITY_IN_CURRENT_PHASE"
            sections["activity"]=_section(state,reason,activity,required_now=phase=="OPEN")
        except Exception as exc:
            activity={"market_id":market,"session_phase":phase,"events":[]}
            sections["activity"]=_section(
                "ERROR",f"ACTIVITY_EXCEPTION:{type(exc).__name__}",activity,required_now=phase=="OPEN"
            )

        try:
            scheduler_status=self.scheduler.status()
            scheduler_state=(scheduler_status.get("markets") or {}).get(market,{})
            raw_events=self.scheduler.events(market,120)
            session_date=str(scheduler_state.get("session_date") or "")
            session_events=[
                row for row in raw_events
                if not session_date or str(row.get("session_date") or "")==session_date
            ]
            decisions=[
                row for row in session_events
                if row.get("event_type")=="TRANSITION_RESEARCH_DECISION"
            ]
            latest_decision=decisions[-1] if decisions else None
            intraday={
                "scheduler_state":scheduler_state,
                "events":session_events,
                "decision_events":decisions,
                "latest_decision":latest_decision,
                "decision_count":int(scheduler_state.get("decision_count") or len(decisions)),
                "allocation_action_count":int(
                    scheduler_state.get("allocation_action_count") or 0
                ),
            }
            if latest_decision:
                state,reason="READY","INTRADAY_DECISION_AVAILABLE"
            elif phase=="OPEN":
                state,reason="PENDING","OPEN_SESSION_MONITORING_NO_RECOMPUTE_YET"
            else:
                state,reason="PENDING","INTRADAY_DECISION_NOT_EXPECTED_OUTSIDE_OPEN"
            sections["intraday_decision"]=_section(
                state,reason,intraday,required_now=False
            )
        except Exception as exc:
            intraday={
                "scheduler_state":{},
                "events":[],
                "decision_events":[],
                "latest_decision":None,
                "decision_count":0,
                "allocation_action_count":0,
            }
            sections["intraday_decision"]=_section(
                "ERROR",f"INTRADAY_DECISION_EXCEPTION:{type(exc).__name__}",intraday,required_now=False
            )
        return live,activity,intraday,sections

    def build(
        self,
        market_id:str,
        lang:str="zh",
        scope:str="full",
        run_id:str|None=None,
    )->dict:
        market=normalize_market_id(market_id)
        lang="en" if str(lang).lower()=="en" else "zh"
        scope=str(scope or "full").lower()
        if scope not in {"full","live"}:
            raise ValueError("scope must be full or live")

        spec=MARKET_REGISTRY.get(market)
        now_utc=datetime.now(timezone.utc)
        local_now=now_utc.astimezone(ZoneInfo(spec.timezone))
        info=trading_day_info(market,local_now)
        phase=official_session_phase(market,local_now)
        session={
            "market_id":market,
            "timezone":spec.timezone,
            "local_iso":local_now.isoformat(),
            "session_date":local_now.date().isoformat(),
            "session_phase":phase,
            "is_open":phase=="OPEN",
            "calendar_known":bool(info.get("calendar_known")),
            "is_trading_day":bool(info.get("is_trading_day")),
            "benchmark":spec.benchmark,
            "currency":spec.currency,
            "assets":list(spec.assets),
            "primary_experiment_mode":str(spec.metadata.get("primary_experiment_mode") or ""),
        }

        live,activity,intraday,sections=self._live_bundle(market,phase)
        payload={
            "schema_version":VERSION,
            "generated_at_utc":now_utc.isoformat(),
            "market_id":market,
            "scope":scope,
            "contract":{
                "frontend_must_not_infer_availability":True,
                "allowed_section_states":sorted(SECTION_STATES),
                "blank_without_section_reason_forbidden":True,
                "intraday_is_not_formal_posterior":True,
            },
            "session":session,
            "live":live,
            "activity":activity,
            "intraday":intraday,
        }

        if scope=="full":
            try:
                daily=self.engine.daily_summary(market,compact=True)
                sections["daily"]=_section("READY","DAILY_SUMMARY_AVAILABLE",daily)
            except Exception as exc:
                daily={}
                sections["daily"]=_section(
                    "ERROR",f"DAILY_SUMMARY_EXCEPTION:{type(exc).__name__}",daily
                )

            try:
                strategies,latest_run=self._strategy_rows(market,lang,run_id)
                if latest_run is None:
                    sstate,sreason="PENDING","NO_FORMAL_DECISION_AVAILABLE"
                else:
                    malformed=[
                        row.get("strategy_id")
                        for row in strategies
                        if not all(
                            _finite(row.get(field))
                            for field in ("expected_net_return","risk","baseline_weight","triaid_weight")
                        )
                    ]
                    if malformed:
                        sstate,sreason="ERROR","STRATEGY_NUMERIC_CONTRACT_INCOMPLETE"
                    else:
                        sstate,sreason="READY","STRATEGY_SURFACE_COMPLETE"
                sections["strategies"]=_section(
                    sstate,sreason,strategies,
                    row_count=len(strategies),
                    run_id=latest_run.run_id if latest_run else None,
                )
            except Exception as exc:
                strategies=[];latest_run=None
                sections["strategies"]=_section(
                    "ERROR",f"STRATEGY_SURFACE_EXCEPTION:{type(exc).__name__}",strategies
                )

            try:
                curves=self.engine.curves(market)
                cstate,creason=(
                    ("READY","POSTERIOR_CURVE_AVAILABLE")
                    if curves else
                    ("PENDING","AWAITING_FIRST_REALIZED_POSTERIOR")
                )
                sections["curves"]=_section(cstate,creason,curves,row_count=len(curves))
            except Exception as exc:
                curves=[]
                sections["curves"]=_section(
                    "ERROR",f"CURVE_EXCEPTION:{type(exc).__name__}",curves
                )

            try:
                evolution=self.engine.evolution_status()
                sections["evolution"]=_section("READY","EVOLUTION_STATUS_AVAILABLE",evolution)
            except Exception as exc:
                evolution={}
                sections["evolution"]=_section(
                    "ERROR",f"EVOLUTION_EXCEPTION:{type(exc).__name__}",evolution
                )

            try:
                raw_runs=[
                    run for run in self.engine.all_runs()
                    if run.market.market_id.upper()==market
                ][-100:]
                runs=[self._run_summary(run) for run in raw_runs]
                rstate,rreason=(
                    ("READY","RUN_HISTORY_AVAILABLE")
                    if runs else
                    ("PENDING","NO_RUN_HISTORY_YET")
                )
                sections["runs"]=_section(rstate,rreason,runs,row_count=len(runs))
            except Exception as exc:
                runs=[]
                sections["runs"]=_section(
                    "ERROR",f"RUN_HISTORY_EXCEPTION:{type(exc).__name__}",runs
                )

            preview_run=None
            if run_id:
                try:
                    preview_run=self.engine.get_run(run_id).model_dump()
                    sections["preview"]=_section(
                        "READY","REQUESTED_PREVIEW_AVAILABLE",preview_run
                    )
                except Exception as exc:
                    sections["preview"]=_section(
                        "ERROR",f"PREVIEW_EXCEPTION:{type(exc).__name__}",None
                    )
            else:
                sections["preview"]=_section(
                    "NOT_APPLICABLE","NO_PREVIEW_REQUESTED",None
                )

            route=None
            if market=="US":
                route=(daily or {}).get("us_return_max")
            elif market=="HK":
                route=(daily or {}).get("hk_return_max")
            elif market=="CN":
                route={
                    "prospective_experiment":(daily or {}).get("prospective_experiment"),
                    "prospective_experiment_status":(daily or {}).get("prospective_experiment_status"),
                    "recovery_wave":(daily or {}).get("recovery_wave"),
                }
            if route:
                sections["market_route"]=_section(
                    "READY","MARKET_ROUTE_AVAILABLE",route
                )
            else:
                sections["market_route"]=_section(
                    "PENDING","MARKET_ROUTE_NOT_YET_AVAILABLE",route
                )

            pstate,preason=self._posterior_state(market,daily,runs)
            sections["posterior"]=_section(pstate,preason,None)

            payload.update({
                "core":{
                    "version":self.engine.core.version,
                    "architecture_version":self.engine.architecture_version,
                },
                "daily":daily,
                "strategies":strategies,
                "curves":curves,
                "evolution":evolution,
                "runs":runs,
                "preview_run":preview_run,
                "route":route,
            })

        critical_errors=[
            name for name,section in sections.items()
            if section.get("state")=="ERROR"
            and bool(section.get("required_now",name in {"daily","strategies"}))
        ]
        stale_required=[
            name for name,section in sections.items()
            if section.get("state")=="STALE" and section.get("required_now")
        ]
        reasons_missing=[
            name for name,section in sections.items()
            if not str(section.get("reason") or "").strip()
        ]
        page_state=(
            "ERROR" if critical_errors
            else ("STALE" if stale_required else "READY")
        )
        payload["sections"]=sections
        payload["integrity"]={
            "page_state":page_state,
            "critical_errors":critical_errors,
            "stale_required_sections":stale_required,
            "sections_without_reason":reasons_missing,
            "unexplained_empty_count":len(reasons_missing),
            "contract_complete":not reasons_missing,
        }
        return payload
