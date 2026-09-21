from __future__ import annotations

import json
import os
import time
import uuid
import urllib.request
from pathlib import Path
from threading import RLock


class StorageBackendError(RuntimeError):
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
    version="supabase-storage-backend@0.1.0"

    def __init__(self)->None:
        self.endpoint=os.environ.get("TRIAID_SUPABASE_PERSISTENCE_URL","").strip()
        self.token=os.environ.get("TRIAID_SUPABASE_TOKEN","").strip()
        if not self.endpoint or not self.token:
            raise StorageBackendError("supabase_backend_missing_endpoint_or_token")
        self.root=Path("/remote/supabase")
        self._lock=RLock()
        ping=self._call({"action":"ping"})
        if not ping.get("ok"):
            raise StorageBackendError("supabase_backend_ping_failed")

    @property
    def persistent(self)->bool:
        return True

    @property
    def durability(self)->str:
        return "PERSISTENT"

    def _call(self,payload:dict,timeout:int=20)->dict:
        data=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        req=urllib.request.Request(
            self.endpoint,
            data=data,
            method="POST",
            headers={
                "content-type":"application/json",
                "x-triaid-token":self.token,
                "user-agent":"TRIAID-FIN-V2-STORAGE/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                raw=response.read().decode("utf-8")
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

    def atomic_write_text(self,name:str,text:str)->None:
        self._call({"action":"write_object","key":name,"content":text})

    def append_line(self,name:str,line:str)->None:
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

    def list_names(self,prefix:str,suffix:str="")->list[str]:
        result=self._call({
            "action":"list_objects",
            "prefix":prefix,
            "suffix":suffix,
        })
        return [str(x) for x in (result.get("keys") or [])]

    def list_texts(self,prefix:str,suffix:str="")->dict[str,str]:
        result=self._call({
            "action":"read_objects",
            "prefix":prefix,
            "suffix":suffix,
        })
        objects=result.get("objects") or []
        return {
            str(row.get("key")):str(row.get("content") or "")
            for row in objects
            if isinstance(row,dict) and row.get("key")
        }

    def status(self)->dict:
        return {
            "backend":"supabase",
            "version":self.version,
            "root":"supabase://triaid-persistence",
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
