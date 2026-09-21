from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

tmp=tempfile.mkdtemp(prefix="triaid-startup-readonly-")
os.environ["TRIAID_STORAGE_BACKEND"]="file"
os.environ["TRIAID_DATA_DIR"]=tmp
os.environ["TRIAID_DATA_AUTOMATION"]="0"
os.environ["TRIAID_CALENDAR_SYNC"]="0"
os.environ["TRIAID_DECISION_AUTOMATION"]="0"
os.environ["TRIAID_STARTUP_MAINTENANCE"]="0"

from triaid_fin.contracts import MarketSnapshot, RunRecord
from triaid_fin.store import RunStore

store=RunStore()
store.save_json("population_state.json",{
    "version":"population-state@legacy",
    "markets":{},
    "last_observation_keys":{"US":"DAILY:legacy"},
})
store.save_json("decision_scheduler_state.json",{
    "markets":{
        "US":{
            "session_date":"2020-01-01",
            "baseline_done":True,
        }
    }
})
store.save_run(RunRecord(
    run_id="US-stale-startup-contract",
    status="FETCHING_DATA",
    module_manifest={},
    market=MarketSnapshot(
        market_id="US",
        as_of="2026-09-18",
        snapshot_id="PENDING",
        metadata={
            "run_scope":"OFFICIAL_EVIDENCE",
            "evidence_eligible":True,
        },
    ),
))

def fingerprint(root:str)->dict[str,str]:
    result={}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        rel=str(path.relative_to(root))
        result[rel]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result

before=fingerprint(tmp)

import app

assert app.engine.get_run("US-stale-startup-contract").status=="FETCHING_DATA"
assert app.engine.population_state.state["version"]=="population-state@0.4.0"
assert app.engine.population_state.state.get("migration_pending_persist") is True

app.status()
app.engine.evolution_status()
app.engine.strategy_evolution_status()
app.engine.population_state.status("US")
app.decision_scheduler.status()
app.market_automation.status()
app.calendar_sync.status()

after_reads=fingerprint(tmp)
assert after_reads==before,(
    "application construction or read/status endpoints changed persistence",
    before,
    after_reads,
)

receipt=app.engine.recover_stale_runs()
assert receipt["recovered_count"]==1
assert receipt["recovered_run_ids"]==["US-stale-startup-contract"]
assert app.engine.get_run("US-stale-startup-contract").status=="FAILED"
after_maintenance=fingerprint(tmp)
assert after_maintenance!=before
assert "runtime_maintenance_events.jsonl" in after_maintenance

print("TRIAID_STARTUP_READONLY_CONTRACT_SMOKE_PASS")
print({
    "files_before":len(before),
    "files_after_readonly":len(after_reads),
    "maintenance_recovered":receipt["recovered_count"],
})
