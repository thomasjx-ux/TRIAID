from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

from .market_data import session_phase
from .frequency_policy import FrequencyPolicy
from .trading_calendar import calendar_status


class MarketDataAutomation:
    version="market-data-automation@0.5.0"

    def __init__(self,engine,decision_scheduler=None)->None:
        self.engine=engine
        self.decision_scheduler=decision_scheduler
        self.enabled=os.getenv("TRIAID_DATA_AUTOMATION","1").lower() not in {"0","false","off","no"}
        self.state_name="market_automation_state.json"
        persisted=engine.store.load_json(
            self.state_name,
            {"last_refresh_epoch":{},"errors":{},"last_phase":{}},
        )
        self.last_refresh:dict[str,float]={}
        self.last_refresh_epoch:dict[str,float]={}
        for key,value in (persisted.get("last_refresh_epoch") or {}).items():
            try:
                self.last_refresh_epoch[str(key)]=float(value)
            except (TypeError,ValueError):
                continue
        self.errors={
            str(key):str(value)
            for key,value in (persisted.get("errors") or {}).items()
        }
        self.last_phase={
            str(key):str(value)
            for key,value in (persisted.get("last_phase") or {}).items()
        }
        self.frequency_policy=FrequencyPolicy(engine.store)

    def _persist_state(self)->None:
        self.engine.store.save_json(
            self.state_name,
            {
                "version":self.version,
                "updated_at":datetime.now(timezone.utc).isoformat(),
                "last_refresh_epoch":dict(self.last_refresh_epoch),
                "errors":dict(self.errors),
                "last_phase":dict(self.last_phase),
            },
        )

    def refresh_plan_for_phase(self,market_id:str,phase:str)->dict[str,int]:
        market=market_id.upper()
        phase=phase.upper()
        if phase=="OPEN":
            return {
                "INTRADAY":self.frequency_policy.interval(market,"INTRADAY"),
                "REALTIME":self.frequency_policy.interval(market,"REALTIME"),
            }
        if phase=="PREOPEN":
            if market=="US":
                return {
                    "PREOPEN":self.frequency_policy.interval(market,"PREOPEN"),
                    "REALTIME":self.frequency_policy.interval(market,"REALTIME"),
                }
            return {
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

    async def tick_once(self,market_ids:tuple[str,...]|None=None)->dict:
        now_monotonic=time.monotonic()
        now_epoch=time.time()
        markets=tuple((m or "").upper() for m in (market_ids or ("US","CN")))
        report={
            "version":self.version,
            "trigger":"EXTERNAL_OR_LOOP_TICK",
            "at":datetime.now(timezone.utc).isoformat(),
            "markets":{},
            "refreshed":[],
            "skipped":[],
            "errors":[],
        }
        state_changed=False
        for market_id in markets:
            if market_id not in {"US","CN"}:
                raise ValueError(f"unsupported market_id: {market_id}")
            phase=session_phase(market_id)
            previous_phase=self.last_phase.get(market_id)
            if previous_phase!=phase:
                if previous_phase is not None:
                    for key in [k for k in self.last_refresh_epoch if k.startswith(f"{market_id}:")]:
                        self.last_refresh_epoch[key]=0.0
                self.last_phase[market_id]=phase
                state_changed=True
                print("TRIAID_MARKET_PHASE",market_id,phase)
            capabilities=self.engine.market_data_capabilities(market_id)[market_id]
            market_report={"phase":phase,"plan":self.refresh_plan_for_phase(market_id,phase),"events":[]}
            report["markets"][market_id]=market_report
            for mode,interval_seconds in market_report["plan"].items():
                if not capabilities.get(mode,{}).get("supported",False):
                    event={"market_id":market_id,"mode":mode,"action":"SKIP","reason":"UNSUPPORTED"}
                    market_report["events"].append(event)
                    report["skipped"].append(event)
                    continue
                key=f"{market_id}:{mode}"
                elapsed=now_epoch-self.last_refresh_epoch.get(key,0.0)
                if elapsed<interval_seconds:
                    event={
                        "market_id":market_id,
                        "mode":mode,
                        "action":"SKIP",
                        "reason":"INTERVAL_NOT_DUE",
                        "elapsed_seconds":max(0.0,elapsed),
                        "interval_seconds":interval_seconds,
                    }
                    market_report["events"].append(event)
                    report["skipped"].append(event)
                    continue
                try:
                    result=await asyncio.to_thread(self.engine.refresh_market_data,market_id,mode)
                    snapshot=await asyncio.to_thread(self.engine.market_data_snapshot,market_id,mode,False)
                    observed=await asyncio.to_thread(self.engine.record_market_observation,snapshot)
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
                        self.last_refresh_epoch[key]=0.0
                    else:
                        self.last_refresh[key]=now_monotonic
                        self.last_refresh_epoch[key]=now_epoch
                    self.errors.pop(key,None)
                    state_changed=True
                    event={
                        "market_id":market_id,
                        "mode":mode,
                        "action":"REFRESH",
                        "source_latest_ts":result.get("source_latest_ts"),
                        "points":result.get("points"),
                        "observation_recorded":observed.get("recorded"),
                        "observation_reason":observed.get("reason"),
                        "decision_action":(
                            decision_result.get("action")
                            if isinstance(decision_result,dict)
                            else None
                        ),
                    }
                    market_report["events"].append(event)
                    report["refreshed"].append(event)
                    print(
                        "TRIAID_MARKET_DATA_AUTO_REFRESH",
                        market_id,mode,
                        result.get("source_latest_ts"),
                        result.get("points"),
                        observed.get("recorded"),
                        observed.get("reason"),
                    )
                    if decision_result is not None:
                        print(
                            "TRIAID_DECISION_AUTOMATION",
                            market_id,mode,
                            decision_result.get("action"),
                        )
                except Exception as exc:
                    message=f"{type(exc).__name__}:{exc}"
                    self.errors[key]=message
                    self.last_refresh[key]=now_monotonic
                    self.last_refresh_epoch[key]=now_epoch
                    state_changed=True
                    event={"market_id":market_id,"mode":mode,"action":"ERROR","error":message}
                    market_report["events"].append(event)
                    report["errors"].append(event)
        if state_changed:
            self._persist_state()
        report["ok"]=not bool(report["errors"])
        return report

    async def run(self)->None:
        while True:
            await self.tick_once()
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
        }
        return labels.get(market_id.upper(),{}).get(symbol,symbol)

    def live_indicators(self,market_id:str)->dict:
        market=market_id.upper()
        rows=self.engine.market_observations(market,"REALTIME",300)
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
        for row in self.engine.market_observations(market,None,max(200,limit*4)):
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
        for row in self.engine.market_transitions(market,None,max(200,limit*4)):
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
            for row in self.decision_scheduler.events(market,max(200,limit*4)):
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
        key=f"{market_id.upper()}:{mode.upper()}"
        self.last_refresh[key]=time.monotonic()
        self.last_refresh_epoch[key]=time.time()
        self._persist_state()

    def status(self)->dict:
        return {
            "version":self.version,
            "automation_enabled":self.enabled,
            "discipline":"OFFICIAL_TRADING_CALENDAR_GATED_REFRESH; DATA_REFRESH_DOES_NOT_TRIGGER_TRADING",
            "session_phase":{m:session_phase(m) for m in ("US","CN")},
            "official_trading_calendar":calendar_status(),
            "last_phase":dict(self.last_phase),
            "refresh_plan":{m:self.refresh_plan(m) for m in ("US","CN")},
            "last_refresh_monotonic":dict(self.last_refresh),
            "last_refresh_epoch":dict(self.last_refresh_epoch),
            "persistent_scheduler_state":self.state_name,
            "external_tick_ready":True,
            "automation_errors":dict(self.errors),
            "frequency_policy":self.frequency_policy.status(),
            "decision_scheduler":(
                self.decision_scheduler.status()
                if self.decision_scheduler is not None
                else None
            ),
            "hub":self.engine.market_data_status(),
        }
