from __future__ import annotations

import os
import tempfile
from pathlib import Path

from triaid_fin.persistence_policy import (
    current_runtime_persistence_policy,
    validate_runtime_persistence_policy,
)
from triaid_fin.storage_backend import StorageWriteFenceError, SupabaseStorageBackend


KEYS=(
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
    "RAILWAY_DEPLOYMENT_ID",
    "RAILWAY_GIT_COMMIT_SHA",
    "TRIAID_RUNTIME_ROLE",
    "TRIAID_PERSISTENCE_SCOPE",
    "TRIAID_RUNTIME_READONLY",
    "TRIAID_RUNTIME_ID",
    "TRIAID_WRITER_ACTIVATION_REQUIRED",
    "TRIAID_WRITER_ACTIVATION_RECEIPT_PATH",
)
original={key:os.environ.get(key) for key in KEYS}


def restore()->None:
    for key,value in original.items():
        if value is None:
            os.environ.pop(key,None)
        else:
            os.environ[key]=value


try:
    with tempfile.TemporaryDirectory(prefix="triaid-policy-smoke-") as root:
        receipt=str(Path(root)/"writer.json")

        os.environ.update({
            "RAILWAY_PROJECT_ID":"project",
            "RAILWAY_SERVICE_ID":"shadow-service",
            "RAILWAY_DEPLOYMENT_ID":"shadow-deploy",
            "RAILWAY_GIT_COMMIT_SHA":"shadow-sha",
            "TRIAID_RUNTIME_ROLE":"SHADOW",
            "TRIAID_PERSISTENCE_SCOPE":"OFFICIAL",
            "TRIAID_RUNTIME_READONLY":"1",
            "TRIAID_RUNTIME_ID":"shadow-runtime",
            "TRIAID_WRITER_ACTIVATION_REQUIRED":"0",
            "TRIAID_WRITER_ACTIVATION_RECEIPT_PATH":receipt,
        })
        shadow=current_runtime_persistence_policy()
        validate_runtime_persistence_policy(shadow,"supabase")
        assert shadow.official_write_authorized is False
        backend=object.__new__(SupabaseStorageBackend)
        backend.policy=shadow
        backend.runtime_role=shadow.runtime_role
        backend.persistence_scope=shadow.persistence_scope
        backend.deployment_id=shadow.deployment_id
        try:
            backend._require_mutation("append_stream","market_observations.jsonl")
        except StorageWriteFenceError as exc:
            assert "official_persistence_write_fenced" in str(exc)
        else:
            raise AssertionError("shadow mutation was not fenced")

        os.environ.update({
            "RAILWAY_SERVICE_ID":"production-service",
            "RAILWAY_DEPLOYMENT_ID":"production-deploy",
            "RAILWAY_GIT_COMMIT_SHA":"production-sha",
            "TRIAID_RUNTIME_ROLE":"PRODUCTION",
            "TRIAID_PERSISTENCE_SCOPE":"OFFICIAL",
            "TRIAID_RUNTIME_READONLY":"0",
            "TRIAID_RUNTIME_ID":"production-runtime",
            "TRIAID_WRITER_ACTIVATION_REQUIRED":"1",
            "TRIAID_WRITER_ACTIVATION_RECEIPT_PATH":receipt,
        })
        production=current_runtime_persistence_policy()
        validate_runtime_persistence_policy(production,"supabase")
        assert production.official_write_authorized is True
        assert production.requires_writer_activation is True
        backend=object.__new__(SupabaseStorageBackend)
        backend.policy=production
        backend.runtime_role=production.runtime_role
        backend.persistence_scope=production.persistence_scope
        backend.deployment_id=production.deployment_id
        try:
            backend._require_mutation("write_object","decision_scheduler_state.json")
        except StorageWriteFenceError as exc:
            assert "official_writer_not_activated" in str(exc)
        else:
            raise AssertionError("pre-activation production mutation was not fenced")

        os.environ.update({
            "RAILWAY_SERVICE_ID":"candidate-service",
            "RAILWAY_DEPLOYMENT_ID":"candidate-deploy",
            "TRIAID_RUNTIME_ROLE":"CANDIDATE",
            "TRIAID_PERSISTENCE_SCOPE":"OFFICIAL",
            "TRIAID_RUNTIME_READONLY":"0",
            "TRIAID_RUNTIME_ID":"candidate-runtime",
            "TRIAID_WRITER_ACTIVATION_REQUIRED":"0",
        })
        candidate=current_runtime_persistence_policy()
        try:
            validate_runtime_persistence_policy(candidate,"supabase")
        except RuntimeError as exc:
            assert "nonproduction_supabase_requires_runtime_read_only" in str(exc)
        else:
            raise AssertionError("writable candidate role was accepted")

        os.environ.pop("TRIAID_RUNTIME_ROLE",None)
        undeclared=current_runtime_persistence_policy()
        try:
            validate_runtime_persistence_policy(undeclared,"supabase")
        except RuntimeError as exc:
            assert "invalid_runtime_role" in str(exc) or "explicit_runtime_role" in str(exc)
        else:
            raise AssertionError("undeclared Railway Supabase role was accepted")
finally:
    restore()

print("TRIAID_PERSISTENCE_ROLE_CONTRACT_SMOKE_PASS")
