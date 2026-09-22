from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
report=engine.long_cycle_hypothesis_run(force=False)
print("TRIAID_LONG_CYCLE_LIVE_BOOTSTRAP_PASS",{
    "experiment_id":report.get("experiment_id"),
    "as_of":report.get("as_of"),
    "horizons":report.get("horizon_years"),
    "downturn_state":((report.get("hypotheses") or {}).get("downturn_confirmation") or {}).get("state"),
    "downturn_support_ratio":((report.get("hypotheses") or {}).get("downturn_confirmation") or {}).get("support_ratio"),
    "stretch_state":((report.get("hypotheses") or {}).get("stretch_vulnerability") or {}).get("state"),
    "stretch_support_ratio":((report.get("hypotheses") or {}).get("stretch_vulnerability") or {}).get("support_ratio"),
    "data_completeness":report.get("data_completeness"),
    "errors":report.get("errors"),
})
