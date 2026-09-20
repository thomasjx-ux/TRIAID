from __future__ import annotations

import os
from pathlib import Path
from threading import RLock


class StorageBackendError(RuntimeError):
    pass


class FileStorageBackend:
    version="file-storage-backend@0.1.0"

    def __init__(self,root:str|None=None)->None:
        preferred=root or os.environ.get("TRIAID_DATA_DIR")
        if preferred:
            self.root=Path(preferred)
        elif Path("/data").exists():
            self.root=Path("/data/triaid_fin_v2")
        else:
            self.root=Path("./runtime_data")
        self.root.mkdir(parents=True,exist_ok=True)
        self._lock=RLock()

    @property
    def persistent(self)->bool:
        try:
            return str(self.root.resolve()).startswith("/data/")
        except Exception:
            return False

    @property
    def durability(self)->str:
        return "PERSISTENT" if self.persistent else "EPHEMERAL"

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
        return {
            "backend":"file",
            "version":self.version,
            "root":str(self.root),
            "durability":self.durability,
            "persistent":self.persistent,
        }


def build_storage_backend(root:str|None=None):
    backend=os.environ.get("TRIAID_STORAGE_BACKEND","file").strip().lower() or "file"
    if backend=="file":
        return FileStorageBackend(root)
    raise StorageBackendError(
        f"unsupported_storage_backend:{backend}. "
        "Supported now: file. Future backends can implement the same contract."
    )
