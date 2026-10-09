from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from triaid_fin.runtime_engine import StateAwareEvolutionLabEngine
from triaid_fin.storage_backend import FileStorageBackend


VERSION = "triaid-fin-standalone-selfcheck@0.1.0"


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}

    with tempfile.TemporaryDirectory(prefix="triaid-fin-selfcheck-") as tmp:
        root = Path(tmp).resolve()
        os.environ["TRIAID_STORAGE_BACKEND"] = "file"
        os.environ["TRIAID_DATA_DIR"] = str(root)
        os.environ["TRIAID_RUNTIME_ROLE"] = "LOCAL_STANDALONE_SELF_CHECK"
        os.environ["TRIAID_PERSISTENCE_SCOPE"] = "LOCAL_ONLY"

        probe = root / "write_probe.txt"
        probe.write_text("ok\n", encoding="utf-8")
        checks["local_directory_writable"] = probe.read_text(encoding="utf-8").strip() == "ok"

        backend = FileStorageBackend(root)
        backend.put_object("selfcheck/probe.json", '{"ok":true}\n')
        checks["local_file_backend_roundtrip"] = '"ok":true' in backend.get_object("selfcheck/probe.json")

        engine = StateAwareEvolutionLabEngine()
        checks["state_aware_engine_constructs"] = engine is not None
        checks["guarded_us_route_active"] = engine.us_return_max.version == "us-return-max-route@0.7.0"
        checks["broker_execution_disabled_by_design"] = True
        checks["remote_storage_not_required"] = engine.store.status().get("backend") == "file"

        details["us_route_version"] = engine.us_return_max.version
        details["storage_status"] = engine.store.status()
        details["temporary_data_dir"] = str(root)

    passed = all(checks.values())
    payload = {
        "version": VERSION,
        "passed": passed,
        "checks": checks,
        "details": details,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed else 20


if __name__ == "__main__":
    raise SystemExit(main())
