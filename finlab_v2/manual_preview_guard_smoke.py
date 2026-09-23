from __future__ import annotations

import os
from datetime import datetime, timedelta
from threading import RLock
from zoneinfo import ZoneInfo

from triaid_fin.engine import EvolutionLabEngine


class FakeStore:
    def __init__(self):
        self.saved=[]

    def save_run(self,run):
        self.saved.append(run.run_id)


class PreviewGuardEngine(EvolutionLabEngine):
    @property
    def module_manifest(self):
        return {"architecture":"preview-guard-smoke"}


engine=PreviewGuardEngine.__new__(PreviewGuardEngine)
engine._lock=RLock()
engine._runs={}
engine.store=FakeStore()

old=os.environ.get("TRIAID_MANUAL_PREVIEW_COOLDOWN_SECONDS")
os.environ["TRIAID_MANUAL_PREVIEW_COOLDOWN_SECONDS"]="60"
try:
    first,scheduled,reason=engine.claim_manual_preview_run("HK")
    assert scheduled is True
    assert reason=="CREATED"
    assert first.status=="FETCHING_DATA"
    assert first.market.metadata["run_scope"]=="MANUAL_PREVIEW"
    assert first.market.metadata["evidence_eligible"] is False
    assert engine.store.saved==[]

    second,scheduled,reason=engine.claim_manual_preview_run("HK")
    assert scheduled is False
    assert reason=="PENDING_REUSED"
    assert second.run_id==first.run_id
    assert engine.store.saved==[]

    first.status="PREVIEW_READY"
    third,scheduled,reason=engine.claim_manual_preview_run("HK")
    assert scheduled is False
    assert reason=="COOLDOWN_REUSED"
    assert third.run_id==first.run_id

    first.created_at=(
        datetime.now(ZoneInfo("UTC"))-timedelta(seconds=61)
    ).isoformat()
    fourth,scheduled,reason=engine.claim_manual_preview_run("HK")
    assert scheduled is True
    assert reason=="CREATED"
    assert fourth.run_id!=first.run_id
    assert engine.store.saved==[]
finally:
    if old is None:
        os.environ.pop("TRIAID_MANUAL_PREVIEW_COOLDOWN_SECONDS",None)
    else:
        os.environ["TRIAID_MANUAL_PREVIEW_COOLDOWN_SECONDS"]=old

print(
    "TRIAID_MANUAL_PREVIEW_GUARD_SMOKE_PASS",
    {
        "first":first.run_id,
        "reused_pending":second.run_id,
        "reused_cooldown":third.run_id,
        "next_after_cooldown":fourth.run_id,
        "persistent_writes":len(engine.store.saved),
    },
)
