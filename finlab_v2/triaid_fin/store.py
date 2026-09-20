from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterable

from .contracts import RunRecord


class RunStore:
    version = "run-store@0.2.0"

    def __init__(self, root: str | None = None) -> None:
        preferred = root or os.environ.get("TRIAID_DATA_DIR")
        if preferred:
            self.root = Path(preferred)
        elif Path("/data").exists():
            self.root = Path("/data/triaid_fin_v2")
        else:
            self.root = Path("./runtime_data")
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @property
    def persistent_mount_detected(self) -> bool:
        try:
            return str(self.root.resolve()).startswith("/data/")
        except Exception:
            return False

    def save_run(self, run: RunRecord) -> None:
        payload = run.model_dump(mode="json")
        path = self.runs_dir / f"{run.run_id}.json"
        tmp = path.with_suffix(".tmp")
        with self._lock:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            tmp.replace(path)

    def load_run(self, run_id: str) -> RunRecord:
        path = self.runs_dir / f"{run_id}.json"
        with self._lock:
            return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[RunRecord]:
        rows=[]
        with self._lock:
            for path in sorted(self.runs_dir.glob("*.json")):
                try:
                    rows.append(RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
                except Exception:
                    continue
        return sorted(rows, key=lambda r: r.created_at)

    def save_json(self, name: str, payload: dict) -> None:
        path = self.root / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        with self._lock:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            tmp.replace(path)

    def load_json(self, name: str, default: dict | None = None) -> dict:
        path = self.root / name
        if not path.exists():
            return {} if default is None else default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {} if default is None else default

    def append_jsonl(self, name: str, payload: dict) -> None:
        path = self.root / name
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read_jsonl(self, name: str, limit: int | None = None) -> list[dict]:
        path = self.root / name
        if not path.exists():
            return []
        rows=[]
        with self._lock:
            try:
                lines=path.read_text(encoding="utf-8").splitlines()
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
            "version": self.version,
            "root": str(self.root),
            "persistent_mount_detected": self.persistent_mount_detected,
            "run_count": len(self.list_runs()),
        }
