#!/bin/sh
set -eu

# Build-time audits must never read or mutate production persistence even when
# the Railway service is configured for Supabase at runtime.
export TRIAID_STORAGE_BACKEND=file
export TRIAID_DATA_DIR="${TRIAID_BUILD_AUDIT_DATA_DIR:-/tmp/triaid-fin-v2-build-audit}"
rm -rf "$TRIAID_DATA_DIR"

exec python release_audit.py build
