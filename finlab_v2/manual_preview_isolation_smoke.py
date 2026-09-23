from __future__ import annotations

import os
import tempfile
from copy import deepcopy

tmp=tempfile.mkdtemp(prefix="triaid-manual-preview-")
os.environ["TRIAID_STORAGE_BACKEND"]="file"
os.environ["TRIAID_DATA_DIR"]=tmp
os.environ["TRIAID_DATA_AUTOMATION"]="0"
os.environ["TRIAID_CALENDAR_SYNC"]="0"
os.environ["TRIAID_DECISION_AUTOMATION"]="0"

import triaid_fin.engine as engine_module
from triaid_fin.contracts import MarketSnapshot, OutcomeRequest, StrategyState
from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()

states=[]
us_defs=[
    d for d in engine.strategy_population.definitions()
    if "US" in {m.upper() for m in d.market_support}
]
for i,definition in enumerate(us_defs):
    expected=0.0 if definition.strategy_id=="P28_CASH" else 0.30-0.006*i
    states.append(
        StrategyState(
            strategy_id=definition.strategy_id,
            lifecycle="active",
            expected_net_return=expected,
            risk=0.08+0.001*i,
            uncertainty=0.01,
            estimated_cost=0.0002,
            metrics={"latest_return":0.001},
            recent_returns=[
                0.0002*(i+1)+0.00005*((j%5)-2)
                for j in range(80)
            ],
        )
    )

snapshot=MarketSnapshot(
    market_id="US",
    as_of="2026-09-18",
    snapshot_id="US:2026-09-18:FINAL:manual-preview-smoke",
    regime="mixed",
    metadata={
        "session_phase":"CLOSED",
        "daily_bar_complete":True,
        "base_cost_bps":2.0,
    },
)

def fake_prepare_live_market(market_id,window_weights):
    assert market_id=="US"
    return {
        "snapshot":snapshot.model_copy(deep=True),
        "panel":None,
        "latest_as_of":"2026-09-18",
        "previous_as_of":"2026-09-17",
        "strategy_states":[s.model_copy(deep=True) for s in states],
        "realized_returns_from_previous_period":{
            s.strategy_id:0.001 for s in states
        },
        "product_realized_returns_from_previous_period":{},
        "product_turnover_notional_from_previous_period":{},
    }

engine_module.prepare_live_market=fake_prepare_live_market

population_before=deepcopy(engine.population_state.state)
prospective_before=deepcopy(engine.prospective_experiment.list(1000))
recovery_decisions_before=deepcopy(engine.recovery_wave_ledger.decisions("CN",1000))
recovery_outcomes_before=deepcopy(engine.recovery_wave_ledger.outcomes("CN",1000))
us_decisions_before=deepcopy(engine.us_return_max_ledger.decisions(1000))
us_outcomes_before=deepcopy(engine.us_return_max_ledger.outcomes(1000))
persisted_before=[r.run_id for r in engine.store.list_runs()]

pending=engine.create_pending_live_run("US","MANUAL_PREVIEW")
duplicate=engine.create_pending_live_run("US","MANUAL_PREVIEW")
assert duplicate.run_id==pending.run_id
assert pending.market.metadata["evidence_eligible"] is False
assert [r.run_id for r in engine.store.list_runs()]==persisted_before

engine.execute_live(pending.run_id,"US","MANUAL_PREVIEW")
preview=engine.get_run(pending.run_id)

assert preview.status=="PREVIEW_READY", {"status":preview.status,"diagnostic_summary":preview.diagnostic_summary,"audit":preview.audit.model_dump(mode="json") if preview.audit else None}
assert preview.strategy_group is not None
assert preview.triaid_decision is not None
assert preview.market.metadata["run_scope"]=="MANUAL_PREVIEW"
assert preview.market.metadata["evidence_eligible"] is False
assert preview.market.metadata["broker_execution_enabled"] is False
assert preview.diagnostic_summary["evidence_eligible"] is False
assert preview.diagnostic_summary["persistent_run_record"] is False
assert preview.diagnostic_summary["evidence_state"]=="MANUAL_PREVIEW_NON_EVIDENCE"

assert engine.population_state.state==population_before
assert engine.prospective_experiment.list(1000)==prospective_before
assert engine.recovery_wave_ledger.decisions("CN",1000)==recovery_decisions_before
assert engine.recovery_wave_ledger.outcomes("CN",1000)==recovery_outcomes_before
assert engine.us_return_max_ledger.decisions(1000)==us_decisions_before
assert engine.us_return_max_ledger.outcomes(1000)==us_outcomes_before
assert [r.run_id for r in engine.store.list_runs()]==persisted_before
assert engine.latest_decision_run("US") is None
assert engine.latest_run("US") is None
assert all(
    row.get("run_id")!=preview.run_id
    for row in engine.daily_summary("US").get("runs_detail",[])
)

try:
    engine.submit_outcome(
        preview.run_id,
        OutcomeRequest(
            realized_returns={s.strategy_id:0.001 for s in states},
            trading_cost=0.0,
        ),
    )
except ValueError as exc:
    assert "manual_preview_is_not_evidence_eligible" in str(exc)
else:
    raise AssertionError("manual preview accepted posterior outcome")

print("TRIAID_MANUAL_PREVIEW_ISOLATION_SMOKE_PASS")
print({
    "run_id":preview.run_id,
    "status":preview.status,
    "selected":len(preview.strategy_group.members),
    "persistent_runs_before":len(persisted_before),
    "persistent_runs_after":len(engine.store.list_runs()),
})
