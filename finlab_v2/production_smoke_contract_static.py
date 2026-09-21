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

# The old Return-Max enum literals may appear only inside the explicit immutable
# frozen-ledger compatibility guard. The obsolete UI label must never return.
assert 'TRIAID 增益' not in text
assert 'annualized historical strategy state-return estimate' in text
assert 'not a calibrated future-return forecast' in text

print("TRIAID_PRODUCTION_SMOKE_CONTRACT_STATIC_PASS")
