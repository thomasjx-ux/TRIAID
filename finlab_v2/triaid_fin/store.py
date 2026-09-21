from __future__ import annotations

import json
from threading import RLock

from .contracts import RunRecord
from .storage_backend import build_storage_backend


class RunStore:
    version = "run-store@0.4.0"

    def __init__(self, root: str | None = None) -> None:
        self.backend=build_storage_backend(root)
        self.root=self.backend.root
        self.runs_dir=self.backend.path("runs")
        if getattr(self.backend,"status",lambda:{})().get("backend")=="file":
            self.runs_dir.mkdir(parents=True,exist_ok=True)
        self._lock=RLock()
        self.integrity_errors:list[str]=[]

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
        errors=[]
        for name,content in self.backend.list_texts("runs/",".json").items():
            try:
                rows.append(RunRecord.model_validate_json(content))
            except Exception as exc:
                errors.append(f"run_parse_error:{name}:{type(exc).__name__}:{exc}")
        self.integrity_errors=[x for x in self.integrity_errors if not x.startswith("run_parse_error:")]
        self.integrity_errors.extend(errors)
        return sorted(rows,key=lambda r:r.created_at)

    def save_json(self, name: str, payload: dict) -> None:
        self.backend.atomic_write_text(
            name,
            json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2),
        )

    def load_json(self, name: str, default: dict | None = None) -> dict:
        if not self.backend.exists(name):
            return {} if default is None else default
        try:
            value=json.loads(self.backend.read_text(name))
        except Exception as exc:
            message=f"json_state_corrupt:{name}:{type(exc).__name__}:{exc}"
            self.integrity_errors.append(message)
            raise RuntimeError(message) from exc
        if not isinstance(value,dict):
            message=f"json_state_invalid_type:{name}:{type(value).__name__}"
            self.integrity_errors.append(message)
            raise RuntimeError(message)
        return value

    def append_jsonl(self, name: str, payload: dict) -> None:
        line=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        self.backend.append_line(name,line)

    def read_jsonl(self, name: str, limit: int | None = None) -> list[dict]:
        rows=[]
        try:
            lines=self.backend.read_lines(name,limit)
        except Exception as exc:
            message=f"jsonl_read_failed:{name}:{type(exc).__name__}:{exc}"
            self.integrity_errors.append(message)
            raise RuntimeError(message) from exc
        parse_errors=[]
        for index,line in enumerate(lines):
            try:
                row=json.loads(line)
                if isinstance(row,dict):
                    rows.append(row)
                else:
                    parse_errors.append(f"jsonl_invalid_type:{name}:{index}")
            except Exception as exc:
                parse_errors.append(f"jsonl_parse_error:{name}:{index}:{type(exc).__name__}")
        self.integrity_errors=[x for x in self.integrity_errors if not (x.startswith(f"jsonl_parse_error:{name}:") or x.startswith(f"jsonl_invalid_type:{name}:"))]
        self.integrity_errors.extend(parse_errors)
        return rows

    def status(self) -> dict:
        return {
            "version":self.version,
            "root":str(self.root),
            "persistent_mount_detected":self.persistent_mount_detected,
            "durability":self.backend.durability,
            "backend":self.backend.status(),
            "run_count":len(self.list_runs()),
            "integrity_errors":list(self.integrity_errors[-100:]),
            "integrity_ok":not bool(self.integrity_errors),
        }
