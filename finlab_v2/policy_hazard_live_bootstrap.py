from __future__ import annotations

import os

from triaid_fin.engine import EvolutionLabEngine


def _read_only()->bool:
    return os.getenv("TRIAID_RUNTIME_READONLY","0").strip().lower() in {"1","true","on","yes"}


engine=EvolutionLabEngine()
if _read_only():
    curve=engine.policy_curve_latest()
    latent=engine.latent_hazard_latest()
    frozen=engine.hazard_prospective_latest()
    resolved={"state":"READ_ONLY_NOT_RESOLVED","reason":"SHADOW_RUNTIME_MUST_NOT_MUTATE_PROSPECTIVE_LEDGER"}
    assert isinstance(curve,dict) and bool(curve), "missing persisted policy curve"
    assert isinstance(latent,dict) and bool(latent), "missing persisted latent hazard"
    assert isinstance(frozen,dict) and bool(frozen), "missing persisted prospective hazard"
else:
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
    "runtime_mode":"READ_ONLY_SHADOW" if _read_only() else "PRODUCTION",
})
print("TRIAID_HAZARD_PROSPECTIVE_LIVE_PASS",{
    "ledger_id":frozen.get("ledger_id"),
    "as_of":frozen.get("as_of"),
    "state_label":(frozen.get("current_state") or {}).get("state_label"),
    "supported_triggers":(frozen.get("current_state") or {}).get("statistically_supported_composites_triggered"),
    "policy_curve_snapshot_id":frozen.get("policy_curve_snapshot_id"),
    "resolve":resolved,
    "runtime_mode":"READ_ONLY_SHADOW" if _read_only() else "PRODUCTION",
})
