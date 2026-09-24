from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

from .market_data import session_phase
from .market_registry import market_ids
from .market_interfaces import market_interface
from .runtime_ports import RuntimeServices
from .runtime_jobs import RUNTIME_JOB_REGISTRY, RuntimeJobContext
from .frequency_policy import FrequencyPolicy
from .trading_calendar import calendar_status


class MarketDataAutomation:
    version="market-data-automation@0.8.0"

    def __init__(self,services,decision_scheduler=None)->None:
        self.services=(
            services
            if isinstance(services,RuntimeServices)
            else RuntimeServices(services)
        )
        self.decision_scheduler=decision_scheduler
        self.enabled=os.getenv("TRIAID_DATA_AUTOMATION","1").lower() not in {"0","false","off","no"}
        self.last_refresh:dict[str,float]={}
        self.errors:dict[str,str]={}
        self.last_phase:dict[str,str]={}
        self.frequency_policy=FrequencyPolicy(self.services.journal)
        self.runtime_job_state:dict[str,dict]={}
        # Backward-compatible status mirrors. They are derived from plugin state
        # and are never used to drive runtime control flow.
        self.auction_shadow_day:dict[str,str]={}
        self.auction_shadow_latest:dict[str,dict]={}
        self.long_cycle_day:str|None=None
        self.long_cycle_latest:dict|None=None
        self.cross_market_crash_day:str|None=None
        self.cross_market_crash_latest:dict|None=None
        self.hazard_research_day:str|None=None
        self.hazard_research_latest:dict|None=None
        self.started_at_utc:str|None=None
        self.last_loop_heartbeat_utc:str|None=None
        self.last_market_cycle_utc:dict[str,str]={}
        self.last_success_utc:dict[str,str]={}
        self.consecutive_failures:dict[str,int]={}
        self.supervisor_restarts:int=0
        refresh_timeout_raw=(os.getenv("TRIAID_REFRESH_TIMEOUT_SECONDS") or "30").strip()
        try:
            refresh_timeout_value=int(refresh_timeout_raw)
        except ValueError:
            refresh_timeout_value=30
        self.refresh_timeout_seconds=max(10,refresh_timeout_value)

    def refresh_plan_for_phase(self,market_id:str,phase:str)->dict[str,int]:
        market=market_id.upper()
        phase=phase.upper()
        if phase=="OPEN":
            return {
                "INTRADAY":self.frequency_policy.interval(market,"INTRADAY"),
                "REALTIME":self.frequency_policy.interval(market,"REALTIME"),
            }
        if phase=="PREOPEN":
            return {
                "PREOPEN":self.frequency_policy.interval(market,"PREOPEN"),
                "REALTIME":self.frequency_policy.interval(market,"REALTIME"),
            }
        if phase=="BREAK":
            return {
                "REALTIME":max(300,self.frequency_policy.interval(market,"REALTIME")),
            }
        if phase=="POSTCLOSE":
            return {"DAILY":self.frequency_policy.interval(market,"DAILY")}
        if phase in {"CLOSED","CALENDAR_UNAVAILABLE"}:
            return {}
        return {}

    def refresh_plan(self,market_id:str)->dict[str,int]:
        market=market_id.upper()
        return self.refresh_plan_for_phase(market,session_phase(market))

    def _sync_legacy_job_state(self)->None:
        auction=self.runtime_job_state.get("CN_PREOPEN_AUCTION_SHADOW") or {}
        if auction.get("latest"):
            latest=auction["latest"]
            market=str(latest.get("market_id") or "CN").upper()
            self.auction_shadow_latest[market]=latest
            if auction.get("last_day"):
                self.auction_shadow_day[market]=auction["last_day"]

        long_cycle=self.runtime_job_state.get("LONG_CYCLE_POSTCLOSE") or {}
        self.long_cycle_day=long_cycle.get("last_day")
        self.long_cycle_latest=long_cycle.get("latest")

        crash=self.runtime_job_state.get("CROSS_MARKET_POSTCLOSE") or {}
        self.cross_market_crash_day=crash.get("last_day")
        self.cross_market_crash_latest=crash.get("latest")

        hazard=self.runtime_job_state.get("HAZARD_RESEARCH_POSTCLOSE") or {}
        self.hazard_research_day=hazard.get("last_day")
        self.hazard_research_latest=hazard.get("latest")

    async def _run_registered_jobs(
        self,
        market_id:str,
        phase:str,
        stage:str,
    )->None:
        profile=market_interface(market_id)
        for job_name in profile.runtime_jobs:
            try:
                if RUNTIME_JOB_REGISTRY.stage(job_name)!=stage:
                    continue
                context=RuntimeJobContext(
                    services=self.services,
                    market_id=market_id,
                    phase=phase,
                    timeout_seconds=self.refresh_timeout_seconds,
                    state=self.runtime_job_state,
                    errors=self.errors,
                )
                await RUNTIME_JOB_REGISTRY.run(job_name,context)
            except Exception as exc:
                key=f"{market_id}:{job_name}:PLUGIN"
                self.errors[key]=f"{type(exc).__name__}:{exc}"
                print("TRIAID_RUNTIME_JOB_RECOVERY",market_id,job_name,self.errors[key])
        self._sync_legacy_job_state()

    async def _run_market_cycle(self,market_id:str,now:float)->None:
        phase=session_phase(market_id)
        if self.last_phase.get(market_id)!=phase:
            for key in [k for k in self.last_refresh if k.startswith(f"{market_id}:")]:
                self.last_refresh[key]=0.0
            self.last_phase[market_id]=phase
            print("TRIAID_MARKET_PHASE",market_id,phase)
        capabilities=self.services.market_data.capabilities(market_id)[market_id]
        await self._run_registered_jobs(market_id,phase,"PRE_REFRESH")
        for mode,interval_seconds in self.refresh_plan_for_phase(market_id,phase).items():
            if not capabilities.get(mode,{}).get("supported",False):
                continue
            key=f"{market_id}:{mode}"
            if now-self.last_refresh.get(key,0.0)<interval_seconds:
                continue
            try:
                result=await asyncio.wait_for(
                    asyncio.to_thread(self.services.market_data.refresh,market_id,mode),
                    timeout=self.refresh_timeout_seconds,
                )
                snapshot=await asyncio.wait_for(
                    asyncio.to_thread(self.services.market_data.snapshot,market_id,mode,False),
                    timeout=self.refresh_timeout_seconds,
                )
                observed=await asyncio.wait_for(
                    asyncio.to_thread(self.services.market_data.record_observation,snapshot),
                    timeout=self.refresh_timeout_seconds,
                )
                decision_result=None
                if self.decision_scheduler is not None:
                    decision_result=await asyncio.to_thread(
                        self.decision_scheduler.after_refresh,
                        market_id,
                        mode,
                        snapshot,
                        observed,
                    )
                if observed.get("reason")=="STALE_SOURCE_TIMESTAMP":
                    self.last_refresh[key]=0.0
                else:
                    self.last_refresh[key]=now
                self.errors.pop(key,None)
                self.last_success_utc[key]=datetime.now(timezone.utc).isoformat()
                self.consecutive_failures[key]=0
                print(
                    "TRIAID_MARKET_DATA_AUTO_REFRESH",
                    market_id,mode,
                    result.get("source_latest_ts"),
                    result.get("points"),
                    observed.get("recorded"),
                    observed.get("reason"),
                )
                if mode=="DAILY":
                    try:
                        forecast=await asyncio.wait_for(
                            asyncio.to_thread(
                                self.services.market_data.refresh_volatility_forecast,
                                market_id,
                            ),
                            timeout=max(60,self.refresh_timeout_seconds),
                        )
                        self.errors.pop(f"{market_id}:VOLATILITY_FORECAST",None)
                        print(
                            "TRIAID_VOLATILITY_FORECAST_REFRESH",
                            market_id,
                            forecast.get("as_of_source_ts"),
                            forecast.get("forecast_move_pct"),
                            ((forecast.get("walk_forward") or {}).get("calibration_quality")),
                        )
                    except Exception as forecast_exc:
                        self.errors[f"{market_id}:VOLATILITY_FORECAST"]=f"{type(forecast_exc).__name__}:{forecast_exc}"
                        print(
                            "TRIAID_VOLATILITY_FORECAST_RECOVERY",
                            market_id,
                            self.errors[f"{market_id}:VOLATILITY_FORECAST"],
                        )
                if decision_result is not None:
                    print(
                        "TRIAID_DECISION_AUTOMATION",
                        market_id,mode,
                        decision_result.get("action"),
                    )
            except Exception as exc:
                self.errors[key]=f"{type(exc).__name__}:{exc}"
                self.consecutive_failures[key]=self.consecutive_failures.get(key,0)+1
                # Retry quickly during an active session instead of waiting a full normal interval.
                self.last_refresh[key]=max(0.0,now-min(interval_seconds,30))
                print(
                    "TRIAID_MARKET_DATA_AUTO_RECOVERY",
                    market_id,mode,
                    self.consecutive_failures[key],
                    self.errors[key],
                )

        await self._run_registered_jobs(market_id,phase,"POST_REFRESH")

    async def run(self)->None:
        self.started_at_utc=datetime.now(timezone.utc).isoformat()
        while True:
            now=time.monotonic()
            self.last_loop_heartbeat_utc=datetime.now(timezone.utc).isoformat()
            for market_id in market_ids():
                try:
                    await self._run_market_cycle(market_id,now)
                    self.last_market_cycle_utc[market_id]=datetime.now(timezone.utc).isoformat()
                    self.errors.pop(f"{market_id}:AUTOMATION_LOOP",None)
                except Exception as exc:
                    key=f"{market_id}:AUTOMATION_LOOP"
                    self.errors[key]=f"{type(exc).__name__}:{exc}"
                    self.consecutive_failures[key]=self.consecutive_failures.get(key,0)+1
                    self.last_market_cycle_utc[market_id]=datetime.now(timezone.utc).isoformat()
                    print(
                        "TRIAID_MARKET_AUTOMATION_RECOVERED_EXCEPTION",
                        market_id,
                        self.consecutive_failures[key],
                        self.errors[key],
                    )
                    # Fault isolation: one market or phase failure must never stop the other market
                    # or terminate the long-running automation loop.
                    continue
            await asyncio.sleep(30)

    @staticmethod
    def _instrument_name(market_id:str,symbol:str)->str:
        try:
            return market_interface(market_id).instrument_labels.get(symbol,symbol)
        except KeyError:
            return symbol

    def live_indicators(self,market_id:str)->dict:
        market=market_id.upper()
        current_phase=session_phase(market)
        rows=self.services.market_data.observations(market,"REALTIME",120)
        valid=[]
        for row in rows:
            try:
                ts=int(row.get("source_latest_ts"))
            except Exception:
                continue
            valid.append((ts,row))
        if not valid:
            return {
                "market_id":market,
                "session_phase":current_phase,
                "observation_session_phase":None,
                "available":False,
                "instruments":[],
            }
        latest_ts,latest=max(valid,key=lambda x:x[0])
        provider=latest.get("provider")
        previous_candidates=[
            (ts,row) for ts,row in valid
            if ts<latest_ts and row.get("provider")==provider
        ]
        previous=max(previous_candidates,key=lambda x:x[0])[1] if previous_candidates else None
        latest_values=latest.get("latest") or {}
        previous_values=(previous or {}).get("latest") or {}
        instruments=[]
        for symbol,payload in latest_values.items():
            current=(payload or {}).get("close")
            prior=(previous_values.get(symbol) or {}).get("close")
            try:
                current_f=float(current)
            except (TypeError,ValueError):
                continue
            change=None
            change_pct=None
            try:
                prior_f=float(prior)
                change=current_f-prior_f
                change_pct=(current_f/prior_f-1.0) if prior_f else None
            except (TypeError,ValueError):
                pass
            instruments.append({
                "symbol":symbol,
                "name":self._instrument_name(market,symbol),
                "close":current_f,
                "previous_close":prior,
                "change":change,
                "change_pct":change_pct,
                "volume":(payload or {}).get("volume"),
            })
        return {
            "market_id":market,
            "session_phase":current_phase,
            "observation_session_phase":latest.get("session_phase"),
            "available":True,
            "provider":provider,
            "quality":latest.get("quality"),
            "source_latest_ts":latest_ts,
            "source_time_utc":datetime.fromtimestamp(latest_ts,timezone.utc).isoformat(),
            "freshness_seconds":max(0.0,time.time()-latest_ts),
            "previous_source_latest_ts":previous.get("source_latest_ts") if previous else None,
            "instruments":instruments,
        }

    def activity(self,market_id:str,limit:int=80)->dict:
        market=market_id.upper()
        phase=session_phase(market)
        plan=self.refresh_plan_for_phase(market,phase)
        events=[]
        for row in self.services.market_data.observations(market,None,max(80,limit)):
            mode=str(row.get("mode") or "").upper()
            interval=self.frequency_policy.interval(market,mode) if mode in {"DAILY","INTRADAY","PREOPEN","REALTIME"} else None
            events.append({
                "at":row.get("observed_at"),
                "kind":"DATA_FETCH",
                "mode":mode,
                "interval_seconds":interval,
                "provider":row.get("provider"),
                "source_latest_ts":row.get("source_latest_ts"),
                "message":f"{mode} data <- {row.get('provider')} · points={row.get('points')} · source_ts={row.get('source_latest_ts')}",
            })
        for row in self.services.market_data.transitions(market,None,max(80,limit)):
            events.append({
                "at":row.get("derived_at"),
                "kind":"STATE_TRANSITION",
                "mode":row.get("mode"),
                "provider":row.get("provider"),
                "source_latest_ts":row.get("source_latest_ts"),
                "message":(
                    f"{row.get('mode')} transition · mean={float(row.get('mean_return') or 0.0):+.4%} "
                    f"· adv={row.get('advancers')} dec={row.get('decliners')}"
                ),
            })
        if self.decision_scheduler is not None:
            for row in self.decision_scheduler.events(market,max(80,limit)):
                decision=row.get("decision") or {}
                assessment=row.get("assessment") or {}
                change=decision.get("weight_change_l1_vs_reference")
                parts=[str(row.get("event_type") or "DECISION")]
                if assessment.get("reason"):
                    parts.append(str(assessment.get("reason")))
                if decision.get("transition_regime"):
                    parts.append(str(decision.get("transition_regime")))
                if change is not None:
                    parts.append(f"weight_L1={float(change):.4f}")
                events.append({
                    "at":row.get("created_at"),
                    "kind":"TRIAID_DECISION",
                    "mode":row.get("mode"),
                    "source_latest_ts":row.get("source_latest_ts"),
                    "message":" · ".join(parts),
                    "weights_before":decision.get("weights_before"),
                    "weights_after":decision.get("weights_after"),
                })
        events=[e for e in events if e.get("at")]
        events.sort(key=lambda e:str(e.get("at")))
        scheduler_state=(
            (self.decision_scheduler.status().get("markets") or {}).get(market,{})
            if self.decision_scheduler is not None
            else {}
        )
        return {
            "market_id":market,
            "session_phase":phase,
            "refresh_plan":plan,
            "schedule_text":" | ".join(f"{mode} every {seconds}s" for mode,seconds in plan.items()) or "no scheduled refresh",
            "decision_state":scheduler_state,
            "events":events[-limit:],
        }

    def record_manual_refresh(self,market_id:str,mode:str)->None:
        self.last_refresh[f"{market_id.upper()}:{mode.upper()}"]=time.monotonic()

    def status(self)->dict:
        return {
            "version":self.version,
            "automation_enabled":self.enabled,
            "discipline":"OFFICIAL_TRADING_CALENDAR_GATED_REFRESH; DATA_REFRESH_DOES_NOT_TRIGGER_TRADING",
            "session_phase":{m:session_phase(m) for m in market_ids()},
            "official_trading_calendar":calendar_status(),
            "last_phase":dict(self.last_phase),
            "refresh_plan":{m:self.refresh_plan(m) for m in market_ids()},
            "last_refresh_monotonic":dict(self.last_refresh),
            "automation_errors":dict(self.errors),
            "frequency_policy":self.frequency_policy.status(),
            "decision_scheduler":(
                self.decision_scheduler.status()
                if self.decision_scheduler is not None
                else None
            ),
            "zero_cost_auction_shadow":dict(self.auction_shadow_latest),
            "long_cycle_hypothesis":{"last_day":self.long_cycle_day,"latest":self.long_cycle_latest},
            "cross_market_crash":{"last_day":self.cross_market_crash_day,"latest":self.cross_market_crash_latest},
            "hazard_research":{"last_day":self.hazard_research_day,"latest":self.hazard_research_latest},
            "self_healing":{
                "enabled":True,
                "started_at_utc":self.started_at_utc,
                "last_loop_heartbeat_utc":self.last_loop_heartbeat_utc,
                "last_market_cycle_utc":dict(self.last_market_cycle_utc),
                "last_success_utc":dict(self.last_success_utc),
                "consecutive_failures":dict(self.consecutive_failures),
                "supervisor_restarts":self.supervisor_restarts,
                "refresh_timeout_seconds":self.refresh_timeout_seconds,
                "policy":"MARKET_FAULT_ISOLATION; FAST_RETRY_ON_REFRESH_FAILURE; SUPERVISOR_RESTART_ON_TASK_EXIT",
            },
            "runtime_jobs":{
                "registry_version":RUNTIME_JOB_REGISTRY.version,
                "registered":list(RUNTIME_JOB_REGISTRY.names()),
                "assignments":{
                    market:list(market_interface(market).runtime_jobs)
                    for market in market_ids()
                },
                "state":dict(self.runtime_job_state),
            },
            "hub":self.services.market_data.status(),
        }
