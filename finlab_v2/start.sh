#!/bin/sh
set -eu

export TRIAID_STARTUP_MAINTENANCE="${TRIAID_STARTUP_MAINTENANCE:-1}"
export TRIAID_LONG_RESEARCH_BOOTSTRAP="${TRIAID_LONG_RESEARCH_BOOTSTRAP:-1}"

if [ -x /app/.venv/bin/python ]; then
  PYTHON_BIN=/app/.venv/bin/python
  UVICORN_BIN=/app/.venv/bin/uvicorn
else
  PYTHON_BIN="$(command -v python)"
  UVICORN_BIN="$(command -v uvicorn)"
fi

# Runtime starts immediately. Non-critical verification is intentionally delayed
# so it cannot compete with readiness or block health checks.
(
  sleep "${TRIAID_POST_START_UI_AUDIT_DELAY:-15}"
  "$PYTHON_BIN" ui_smoke.py || echo TRIAID_UI_NONBLOCKING_AUDIT_FAILED
) &
(
  sleep "${TRIAID_POST_START_LIVE_BOOTSTRAP_DELAY:-20}"
  "$PYTHON_BIN" hk_market_live_bootstrap.py || echo TRIAID_HK_MARKET_LIVE_FAILED
) &
(
  sleep "${TRIAID_POST_START_LIVE_BOOTSTRAP_DELAY:-20}"
  "$PYTHON_BIN" policy_hazard_live_bootstrap.py || echo TRIAID_POLICY_HAZARD_LIVE_FAILED
) &
(
  sleep "${TRIAID_POST_START_FULL_AUDIT_DELAY:-60}"
  "$PYTHON_BIN" risk_center_full_audit.py || echo TRIAID_RISK_CENTER_FULL_AUDIT_FAILED
) &

exec "$UVICORN_BIN" app:app --host 0.0.0.0 --port "${PORT:-8080}"
