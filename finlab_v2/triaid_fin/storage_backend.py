from __future__ import annotations

import json
import os
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from threading import RLock

from .persistence_policy import (
    current_runtime_persistence_policy,
    validate_runtime_persistence_policy,
    writer_activation_status,
    write_writer_activation_receipt,
)


class StorageBackendError(RuntimeError):
    pass


class StorageWriteFenceError(StorageBackendError):
    pass


def _decode_mount_path(value:str)->str:
    return (
        value.replace("\\040"," ")
        .replace("\\011","\t")
        .replace("\\012","\n")
        .replace("\\134","\\")
    )


def _read_mounts()->list[dict]:
    rows=[]
    try:
        raw=Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except Exception:
        return rows
    for line in raw.splitlines():
        try:
            left,right=line.split(" - ",1)
            lparts=left.split()
            rparts=right.split()
            if len(lparts)<6 or len(rparts)<2:
                continue
            rows.append({
                "mount_point":_decode_mount_path(lparts[4]),
                "fs_type":rparts[0],
                "source":rparts[1],
                "options":lparts[5],
            })
        except Exception:
            continue
    return rows


class FileStorageBackend:
    version="file-storage-backend@0.2.0"

    def __init__(self,root:str|None=None)->None:
        self.policy=current_runtime_persistence_policy()
        validate_runtime_persistence_policy(self.policy,"file")
        self.expected_volume_mount=Path(
            os.environ.get("TRIAID_VOLUME_MOUNT","/data")
        )
        preferred=root or os.environ.get("TRIAID_DATA_DIR")
        if preferred:
            self.root=Path(preferred)
        elif self.expected_volume_mount.exists():
            self.root=self.expected_volume_mount/"triaid_fin_v2"
        else:
            self.root=Path("./runtime_data")
        self.root.mkdir(parents=True,exist_ok=True)
        self._lock=RLock()
        self._probe=self._load_persistence_probe()

    def _mount_details(self)->dict:
        mounts=_read_mounts()
        expected=str(self.expected_volume_mount.resolve())
        resolved=str(self.root.resolve())
        expected_row=next(
            (row for row in mounts if row["mount_point"]==expected),
            None,
        )
        covering=[]
        for row in mounts:
            mount=row["mount_point"]
            if resolved==mount or resolved.startswith(mount.rstrip("/")+"/"):
                covering.append(row)
        covering.sort(key=lambda x:len(x["mount_point"]),reverse=True)
        active=covering[0] if covering else None
        return {
            "expected_mount":expected,
            "expected_mount_exists":self.expected_volume_mount.exists(),
            "expected_mount_is_mounted":expected_row is not None,
            "expected_mount_fs_type":expected_row.get("fs_type") if expected_row else None,
            "expected_mount_source":expected_row.get("source") if expected_row else None,
            "root_resolved":resolved,
            "root_mount_point":active.get("mount_point") if active else None,
            "root_fs_type":active.get("fs_type") if active else None,
            "root_mount_source":active.get("source") if active else None,
        }

    @property
    def persistent(self)->bool:
        details=self._mount_details()
        expected=details["expected_mount"]
        resolved=details["root_resolved"]
        return bool(
            details["expected_mount_is_mounted"]
            and (resolved==expected or resolved.startswith(expected.rstrip("/")+"/"))
        )

    @property
    def durability(self)->str:
        return "PERSISTENT" if self.persistent else "EPHEMERAL"

    def _load_persistence_probe(self)->dict:
        marker=self.root/".triaid_persistence_probe.json"
        deployment_id=os.environ.get("RAILWAY_DEPLOYMENT_ID")
        previous=None
        try:
            if marker.exists():
                previous=json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            previous=None
        previous_deployment=(previous or {}).get("deployment_id")
        confirmed=bool(
            (previous or {}).get("confirmed_across_deployments")
            or (
                deployment_id
                and previous_deployment
                and deployment_id!=previous_deployment
            )
        )
        return {
            "deployment_id":deployment_id,
            "previous_deployment_id":previous_deployment,
            "confirmed_across_deployments":confirmed,
            "marker_present":marker.exists(),
            "written_at":(previous or {}).get("written_at"),
        }

    def refresh_persistence_probe(self)->dict:
        marker=self.root/".triaid_persistence_probe.json"
        deployment_id=os.environ.get("RAILWAY_DEPLOYMENT_ID")
        previous=None
        try:
            if marker.exists():
                previous=json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            previous=None
        previous_deployment=(previous or {}).get("deployment_id")
        confirmed=bool(
            (previous or {}).get("confirmed_across_deployments")
            or (
                deployment_id
                and previous_deployment
                and deployment_id!=previous_deployment
            )
        )
        payload={
            "deployment_id":deployment_id,
            "instance_id":str(uuid.uuid4()),
            "written_at":time.time(),
            "previous_deployment_id":previous_deployment,
            "confirmed_across_deployments":confirmed,
        }
        try:
            marker.write_text(
                json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            payload["write_error"]=f"{type(exc).__name__}:{exc}"
        self._probe=self._load_persistence_probe()
        if payload.get("write_error"):
            self._probe["write_error"]=payload["write_error"]
        return dict(self._probe)

    def path(self,name:str)->Path:
        path=self.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        return path

    def exists(self,name:str)->bool:
        return self.path(name).exists()

    def atomic_write_text(self,name:str,text:str)->None:
        path=self.path(name)
        tmp=path.with_suffix(path.suffix+".tmp")
        with self._lock:
            tmp.write_text(text,encoding="utf-8")
            tmp.replace(path)

    def append_line(self,name:str,line:str)->None:
        path=self.path(name)
        with self._lock:
            with path.open("a",encoding="utf-8") as handle:
                handle.write(line+"\n")

    def read_text(self,name:str)->str:
        path=self.path(name)
        with self._lock:
            return path.read_text(encoding="utf-8")

    def read_lines(self,name:str,limit:int|None=None)->list[str]:
        path=self.path(name)
        if not path.exists():
            return []
        with self._lock:
            lines=path.read_text(encoding="utf-8").splitlines()
        if limit is not None and limit>0:
            lines=lines[-limit:]
        return lines

    def list_names(self,prefix:str,suffix:str="")->list[str]:
        base=self.path(prefix)
        if not base.exists():
            return []
        pattern=f"*{suffix}" if suffix else "*"
        with self._lock:
            return [
                str(path.relative_to(self.root)).replace("\\","/")
                for path in sorted(base.glob(pattern))
            ]

    def list_texts(self,prefix:str,suffix:str="")->dict[str,str]:
        out={}
        for name in self.list_names(prefix,suffix):
            try:
                out[name]=self.read_text(name)
            except Exception:
                continue
        return out

    def list_files(self,prefix:str,suffix:str="")->list[Path]:
        return [self.root/name for name in self.list_names(prefix,suffix)]

    def status(self)->dict:
        mount=self._mount_details()
        return {
            "backend":"file",
            "version":self.version,
            "persistence_policy":self.policy.status(),
            "mutation_allowed":True,
            "mutation_scope":"LOCAL_OR_ISOLATED",
            "root":str(self.root),
            "durability":self.durability,
            "persistent":self.persistent,
            "volume":mount,
            "persistence_probe":{
                "deployment_id":self._probe.get("deployment_id"),
                "previous_deployment_id":self._probe.get("previous_deployment_id"),
                "confirmed_across_deployments":bool(
                    self._probe.get("confirmed_across_deployments")
                ),
                "marker_present":bool(self._probe.get("marker_present")),
                "written_at":self._probe.get("written_at"),
                "write_error":self._probe.get("write_error"),
            },
        }



class SupabaseStorageBackend:
    version="supabase-storage-backend@0.4.0"
    persistence_contract="triaid-persistence-contract@1.0.0"

    def __init__(self)->None:
        self.endpoint=os.environ.get("TRIAID_SUPABASE_PERSISTENCE_URL","").strip()
        self.token=os.environ.get("TRIAID_SUPABASE_TOKEN","").strip()
        self.policy=current_runtime_persistence_policy()
        validate_runtime_persistence_policy(self.policy,"supabase")
        self.runtime_id=self.policy.runtime_id
        self.runtime_role=self.policy.runtime_role
        self.persistence_scope=self.policy.persistence_scope
        self.service_id=self.policy.service_id
        self.deployment_id=self.policy.deployment_id
        self.commit_sha=self.policy.commit_sha
        if not self.endpoint or not self.token:
            raise StorageBackendError("supabase_backend_missing_endpoint_or_token")
        self.root=Path("/remote/supabase")
        self._lock=RLock()
        ping=self._call({"action":"ping"})
        if not ping.get("ok"):
            raise StorageBackendError("supabase_backend_ping_failed")
        self.remote_contract_version=str(ping.get("contract_version") or "")
        self.remote_active_writer=ping.get("active_writer")
        if self.remote_contract_version!=self.persistence_contract:
            raise StorageBackendError(
                "supabase_persistence_contract_mismatch:"
                f"expected={self.persistence_contract}:actual={self.remote_contract_version or 'missing'}"
            )

    @property
    def persistent(self)->bool:
        return True

    @property
    def durability(self)->str:
        return "PERSISTENT"

    def _call(self,payload:dict,timeout:int|None=None)->dict:
        if timeout is None:
            try:
                timeout=int(os.environ.get("TRIAID_SUPABASE_TIMEOUT_SECONDS","20"))
            except Exception:
                timeout=20
        timeout=max(1,min(int(timeout),120))
        data=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        req=urllib.request.Request(
            self.endpoint,
            data=data,
            method="POST",
            headers={
                "content-type":"application/json",
                "x-triaid-token":self.token,
                **({"x-triaid-runtime-id":self.runtime_id} if self.runtime_id else {}),
                **({"x-triaid-runtime-role":self.runtime_role} if self.runtime_role else {}),
                **({"x-triaid-persistence-scope":self.persistence_scope} if self.persistence_scope else {}),
                **({"x-triaid-service-id":self.service_id} if self.service_id else {}),
                **({"x-triaid-deployment-id":self.deployment_id} if self.deployment_id else {}),
                **({"x-triaid-commit-sha":self.commit_sha} if self.commit_sha else {}),
                "user-agent":"TRIAID-FIN-V2-STORAGE/0.4",
            },
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                raw=response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                detail=exc.read().decode("utf-8","replace")
            except Exception:
                detail=""
            raise StorageBackendError(
                f"supabase_http_error:{exc.code}:{detail or exc.reason}"
            ) from exc
        except Exception as exc:
            raise StorageBackendError(
                f"supabase_call_failed:{type(exc).__name__}:{exc}"
            ) from exc
        try:
            result=json.loads(raw)
        except Exception as exc:
            raise StorageBackendError("supabase_invalid_json") from exc
        if isinstance(result,dict) and result.get("error"):
            raise StorageBackendError(f"supabase_error:{result['error']}")
        return result if isinstance(result,dict) else {}

    def path(self,name:str)->Path:
        # Compatibility only. Remote storage callers should use backend methods.
        return self.root/name

    def exists(self,name:str)->bool:
        return bool(self._call({"action":"exists_object","key":name}).get("exists"))

    def _require_mutation(self,action:str,name:str)->None:
        if not self.policy.official_write_authorized:
            raise StorageWriteFenceError(
                "official_persistence_write_fenced:"
                f"role={self.runtime_role}:scope={self.persistence_scope}:"
                f"action={action}:key={name}"
            )
        activation=writer_activation_status(self.policy)
        if self.policy.requires_writer_activation and not activation.get("ready"):
            raise StorageWriteFenceError(
                "official_writer_not_activated:"
                f"deployment_id={self.deployment_id}:action={action}:key={name}"
            )

    def activate_official_writer(self)->dict:
        if not self.policy.official_write_authorized:
            raise StorageWriteFenceError(
                "writer_activation_requires_production_official_role"
            )
        if not self.deployment_id:
            raise StorageWriteFenceError("writer_activation_requires_deployment_id")
        result=self._call({"action":"activate_writer"})
        active=result.get("active_writer") or {}
        if (
            str(active.get("runtime_id") or "")!=self.runtime_id
            or str(active.get("service_id") or "")!=self.service_id
            or str(active.get("deployment_id") or "")!=self.deployment_id
        ):
            raise StorageBackendError(f"writer_activation_identity_mismatch:{active}")
        return write_writer_activation_receipt(active)

    def writer_status(self)->dict:
        return self._call({"action":"writer_status"})

    def atomic_write_text(self,name:str,text:str)->None:
        self._require_mutation("write_object",name)
        self._call({"action":"write_object","key":name,"content":text})

    def append_line(self,name:str,line:str)->None:
        self._require_mutation("append_stream",name)
        self._call({"action":"append_stream","key":name,"line":line})

    def read_text(self,name:str)->str:
        result=self._call({"action":"read_object","key":name})
        if not result.get("found"):
            raise FileNotFoundError(name)
        return str(result.get("content") or "")

    def read_lines(self,name:str,limit:int|None=None)->list[str]:
        payload={"action":"read_stream","key":name}
        if limit is not None:
            payload["limit"]=int(limit)
        result=self._call(payload)
        lines=result.get("lines") or []
        return [str(x) for x in lines]

    def _paged_objects(
        self,
        action:str,
        prefix:str,
        suffix:str="",
        *,
        page_limit:int,
    )->list:
        cursor=""
        seen_cursors:set[str]=set()
        rows:list=[]
        while True:
            payload={
                "action":action,
                "prefix":prefix,
                "suffix":suffix,
                "limit":int(page_limit),
            }
            if cursor:
                payload["cursor"]=cursor
            result=self._call(payload)
            page=(
                result.get("keys")
                if action=="list_objects"
                else result.get("objects")
            ) or []
            rows.extend(page)
            next_cursor=str(result.get("next_cursor") or "")
            if not next_cursor:
                break
            if next_cursor==cursor or next_cursor in seen_cursors:
                raise StorageBackendError(
                    f"supabase_pagination_cursor_loop:action={action}:cursor={next_cursor}"
                )
            seen_cursors.add(next_cursor)
            cursor=next_cursor
        return rows

    def list_names(self,prefix:str,suffix:str="")->list[str]:
        rows=self._paged_objects(
            "list_objects",
            prefix,
            suffix,
            page_limit=100,
        )
        return [str(x) for x in rows]

    def list_texts(self,prefix:str,suffix:str="")->dict[str,str]:
        objects=self._paged_objects(
            "read_objects",
            prefix,
            suffix,
            page_limit=20,
        )
        return {
            str(row.get("key")):str(row.get("content") or "")
            for row in objects
            if isinstance(row,dict) and row.get("key")
        }

    def status(self)->dict:
        activation=writer_activation_status(self.policy)
        return {
            "backend":"supabase",
            "version":self.version,
            "persistence_contract":self.remote_contract_version,
            "root":"supabase://triaid-persistence",
            "runtime_id":self.runtime_id or None,
            "runtime_role":self.runtime_role,
            "persistence_scope":self.persistence_scope,
            "service_id":self.service_id or None,
            "deployment_id":self.deployment_id or None,
            "persistence_policy":self.policy.status(),
            "mutation_allowed":bool(
                self.policy.official_write_authorized
                and (
                    not self.policy.requires_writer_activation
                    or activation.get("ready")
                )
            ),
            "writer_activation":activation,
            "durability":"PERSISTENT",
            "persistent":True,
            "endpoint_configured":bool(self.endpoint),
            "token_configured":bool(self.token),
            "persistence_probe":{
                "confirmed_across_deployments":True,
                "method":"external_postgres_backend",
            },
        }

def build_storage_backend(root:str|None=None):
    backend=os.environ.get("TRIAID_STORAGE_BACKEND","file").strip().lower() or "file"
    if backend=="file":
        return FileStorageBackend(root)
    if backend=="supabase":
        return SupabaseStorageBackend()
    raise StorageBackendError(
        f"unsupported_storage_backend:{backend}. "
        "Supported backends: file, supabase."
    )
