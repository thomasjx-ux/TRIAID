from pathlib import Path

text=Path("production_api_smoke.py").read_text(encoding="utf-8")

required=[
    'TRIAID 相对收益差',
    'STRICT_MAXIMIZE_CURRENT_MULTI_WINDOW_STATE_RETURN_ESTIMATE',
    'STRICT_MAX_STATE_RETURN_ESTIMATE_WITH_DETERMINISTIC_TIE_BREAK',
    'ALL_ADMISSIBLE_ACTIVE_STRATEGIES_STRICT_MAX_STATE_RETURN_ESTIMATE',
]
for needle in required:
    assert needle in text,needle

for stale in [
    'TRIAID 增益',
    'STRICT_MAX_EXPECTED_NET_RETURN_WITH_DETERMINISTIC_TIE_BREAK',
    'ALL_ACTIVE_STRATEGIES_STRICT_MAX_EXPECTED_NET_RETURN',
]:
    assert stale not in text,stale

print("TRIAID_PRODUCTION_SMOKE_CONTRACT_STATIC_PASS")
