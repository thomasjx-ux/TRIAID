from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from .market_registry import market_ids


VERSION="outcome-resolution-automation@1.0.0"


class OutcomeResolutionAutomation:
    """Resolve matured T0/T1 evidence outside all HTTP read paths."""

    version=VERSION

    def __init__(self,resolver)->None:
        self.resolver=resolver
        self.enabled=(
            os.getenv("TRIAID_OUTCOME_AUTOMATION","1").strip().lower()
            not in {"0","false","off","no"}
        )
        raw=(os.getenv("TRIAID_OUTCOME_AUTOMATION_INTERVAL_SECONDS") or "300").strip()
        try:
            interval=int(raw)
        except ValueError:
            interval=300
        self.interval_seconds=max(60,min(interval,3600))
        self.cycles=0
        self.last_cycle_utc:str|None=None
        self.last_results:dict[str,dict]={}
        self.errors:dict[str,str]={}

    async def run_once(self)->dict:
        results={}
        for market in market_ids():
            try:
                resolved=await asyncio.to_thread(self.resolver.resolve_market,market)
                summary={
                    "market_id":market,
                    "formal_evidence_count":int(resolved.get("formal_evidence_count") or 0),
                    "evaluated_count":int(resolved.get("evaluated_count") or 0),
                    "waiting_count":int(resolved.get("waiting_count") or 0),
                    "read_health":dict(resolved.get("read_health") or {}),
                }
                results[market]=summary
                self.last_results[market]=summary
                self.errors.pop(market,None)
            except Exception as exc:
                self.errors[market]=f"{type(exc).__name__}:{exc}"
        self.cycles+=1
        self.last_cycle_utc=datetime.now(timezone.utc).isoformat()
        return {
            "version":self.version,
            "cycle":self.cycles,
            "at_utc":self.last_cycle_utc,
            "markets":results,
            "errors":dict(self.errors),
        }

    async def run(self)->None:
        while True:
            report=await self.run_once()
            print(
                "TRIAID_OUTCOME_AUTOMATION_CYCLE",
                report["cycle"],
                len(report["markets"]),
                len(report["errors"]),
                flush=True,
            )
            await asyncio.sleep(self.interval_seconds)

    def status(self)->dict:
        return {
            "version":self.version,
            "enabled":self.enabled,
            "interval_seconds":self.interval_seconds,
            "cycles":self.cycles,
            "last_cycle_utc":self.last_cycle_utc,
            "last_results":dict(self.last_results),
            "errors":dict(self.errors),
            "discipline":"WRITER_ACTIVATED_RUNTIME_TASK_ONLY_NEVER_HTTP_READ_TRIGGERED",
        }
