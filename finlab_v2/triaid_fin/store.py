from __future__ import annotations

import json
from threading import RLock

from .contracts import RunRecord
from .storage_backend import build_storage_backend


class RunStore:
    version = "run-store@0.3.0"

    def __init__(self, root: str | None = None) -> None:
        self.backend=build_storage_backend(root)
        self.root=self.backend.root
        self.runs_dir=self.backend.path("runs")
        self.runs_dir.mkdir(parents=True,exist_ok=True)
        self._lock=RLock()

    @property
    def persistent_mount_detected(self) -> bool:
        return bool(self.backend.persistent)

    def save_run(self, run: RunRecord) -> None:
        payload = run.model_dump(mode="json")
        self.backend.atomic_write_text(
            f"runs/{run.run_id}.json",
            json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2),
        )

    def load_run(self, run_id: str) -> RunRecord:
        return RunRecord.model_validate_json(
            self.backend.read_text(f"runs/{run_id}.json")
        )

    def list_runs(self) -> list[RunRecord]:
        rows=[]
        for path in self.backend.list_files("runs",".json"):
            try:
                rows.append(RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return sorted(rows, key=lambda r: r.created_at)

    def save_json(self, name: str, payload: dict) -> None:
        self.backend.atomic_write_text(
            name,
            json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2),
        )

    def load_json(self, name: str, default: dict | None = None) -> dict:
        if not self.backend.exists(name):
            return {} if default is None else default
        try:
            return json.loads(self.backend.read_text(name))
        except Exception:
            return {} if default is None else default

    def append_jsonl(self, name: str, payload: dict) -> None:
        line=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        self.backend.append_line(name,line)

    def read_jsonl(self, name: str, limit: int | None = None) -> list[dict]:
        if not self.backend.exists(name):
            return []
        rows=[]
        try:
            lines=self.backend.read_text(name).splitlines()
        except Exception:
            return []
        if limit is not None and limit>0:
            lines=lines[-limit:]
        for line in lines:
            try:
                row=json.loads(line)
                if isinstance(row,dict):
                    rows.append(row)
            except Exception:
                continue
        return rows

    def status(self) -> dict:
        return {
            "version":self.version,
            "root":str(self.root),
            "persistent_mount_detected":self.persistent_mount_detected,
            "durability":self.backend.durability,
            "backend":self.backend.status(),
            "run_count":len(self.list_runs()),
        }
