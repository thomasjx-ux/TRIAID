from pathlib import Path

smoke=Path("production_api_smoke.py").read_text(encoding="utf-8")
route=Path("triaid_fin/us_return_max.py").read_text(encoding="utf-8")

for needle in [
    "TRIAID 相对收益差",
    "annualized historical strategy state-return estimate",
    "not a calibrated future-return forecast",
]:
    assert needle in smoke,needle

for needle in [
    "MAXIMIZE_CURRENT_MULTI_WINDOW_STATE_RETURN_ESTIMATE_NET_OF_META_SWITCH_COST_ACROSS_ADMISSIBLE_ACTIVE_STRATEGIES_THEN_APPLY_EXECUTION_CAPACITY",
    "MAX_NET_STATE_RETURN_ESTIMATE_WITH_DETERMINISTIC_TIE_BREAK",
    "ALL_ADMISSIBLE_ACTIVE_STRATEGIES_NET_OF_META_SWITCH_COST",
]:
    assert needle in route,needle

assert "TRIAID 增益" not in smoke


smoke_text=Path("production_api_smoke.py").read_text(encoding="utf-8")
assert 'TRIAID_PRODUCTION_SMOKE_MUTATIONS","0"' in smoke_text
assert 'read-only production smoke changed persistent run ledger' in smoke_text
assert 'if MUTATING_SMOKE:' in smoke_text

print("TRIAID_PRODUCTION_SMOKE_CONTRACT_STATIC_PASS")
