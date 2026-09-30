from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


VALID_RUNTIME_ROLES=frozenset({"PRODUCTION","SHADOW","CANDIDATE","AUDIT","LOCAL"})
VALID_PERSISTENCE_SCOPES=frozenset({"OFFICIAL","ISOLATED","LOCAL"})
WRITER_ACTIVATION_CONTRACT="triaid-writer-activation@1.0.0"


def env_flag(name:str,default:bool=False)->bool:
    raw=os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1","true","on","yes"}


@dataclass(frozen=True)
class RuntimePersistencePolicy:
    runtime_role:str
    persistence_scope:str
    runtime_read_only:bool
    railway_runtime:bool
    role_explicit:bool
    scope_explicit:bool
    writer_activation_required:bool
    runtime_id:str
    service_id:str
    deployment_id:str
    commit_sha:str

    @property
    def official_write_authorized(self)->bool:
        return bool(
            self.runtime_role=="PRODUCTION"
            and self.persistence_scope=="OFFICIAL"
            and not self.runtime_read_only
        )

    @property
    def requires_writer_activation(self)->bool:
        return bool(
            self.writer_activation_required
            and self.official_write_authorized
            and self.railway_runtime
        )

    def status(self)->dict:
        return {
            "contract":WRITER_ACTIVATION_CONTRACT,
            "runtime_role":self.runtime_role,
            "persistence_scope":self.persistence_scope,
            "runtime_read_only":self.runtime_read_only,
            "railway_runtime":self.railway_runtime,
            "role_explicit":self.role_explicit,
            "scope_explicit":self.scope_explicit,
            "official_write_authorized":self.official_write_authorized,
            "writer_activation_required":self.requires_writer_activation,
            "runtime_id":self.runtime_id or None,
            "service_id":self.service_id or None,
            "deployment_id":self.deployment_id or None,
            "commit_sha":self.commit_sha or None,
        }


def current_runtime_persistence_policy()->RuntimePersistencePolicy:
    raw_role=(os.getenv("TRIAID_RUNTIME_ROLE") or "").strip().upper()
    raw_scope=(os.getenv("TRIAID_PERSISTENCE_SCOPE") or "").strip().upper()
    railway_runtime=bool(
        os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_SERVICE_ID")
        or os.getenv("RAILWAY_DEPLOYMENT_ID")
    )
    runtime_read_only=env_flag("TRIAID_RUNTIME_READONLY",False)

    if raw_role:
        role=raw_role
    elif railway_runtime:
        role="UNDECLARED"
    elif runtime_read_only:
        role="SHADOW"
    else:
        role="LOCAL"

    if raw_scope:
        scope=raw_scope
    elif railway_runtime:
        scope="UNDECLARED"
    else:
        scope="LOCAL"

    return RuntimePersistencePolicy(
        runtime_role=role,
        persistence_scope=scope,
        runtime_read_only=runtime_read_only,
        railway_runtime=railway_runtime,
        role_explicit=bool(raw_role),
        scope_explicit=bool(raw_scope),
        writer_activation_required=env_flag("TRIAID_WRITER_ACTIVATION_REQUIRED",False),
        runtime_id=(os.getenv("TRIAID_RUNTIME_ID") or "").strip(),
        service_id=(os.getenv("RAILWAY_SERVICE_ID") or "").strip(),
        deployment_id=(os.getenv("RAILWAY_DEPLOYMENT_ID") or "").strip(),
        commit_sha=(os.getenv("RAILWAY_GIT_COMMIT_SHA") or "").strip(),
    )


def validate_runtime_persistence_policy(policy:RuntimePersistencePolicy,backend_name:str)->None:
    backend=str(backend_name or "").strip().lower()
    if policy.runtime_role not in VALID_RUNTIME_ROLES:
        raise RuntimeError(f"invalid_runtime_role:{policy.runtime_role}")
    if policy.persistence_scope not in VALID_PERSISTENCE_SCOPES:
        raise RuntimeError(f"invalid_persistence_scope:{policy.persistence_scope}")

    if backend=="supabase" and policy.railway_runtime:
        if not policy.role_explicit:
            raise RuntimeError("railway_supabase_requires_explicit_runtime_role")
        if not policy.scope_explicit:
            raise RuntimeError("railway_supabase_requires_explicit_persistence_scope")
        if policy.runtime_role=="PRODUCTION":
            if policy.persistence_scope!="OFFICIAL":
                raise RuntimeError("production_supabase_requires_official_scope")
            if policy.runtime_read_only:
                raise RuntimeError("production_supabase_cannot_be_runtime_read_only")
            if not policy.runtime_id:
                raise RuntimeError("production_supabase_requires_runtime_id")
            if not policy.service_id:
                raise RuntimeError("production_supabase_requires_service_id")
            if not policy.deployment_id:
                raise RuntimeError("production_supabase_requires_deployment_id")
        else:
            if not policy.runtime_read_only:
                raise RuntimeError(
                    f"nonproduction_supabase_requires_runtime_read_only:{policy.runtime_role}"
                )


def writer_activation_receipt_path()->Path:
    return Path(
        os.getenv(
            "TRIAID_WRITER_ACTIVATION_RECEIPT_PATH",
            "/tmp/triaid_writer_activation.json",
        )
    )


def writer_activation_status(
    policy:RuntimePersistencePolicy|None=None,
)->dict:
    policy=policy or current_runtime_persistence_policy()
    base={
        "contract":WRITER_ACTIVATION_CONTRACT,
        "required":policy.requires_writer_activation,
        "ready":True,
        "state":"NOT_REQUIRED",
        "runtime_role":policy.runtime_role,
        "persistence_scope":policy.persistence_scope,
        "deployment_id":policy.deployment_id or None,
        "receipt_path":str(writer_activation_receipt_path()),
    }
    if not policy.requires_writer_activation:
        return base

    path=writer_activation_receipt_path()
    if not path.exists():
        return {
            **base,
            "ready":False,
            "state":"PENDING",
        }
    try:
        receipt=json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            **base,
            "ready":False,
            "state":"INVALID_RECEIPT",
            "error":f"{type(exc).__name__}:{exc}",
        }
    active=receipt.get("active_writer") or {}
    matches=bool(
        receipt.get("contract")==WRITER_ACTIVATION_CONTRACT
        and receipt.get("passed") is True
        and str(active.get("runtime_id") or "")==policy.runtime_id
        and str(active.get("service_id") or "")==policy.service_id
        and str(active.get("deployment_id") or "")==policy.deployment_id
    )
    return {
        **base,
        "ready":matches,
        "state":"ACTIVE" if matches else "MISMATCH",
        "active_writer":{
            "runtime_id":active.get("runtime_id"),
            "service_id":active.get("service_id"),
            "deployment_id":active.get("deployment_id"),
            "commit_sha":active.get("commit_sha"),
            "activated_at":active.get("activated_at"),
        },
    }


def write_writer_activation_receipt(active_writer:dict)->dict:
    policy=current_runtime_persistence_policy()
    payload={
        "contract":WRITER_ACTIVATION_CONTRACT,
        "passed":True,
        "active_writer":active_writer,
    }
    path=writer_activation_receipt_path()
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(
        json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    status=writer_activation_status(policy)
    if not status.get("ready"):
        raise RuntimeError(f"writer_activation_receipt_mismatch:{status}")
    return status
