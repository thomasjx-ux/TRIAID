import os
import shutil
import tempfile

tmp=tempfile.mkdtemp(prefix="triaid-fin-v2-live-smoke-")
os.environ["TRIAID_DATA_DIR"]=tmp

from triaid_fin.engine import EvolutionLabEngine

try:
    engine=EvolutionLabEngine()
    rows=[]
    for market_id in ("US","CN"):
        pending=engine.create_pending_live_run(market_id)
        engine.execute_live(pending.run_id,market_id)
        run=engine.get_run(pending.run_id)
        assert run.status=="DECISION_READY_AWAITING_OUTCOME", (market_id,run.status,run.diagnostic_summary)
        assert run.audit and run.audit.passed
        assert len(run.strategy_states)==(29 if market_id=="US" else 33)
        assert run.strategy_group is not None
        assert run.triaid_decision is not None
        assert run.market.metadata.get("source")
        assert run.market.as_of
        rows.append({
            "market":market_id,
            "as_of":run.market.as_of,
            "snapshot_id":run.market.snapshot_id,
            "members":run.strategy_group.members,
            "core":run.triaid_decision.core_version,
        })
    print("TRIAID_FIN_V2_LIVE_SMOKE_PASS")
    for row in rows:
        print(row)
finally:
    shutil.rmtree(tmp,ignore_errors=True)
