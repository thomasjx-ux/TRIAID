from __future__ import annotations

import asyncio
import os
import time

from .market_data import session_phase
from .frequency_policy import FrequencyPolicy
from .trading_calendar import calendar_status


class MarketDataAutomation:
    version="market-data-automation@0.4.0"

    def __init__(self,engine,decision_scheduler=None)->None:
        self.engine=engine
        self.decision_scheduler=decision_scheduler
        self.enabled=os.getenv("TRIAID_DATA_AUTOMATION","1").lower() not in {"0","false","off","no"}
        self.last_refresh:dict[str,float]={}
        self.errors:dict[str,str]={}
        self.last_phase:dict[str,str]={}
        self.frequency_policy=FrequencyPolicy(engine.store)

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

    async def run(self)->None:
        while True:
            now=time.monotonic()
            for market_id in ("US","CN"):
                phase=session_phase(market_id)
                if self.last_phase.get(market_id)!=phase:
                    for key in [k for k in self.last_refresh if k.startswith(f"{market_id}:")]:
                        self.last_refresh[key]=0.0
                    self.last_phase[market_id]=phase
                    print("TRIAID_MARKET_PHASE",market_id,phase)
                capabilities=self.engine.market_data_capabilities(market_id)[market_id]
                for mode,interval_seconds in self.refresh_plan(market_id).items():
                    if not capabilities.get(mode,{}).get("supported",False):
                        continue
                    key=f"{market_id}:{mode}"
                    if now-self.last_refresh.get(key,0.0)<interval_seconds:
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
                        else:
                            self.last_refresh[key]=now
                        self.errors.pop(key,None)
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
                        self.errors[key]=f"{type(exc).__name__}:{exc}"
                        self.last_refresh[key]=now
            await asyncio.sleep(30)

    def record_manual_refresh(self,market_id:str,mode:str)->None:
        self.last_refresh[f"{market_id.upper()}:{mode.upper()}"]=time.monotonic()

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
            "automation_errors":dict(self.errors),
            "frequency_policy":self.frequency_policy.status(),
            "decision_scheduler":(
                self.decision_scheduler.status()
                if self.decision_scheduler is not None
                else None
            ),
            "hub":self.engine.market_data_status(),
        }
