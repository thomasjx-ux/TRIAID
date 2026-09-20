from __future__ import annotations

import asyncio
import os
import time

from .market_data import session_phase


class MarketDataAutomation:
    version="market-data-automation@0.1.0"

    def __init__(self,engine)->None:
        self.engine=engine
        self.enabled=os.getenv("TRIAID_DATA_AUTOMATION","1").lower() not in {"0","false","off","no"}
        self.last_refresh:dict[str,float]={}
        self.errors:dict[str,str]={}

    def refresh_plan(self,market_id:str)->dict[str,int]:
        phase=session_phase(market_id)
        if phase=="OPEN":
            return {"INTRADAY":300,"REALTIME":120}
        if phase=="PREOPEN":
            if market_id.upper()=="US":
                return {"PREOPEN":300,"REALTIME":120}
            return {"DAILY":1800}
        if phase=="POSTCLOSE":
            return {"DAILY":600}
        return {"DAILY":3600}

    async def run(self)->None:
        while True:
            now=time.monotonic()
            for market_id in ("US","CN"):
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
                        self.last_refresh[key]=now
                        self.errors.pop(key,None)
                        print(
                            "TRIAID_MARKET_DATA_AUTO_REFRESH",
                            market_id,mode,
                            result.get("source_latest_ts"),
                            result.get("points"),
                            observed.get("recorded"),
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
            "discipline":"DATA_REFRESH_DOES_NOT_TRIGGER_TRADING_OR_CORE_ADJUSTMENT",
            "session_phase":{m:session_phase(m) for m in ("US","CN")},
            "refresh_plan":{m:self.refresh_plan(m) for m in ("US","CN")},
            "last_refresh_monotonic":dict(self.last_refresh),
            "automation_errors":dict(self.errors),
            "hub":self.engine.market_data_status(),
        }
