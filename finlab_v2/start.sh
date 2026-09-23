#!/bin/sh
set -eu

export TRIAID_STARTUP_MAINTENANCE="${TRIAID_STARTUP_MAINTENANCE:-1}"
export TRIAID_LONG_RESEARCH_BOOTSTRAP="${TRIAID_LONG_RESEARCH_BOOTSTRAP:-1}"
export TRIAID_RELEASE_AUDIT_REQUIRED="${TRIAID_RELEASE_AUDIT_REQUIRED:-1}"
export TRIAID_RELEASE_AUDIT_RECEIPT_PATH="${TRIAID_RELEASE_AUDIT_RECEIPT_PATH:-/tmp/triaid_release_audit.json}"

# Railway's injected Git SHA is the authoritative runtime source identity.
# Bind both declared and runtime revisions to it before app import/audit so
# stale dashboard variables cannot describe a different source than the
# container that is actually running.
if [ -n "${RAILWAY_GIT_COMMIT_SHA:-}" ]; then
  export TRIAID_DEPLOY_REVISION="$RAILWAY_GIT_COMMIT_SHA"
  export TRIAID_DEPLOY_REV="$RAILWAY_GIT_COMMIT_SHA"
  export TRIAID_V2_REV="$RAILWAY_GIT_COMMIT_SHA"
fi

if [ -x /app/.venv/bin/python ]; then
  PYTHON_BIN=/app/.venv/bin/python
  UVICORN_BIN=/app/.venv/bin/uvicorn
else
  PYTHON_BIN="$(command -v python)"
  UVICORN_BIN="$(command -v uvicorn)"
fi

rm -f "$TRIAID_RELEASE_AUDIT_RECEIPT_PATH"

"$UVICORN_BIN" app:app --host 0.0.0.0 --port "${PORT:-8080}" &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

if ! "$PYTHON_BIN" release_audit.py runtime; then
  echo TRIAID_RELEASE_AUDIT_BLOCKED_DEPLOY
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
  exit 1
fi

echo TRIAID_RELEASE_READY
trap - INT TERM EXIT
wait "$SERVER_PID"
