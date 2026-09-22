from pathlib import Path

smoke=Path("production_api_smoke.py").read_text(encoding="utf-8")
route=Path("triaid_fin/us_return_max.py").read_text(encoding="utf-8")

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

for needle in [
    "historical strategy state-return estimate",
    "calibrated future-return forecast",
    "MAXIMIZE_CURRENT_MULTI_WINDOW_STATE_RETURN_ESTIMATE_NET_OF_META_SWITCH_COST_ACROSS_ADMISSIBLE_ACTIVE_STRATEGIES_THEN_APPLY_EXECUTION_CAPACITY",
    "MAX_REALIZABLE_NET_RETURN_PROXY_WITH_COST_ONLY_THEN_DETERMINISTIC_TIE_BREAK",
    "ALL_ADMISSIBLE_ACTIVE_STRATEGIES_NET_OF_META_SWITCH_COST",
]:
    assert needle in route,needle

assert "TRIAID 增益" not in smoke


smoke_text=Path("production_api_smoke.py").read_text(encoding="utf-8")
assert 'TRIAID_PRODUCTION_SMOKE_MUTATIONS","0"' in smoke_text
assert 'TRIAID_PRODUCTION_SMOKE_EXPECTED_STORAGE' in smoke_text
assert 'startup_maintenance_enabled' in smoke_text
assert 'read-only production smoke changed persistent run ledger' in smoke_text
assert 'if MUTATING_SMOKE:' in smoke_text

probe_text=Path("production_storage_readonly_probe.py").read_text(encoding="utf-8")
assert 'TRIAID_PRODUCTION_STORAGE_READONLY_PROBE_PASS' in probe_text
assert 'store.list_runs()' in probe_text
assert 'save_' not in probe_text
assert 'append_' not in probe_text

print("TRIAID_PRODUCTION_SMOKE_CONTRACT_STATIC_PASS")
