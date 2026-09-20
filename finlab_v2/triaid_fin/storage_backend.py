from __future__ import annotations

import json
import os
import time
import uuid
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
        self._probe=self._update_persistence_probe()

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

    def _update_persistence_probe(self)->dict:
        marker=self.root/".triaid_persistence_probe.json"
        deployment_id=os.environ.get("RAILWAY_DEPLOYMENT_ID")
        instance_id=str(uuid.uuid4())
        previous=None
        try:
            if marker.exists():
                previous=json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            previous=None

        previous_deployment=(previous or {}).get("deployment_id")
        confirmed=bool(
            deployment_id
            and previous_deployment
            and deployment_id!=previous_deployment
        )
        payload={
            "deployment_id":deployment_id,
            "instance_id":instance_id,
            "written_at":time.time(),
            "previous_deployment_id":previous_deployment,
            "confirmed_across_deployments":confirmed
            or bool((previous or {}).get("confirmed_across_deployments")),
        }
        try:
            marker.write_text(
                json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            payload["write_error"]=f"{type(exc).__name__}:{exc}"
        return payload

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

    def list_files(self,prefix:str,suffix:str="")->list[Path]:
        base=self.path(prefix)
        if not base.exists():
            return []
        pattern=f"*{suffix}" if suffix else "*"
        with self._lock:
            return sorted(base.glob(pattern))

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
                "write_error":self._probe.get("write_error"),
            },
        }


def build_storage_backend(root:str|None=None):
    backend=os.environ.get("TRIAID_STORAGE_BACKEND","file").strip().lower() or "file"
    if backend=="file":
        return FileStorageBackend(root)
    raise StorageBackendError(
        f"unsupported_storage_backend:{backend}. "
        "Supported now: file. Future backends can implement the same contract."
    )
