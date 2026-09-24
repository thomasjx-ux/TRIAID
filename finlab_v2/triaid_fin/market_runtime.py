from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

from .market_data import session_phase
from .market_registry import MARKET_REGISTRY, market_ids, normalize_market_id
from .frequency_policy import FrequencyPolicy
from .trading_calendar import calendar_status


class MarketDataAutomation:
    version="market-data-automation@0.8.0"

    def __init__(self,engine,decision_scheduler=None)->None:
        self.engine=engine
        self.decision_scheduler=decision_scheduler
        self.enabled=os.getenv("TRIAID_DATA_AUTOMATION","1").lower() not in {"0","false","off","no"}
        self.last_refresh:dict[str,float]={}
        self.errors:dict[str,str]={}
        self.last_phase:dict[str,str]={}
        self.frequency_policy=FrequencyPolicy(engine.store)
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

    async def _run_market_cycle(self,market_id:str,now:float)->None:
        phase=session_phase(market_id)
        if self.last_phase.get(market_id)!=phase:
            for key in [k for k in self.last_refresh if k.startswith(f"{market_id}:")]:
                self.last_refresh[key]=0.0
            self.last_phase[market_id]=phase
            print("TRIAID_MARKET_PHASE",market_id,phase)
        capabilities=self.engine.market_data_capabilities(market_id)[market_id]
        if market_id=="CN" and phase=="PREOPEN":
            local_now=datetime.now(ZoneInfo("Asia/Shanghai"))
            day=local_now.date().isoformat()
            if local_now.time()>=dt_time(9,25) and self.auction_shadow_day.get("CN")!=day:
                try:
                    probe=await asyncio.to_thread(self.engine.market_data_auction_shadow_probe,"CN")
                    event={
                        **probe,
                        "observed_at":datetime.now(timezone.utc).isoformat(),
                        "trade_date":day,
                    }
                    self.engine.store.append_jsonl("auction_shadow_events.jsonl",event)
                    self.auction_shadow_latest["CN"]=event
                    self.auction_shadow_day["CN"]=day
                    print("TRIAID_ZERO_COST_AUCTION_SHADOW",probe.get("available_symbols"),probe.get("total_symbols"),probe.get("all_symbols_available"))
                except Exception as exc:
                    self.errors["CN:AUCTION_SHADOW"]=f"{type(exc).__name__}:{exc}"
        for mode,interval_seconds in self.refresh_plan_for_phase(market_id,phase).items():
            if not capabilities.get(mode,{}).get("supported",False):
                continue
            key=f"{market_id}:{mode}"
            if now-self.last_refresh.get(key,0.0)<interval_seconds:
                continue
            try:
                result=await asyncio.wait_for(
                    asyncio.to_thread(self.engine.refresh_market_data,market_id,mode),
                    timeout=self.refresh_timeout_seconds,
                )
                snapshot=await asyncio.wait_for(
                    asyncio.to_thread(self.engine.market_data_snapshot,market_id,mode,False),
                    timeout=self.refresh_timeout_seconds,
                )
                observed=await asyncio.wait_for(
                    asyncio.to_thread(self.engine.record_market_observation,snapshot),
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
                                self.engine.refresh_volatility_forecast,
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

        if market_id=="US" and phase=="POSTCLOSE":
            local_now=datetime.now(ZoneInfo("America/New_York"))
            day=local_now.date().isoformat()
            if self.long_cycle_day!=day:
                try:
                    report=await asyncio.wait_for(
                        asyncio.to_thread(self.engine.long_cycle_hypothesis_run,False),
                        timeout=max(120,self.refresh_timeout_seconds),
                    )
                    self.long_cycle_latest={
                        "experiment_id":report.get("experiment_id"),
                        "as_of":report.get("as_of"),
                        "downturn_state":((report.get("hypotheses") or {}).get("downturn_confirmation") or {}).get("state"),
                        "stretch_state":((report.get("hypotheses") or {}).get("stretch_vulnerability") or {}).get("state"),
                    }
                    self.long_cycle_day=day
                    self.errors.pop("US:LONG_CYCLE",None)
                    print(
                        "TRIAID_LONG_CYCLE_DAILY",
                        report.get("experiment_id"),
                        report.get("as_of"),
                        self.long_cycle_latest.get("downturn_state"),
                        self.long_cycle_latest.get("stretch_state"),
                    )
                except Exception as exc:
                    self.errors["US:LONG_CYCLE"]=f"{type(exc).__name__}:{exc}"
                    print("TRIAID_LONG_CYCLE_RECOVERY",self.errors["US:LONG_CYCLE"])

            if self.cross_market_crash_day!=day:
                try:
                    report=await asyncio.wait_for(
                        asyncio.to_thread(self.engine.cross_market_crash_run,False),
                        timeout=max(180,self.refresh_timeout_seconds),
                    )
                    episodes=report.get("canonical_episode_studies") or {}
                    self.cross_market_crash_latest={
                        "experiment_id":report.get("experiment_id"),
                        "as_of":report.get("as_of"),
                        "paired_event_rows":((report.get("detected_crashes") or {}).get("paired_event_rows")),
                        "episodes":{
                            key:{
                                "relation":value.get("relation"),
                                "cn_trough_minus_us_trough_calendar_days":value.get("cn_trough_minus_us_trough_calendar_days"),
                            }
                            for key,value in episodes.items()
                        },
                    }
                    self.cross_market_crash_day=day
                    self.errors.pop("NMARKET:CRASH_LINKAGE",None)
                    print(
                        "TRIAID_N_MARKET_CRASH_LINKAGE_DAILY",
                        report.get("experiment_id"),
                        report.get("as_of"),
                        self.cross_market_crash_latest.get("paired_event_rows"),
                    )
                except Exception as exc:
                    self.errors["NMARKET:CRASH_LINKAGE"]=f"{type(exc).__name__}:{exc}"
                    print("TRIAID_N_MARKET_CRASH_LINKAGE_RECOVERY",self.errors["NMARKET:CRASH_LINKAGE"])

            if self.hazard_research_day!=day:
                try:
                    latent=await asyncio.wait_for(
                        asyncio.to_thread(self.engine.latent_hazard_run,False),
                        timeout=max(240,self.refresh_timeout_seconds),
                    )
                    policy=None
                    try:
                        policy=await asyncio.wait_for(
                            asyncio.to_thread(self.engine.policy_curve_run,False),
                            timeout=max(180,self.refresh_timeout_seconds),
                        )
                    except Exception as curve_exc:
                        self.errors["US:POLICY_CURVE"]=f"{type(curve_exc).__name__}:{curve_exc}"
                        print("TRIAID_POLICY_CURVE_RECOVERY",self.errors["US:POLICY_CURVE"])
                    frozen=await asyncio.wait_for(
                        asyncio.to_thread(self.engine.hazard_prospective_freeze,latent,policy),
                        timeout=max(120,self.refresh_timeout_seconds),
                    )
                    resolved=await asyncio.wait_for(
                        asyncio.to_thread(self.engine.hazard_prospective_resolve),
                        timeout=max(240,self.refresh_timeout_seconds),
                    )
                    risk_warning=await asyncio.wait_for(
                        asyncio.to_thread(self.engine.risk_warning_run,True),
                        timeout=max(120,self.refresh_timeout_seconds),
                    )
                    risk_control=await asyncio.wait_for(
                        asyncio.to_thread(self.engine.risk_control_run,True),
                        timeout=max(120,self.refresh_timeout_seconds),
                    )
                    self.hazard_research_latest={
                        "experiment_id":latent.get("experiment_id"),
                        "as_of":latent.get("as_of"),
                        "current_state":(latent.get("current_state") or {}).get("state_label"),
                        "supported_trigger_count":(latent.get("current_state") or {}).get("supported_trigger_count"),
                        "policy_curve_snapshot_id":(policy or {}).get("snapshot_id"),
                        "policy_curve_usable":((policy or {}).get("data_quality") or {}).get("term_curve_usable"),
                        "prospective_ledger_id":frozen.get("ledger_id"),
                        "updated_outcomes":resolved.get("updated_outcomes"),
                        "risk_warning_id":risk_warning.get("warning_id"),
                        "risk_pressure_index":(risk_warning.get("overall") or {}).get("risk_pressure_index"),
                        "risk_band":(risk_warning.get("overall") or {}).get("risk_band"),
                        "risk_20d":((risk_warning.get("horizon_estimates") or {}).get("20") or {}).get("risk_pressure_index"),
                        "risk_60d":((risk_warning.get("horizon_estimates") or {}).get("60") or {}).get("risk_pressure_index"),
                        "risk_120d":((risk_warning.get("horizon_estimates") or {}).get("120") or {}).get("risk_pressure_index"),
                        "risk_250d":((risk_warning.get("horizon_estimates") or {}).get("250") or {}).get("risk_pressure_index"),
                        "risk_control_experiment_id":risk_control.get("experiment_id"),
                        "risk_control_stage":(risk_control.get("risk_control_experiment") or {}).get("stage"),
                    }
                    self.hazard_research_day=day
                    self.errors.pop("US:LATENT_HAZARD",None)
                    self.errors.pop("US:HAZARD_PROSPECTIVE",None)
                    print(
                        "TRIAID_HAZARD_RESEARCH_DAILY",
                        latent.get("experiment_id"),
                        latent.get("as_of"),
                        self.hazard_research_latest.get("current_state"),
                        self.hazard_research_latest.get("policy_curve_usable"),
                        self.hazard_research_latest.get("updated_outcomes"),
                        self.hazard_research_latest.get("risk_pressure_index"),
                        self.hazard_research_latest.get("risk_band"),
                        self.hazard_research_latest.get("risk_control_stage"),
                    )
                except Exception as exc:
                    self.errors["US:HAZARD_PROSPECTIVE"]=f"{type(exc).__name__}:{exc}"
                    print("TRIAID_HAZARD_PROSPECTIVE_RECOVERY",self.errors["US:HAZARD_PROSPECTIVE"])

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
        labels={
            "US":{
                "SPY":"S&P 500 · SPY",
                "QQQ":"Nasdaq 100 · QQQ",
                "IWM":"Russell 2000 · IWM",
                "TLT":"US Treasury · TLT",
                "GLD":"Gold · GLD",
            },
            "CN":{
                "510300.SS":"沪深300ETF · 510300",
                "510500.SS":"中证500ETF · 510500",
                "159915.SZ":"创业板ETF · 159915",
                "512100.SS":"中证1000ETF · 512100",
                "511010.SS":"国债ETF · 511010",
            },
            "HK":{
                "2800.HK":"盈富基金 · 2800.HK",
                "2828.HK":"恒生国企ETF · 2828.HK",
                "3033.HK":"恒生科技ETF · 3033.HK",
                "2819.HK":"香港债券ETF · 2819.HK",
            },
        }
        return labels.get(market_id.upper(),{}).get(symbol,symbol)

    def live_indicators(self,market_id:str)->dict:
        market=market_id.upper()
        rows=self.engine.market_observations(market,"REALTIME",120)
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
                "session_phase":session_phase(market),
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
            "session_phase":latest.get("session_phase") or session_phase(market),
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
        for row in self.engine.market_observations(market,None,max(80,limit)):
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
        for row in self.engine.market_transitions(market,None,max(80,limit)):
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
            "hub":self.engine.market_data_status(),
        }
