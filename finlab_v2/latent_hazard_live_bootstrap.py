from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
report=engine.latent_hazard_run(force=True)
print("TRIAID_LATENT_HAZARD_LIVE_PASS",{
    "experiment_id":report.get("experiment_id"),
    "as_of":report.get("as_of"),
    "event_count":report.get("event_count"),
    "control_count":report.get("control_count"),
    "top_long_lead_candidates":report.get("top_long_lead_candidates"),
    "top_any_lead_candidates":report.get("top_any_lead_candidates"),
    "data_completeness":report.get("data_completeness"),
})
