from __future__ import annotations

import asyncio
from pathlib import Path

from triaid_fin.outcome_runtime import OutcomeResolutionAutomation

class FakeResolver:
    def __init__(self)->None:
        self.markets=[]
    def resolve_market(self,market:str)->dict:
        self.markets.append(market)
        return {
            "formal_evidence_count":3,
            "evaluated_count":2,
            "waiting_count":1,
            "read_health":{"state":"OK","error_count":0},
        }

resolver=FakeResolver()
automation=OutcomeResolutionAutomation(resolver)
report=asyncio.run(automation.run_once())
assert resolver.markets==["US","CN","HK"]
assert report["cycle"]==1
assert set(report["markets"])=={"US","CN","HK"}
assert automation.status()["discipline"]=="WRITER_ACTIVATED_RUNTIME_TASK_ONLY_NEVER_HTTP_READ_TRIGGERED"

app_text=Path("app.py").read_text(encoding="utf-8")
assert "background_tasks.add_task(_resolve_outcome_background" not in app_text
assert "def _resolve_outcome_background(" not in app_text
assert "run_after_writer_activation(outcome_automation.run)" in app_text
assert "outcome_automation.status()" in app_text

print("TRIAID_READ_PATH_PURITY_SMOKE_PASS")
