#!/bin/sh
set -eu

# Build-time audits must never read or mutate production persistence even when
# the runtime service is configured for a remote backend.
export TRIAID_STORAGE_BACKEND=file
export TRIAID_DATA_DIR="${TRIAID_BUILD_AUDIT_DATA_DIR:-/tmp/triaid-fin-v2-build-audit}"
export TRIAID_RUNTIME_ROLE=AUDIT
export TRIAID_PERSISTENCE_SCOPE=ISOLATED
export TRIAID_RUNTIME_READONLY=0
export TRIAID_WRITER_ACTIVATION_REQUIRED=0
unset TRIAID_WRITER_ACTIVATION_RECEIPT_PATH

# Build audits exercise the canonical automated research path. Runtime services
# may deliberately disable automation, but those flags must not weaken build
# regressions.
export TRIAID_DATA_AUTOMATION=1
export TRIAID_DECISION_AUTOMATION=1

rm -rf "$TRIAID_DATA_DIR"

# Prediction adjustment is a release gate because horizon leakage, incomplete
# bars, cost-unit mixing, or H1 promotion would invalidate prospective evidence.
python prediction_adjustment_smoke.py

exec python release_audit.py build
