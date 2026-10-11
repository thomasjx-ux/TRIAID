from pathlib import Path

smoke=Path("production_api_smoke.py").read_text(encoding="utf-8")
route=Path("triaid_fin/us_return_max.py").read_text(encoding="utf-8")
legacy_route=Path("triaid_fin/us_return_max_legacy.py").read_text(encoding="utf-8")
core=Path("triaid_fin/core.py").read_text(encoding="utf-8")
opportunity=Path("triaid_fin/opportunity_state.py").read_text(encoding="utf-8")

for needle in [
    "TRIAID 相对收益差",
    "RecoveryWaveCore.version",
    "USReturnMaxRoute.version",
    "us-return-max-route@0.1.0",
    "us-return-max-route@0.2.0",
    "if metric_semantics:",
    "if \"capital_capacity\" in us_return_latest:",
]:
    assert needle in smoke,needle

# The old US implementation is preserved verbatim as a compatibility module;
# the active wrapper adds H20 opportunity gating on top of it.
for needle in [
    "PRIMARY_OBJECTIVE",
    "OBJECTIVE_CONSTITUTION",
    "calibrated future-return forecast",
    "MAX_REALIZABLE_NET_RETURN_UNDER_HARD_CONCENTRATION_AND_EXECUTION_CONSTRAINTS",
    "ALL_ADMISSIBLE_ACTIVE_STRATEGIES_NET_OF_META_SWITCH_COST",
    "fixed_strategy_count_target",
    "risk_used_as_secondary_objective",
    "uncertainty_used_as_secondary_objective",
]:
    assert needle in legacy_route,needle

for needle in [
    'version = "us-return-max-route@0.7.0"',
    "build_market_prediction_adjustment",
    "H20_PREDICTION_ADJUSTMENT_NET_OF_META_SWITCH_COST",
    "prediction_adjustment_ready",
    "market_opportunity_state",
    "prediction_invalidation_contract",
]:
    assert needle in route,needle

for needle in [
    "MARKET_PREDICTION_ADJUSTMENT",
    "prediction_adjustment_version",
    "prediction_decision_state",
    "market_opportunity_state",
]:
    assert needle in core,needle

for needle in [
    '"US"',
    '"CN"',
    '"HK"',
    "MARKET_SPECIFIC_FIXED_DESIGN_PRIOR_NOT_OUTCOME_RETUNED",
    "NO_POSITIVE_EDGE",
]:
    assert needle in opportunity,needle

assert "TRIAID 增益" not in smoke

smoke_text=Path("production_api_smoke.py").read_text(encoding="utf-8")
assert 'TRIAID_PRODUCTION_SMOKE_MUTATIONS","0"' in smoke_text
assert 'TRIAID_PRODUCTION_SMOKE_EXPECTED_STORAGE' in smoke_text
assert 'startup_maintenance_enabled' in smoke_text
assert 'read-only production smoke changed persistent run ledger' in smoke_text
assert 'if MUTATING_SMOKE:' in smoke_text
assert 'route_version=="us-return-max-route@0.5.0"' in smoke_text
assert '"MAXIMIZE_REALIZABLE_NET_RETURN"' in smoke_text
assert '"MAX_REALIZABLE_NET_RETURN_UNDER_HARD_CONCENTRATION_AND_EXECUTION_CONSTRAINTS"' in smoke_text
assert 'fixed_strategy_count_target' in smoke_text
assert 'selected_strategy_count' in smoke_text

probe_text=Path("production_storage_readonly_probe.py").read_text(encoding="utf-8")
assert 'TRIAID_PRODUCTION_STORAGE_READONLY_PROBE_PASS' in probe_text
assert 'store.list_runs()' in probe_text
assert 'save_' not in probe_text
assert 'append_' not in probe_text

app_text=Path("app.py").read_text(encoding="utf-8")
engine_text=Path("triaid_fin/engine.py").read_text(encoding="utf-8")
decision_api_text=Path("triaid_fin/decision_api.py").read_text(encoding="utf-8")

# Manual preview is intentionally public for the UI, but it must remain
# non-evidence-bearing and repeated clicks must not queue duplicate work.
assert 'claim_manual_preview_run(market_id)' in app_text
assert 'if scheduled:' in app_text
assert '"evidence_eligible":False' in app_text
assert 'TRIAID_MANUAL_PREVIEW_COOLDOWN_SECONDS' in engine_text
assert 'PENDING_REUSED' in engine_text
assert 'COOLDOWN_REUSED' in engine_text
assert '"run_scope":"MANUAL_PREVIEW"' in engine_text
assert '"evidence_eligible":False' in engine_text

# Three-market integration must stay symmetric at the API boundary.
assert 'VALID_MARKETS={"US","CN","HK"}' in decision_api_text
assert 'market_id must be US, CN or HK' in decision_api_text

print("TRIAID_PRODUCTION_SMOKE_CONTRACT_STATIC_PASS")
