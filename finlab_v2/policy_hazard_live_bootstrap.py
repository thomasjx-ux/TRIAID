from __future__ import annotations
from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
curve=engine.policy_curve_run(force=True)
latent=engine.latent_hazard_run(force=False)
frozen=engine.hazard_prospective_freeze(latent,curve)
resolved=engine.hazard_prospective_resolve()
print("TRIAID_POLICY_CURVE_LIVE_PASS",{
    "snapshot_id":curve.get("snapshot_id"),
    "as_of":curve.get("as_of"),
    "data_quality":curve.get("data_quality"),
    "metrics":curve.get("metrics"),
    "fed_funds_symbols":[x.get("symbol") for x in curve.get("fed_funds_curve",[])],
    "sofr_1m_symbols":[x.get("symbol") for x in curve.get("sofr_1m_curve",[])],
    "sofr_3m_symbols":[x.get("symbol") for x in curve.get("sofr_3m_curve",[])],
})
print("TRIAID_HAZARD_PROSPECTIVE_LIVE_PASS",{
    "ledger_id":frozen.get("ledger_id"),
    "as_of":frozen.get("as_of"),
    "state_label":(frozen.get("current_state") or {}).get("state_label"),
    "supported_triggers":(frozen.get("current_state") or {}).get("statistically_supported_composites_triggered"),
    "policy_curve_snapshot_id":frozen.get("policy_curve_snapshot_id"),
    "resolve":resolved,
})
