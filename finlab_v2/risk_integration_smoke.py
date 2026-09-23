from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
rw=engine.risk_warning_run(force=True)
rc=engine.risk_control_run(force=True)

assert rw["warning_type"]=="TRIAID_RISK_WARNING"
assert rw["production_action"]=="NONE"
assert rw["applied_to_weights"] is False
assert rc["experiment_type"]=="THREE_MARKET_RISK_CONTROL_SHADOW"
assert rc["risk_control_experiment"]["production_action"]=="NONE"
assert rc["risk_control_experiment"]["applied_to_weights"] is False
assert [x["market"] for x in rc["three_market_state"]]==["US","CN","HK"]
assert "risk_evidence_coverage" in rc["data_quality"]
print("TRIAID_RISK_INTEGRATION_SMOKE_PASS",{
    "risk_warning":rw.get("version"),
    "risk_control":rc.get("version"),
    "risk":(rw.get("overall") or {}).get("risk_pressure_index"),
    "stage":(rc.get("risk_control_experiment") or {}).get("stage"),
})
