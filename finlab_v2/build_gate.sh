#!/bin/sh
set -eu

# Build-time audits must never read or mutate production persistence even when
# the Railway service is configured for Supabase at runtime.
export TRIAID_STORAGE_BACKEND=file
export TRIAID_DATA_DIR="${TRIAID_BUILD_AUDIT_DATA_DIR:-/tmp/triaid-fin-v2-build-audit}"

# Build audits exercise the canonical automated research path. Runtime services
# may deliberately disable automation (for example isolated Shadow candidates),
# but those runtime flags must not weaken or invalidate build-time regressions.
export TRIAID_DATA_AUTOMATION=1
export TRIAID_DECISION_AUTOMATION=1

rm -rf "$TRIAID_DATA_DIR"

exec python release_audit.py build
