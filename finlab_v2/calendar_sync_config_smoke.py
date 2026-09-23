from __future__ import annotations

import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
src=(ROOT/"triaid_fin"/"trading_calendar_sync.py").read_text(encoding="utf-8")

checks={
    "blank_env_falls_back_default":'(os.getenv("TRIAID_CALENDAR_SYNC_INTERVAL_SECONDS") or "21600").strip()' in src,
    "invalid_env_falls_back_default":"except ValueError:" in src and "interval_value=21600" in src,
    "minimum_interval_preserved":"max(3600,interval_value)" in src,
}
failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_CALENDAR_SYNC_CONFIG_SMOKE_FAILED:"+",".join(failed))
print("TRIAID_CALENDAR_SYNC_CONFIG_SMOKE_PASS",len(checks))
