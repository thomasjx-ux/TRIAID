from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parent
calendar=(ROOT/"triaid_fin"/"trading_calendar_sync.py").read_text(encoding="utf-8")
runtime=(ROOT/"triaid_fin"/"market_runtime.py").read_text(encoding="utf-8")

checks={
    "calendar_blank_env_safe":'(os.getenv("TRIAID_CALENDAR_SYNC_INTERVAL_SECONDS") or "21600").strip()' in calendar,
    "calendar_invalid_env_safe":"interval_value=21600" in calendar,
    "refresh_timeout_blank_env_safe":'(os.getenv("TRIAID_REFRESH_TIMEOUT_SECONDS") or "30").strip()' in runtime,
    "refresh_timeout_invalid_env_safe":"refresh_timeout_value=30" in runtime,
    "refresh_timeout_minimum_preserved":"max(10,refresh_timeout_value)" in runtime,
}
failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_RUNTIME_ENV_CONFIG_SMOKE_FAILED:"+",".join(failed))
print("TRIAID_RUNTIME_ENV_CONFIG_SMOKE_PASS",len(checks))
