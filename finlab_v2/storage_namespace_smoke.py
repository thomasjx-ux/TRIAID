from __future__ import annotations

import os

from triaid_fin.storage_backend import SupabaseStorageBackend


class MemorySupabase(SupabaseStorageBackend):
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}
        self.streams: dict[str, list[str]] = {}
        super().__init__()

    def _call(self, payload: dict, timeout: int = 20) -> dict:
        action = payload.get("action")
        key = str(payload.get("key") or "")
        if action == "ping":
            return {"ok": True}
        if action == "exists_object":
            return {"exists": key in self.objects}
        if action == "write_object":
            self.objects[key] = str(payload.get("content") or "")
            return {"ok": True}
        if action == "read_object":
            if key not in self.objects:
                return {"found": False}
            return {"found": True, "content": self.objects[key]}
        if action == "append_stream":
            self.streams.setdefault(key, []).append(str(payload.get("line") or ""))
            return {"ok": True}
        if action == "read_stream":
            lines = list(self.streams.get(key, []))
            limit = payload.get("limit")
            if limit is not None:
                lines = lines[-int(limit):]
            return {"lines": lines}
        if action == "list_objects":
            prefix = str(payload.get("prefix") or "")
            suffix = str(payload.get("suffix") or "")
            keys = sorted(k for k in self.objects if k.startswith(prefix) and k.endswith(suffix))
            return {"keys": keys}
        if action == "read_objects":
            prefix = str(payload.get("prefix") or "")
            suffix = str(payload.get("suffix") or "")
            return {
                "objects": [
                    {"key": k, "content": self.objects[k]}
                    for k in sorted(self.objects)
                    if k.startswith(prefix) and k.endswith(suffix)
                ]
            }
        raise AssertionError(f"unexpected action: {action}")


def main() -> None:
    before = {
        name: os.environ.get(name)
        for name in (
            "TRIAID_SUPABASE_PERSISTENCE_URL",
            "TRIAID_SUPABASE_TOKEN",
            "TRIAID_STORAGE_NAMESPACE",
        )
    }
    try:
        os.environ["TRIAID_SUPABASE_PERSISTENCE_URL"] = "https://example.invalid/storage"
        os.environ["TRIAID_SUPABASE_TOKEN"] = "test-token"
        os.environ["TRIAID_STORAGE_NAMESPACE"] = "gcp-shadow"

        backend = MemorySupabase()
        backend.atomic_write_text("runs/a.json", "{}")
        backend.append_line("events.jsonl", "{\"event\":1}")

        assert "gcp-shadow/runs/a.json" in backend.objects
        assert "runs/a.json" not in backend.objects
        assert backend.exists("runs/a.json")
        assert backend.read_text("runs/a.json") == "{}"
        assert backend.list_names("runs/", ".json") == ["runs/a.json"]
        assert backend.list_texts("runs/", ".json") == {"runs/a.json": "{}"}
        assert backend.read_lines("events.jsonl") == ['{"event":1}']

        status = backend.status()
        assert status["namespace"] == "gcp-shadow"
        assert status["root"].endswith("/gcp-shadow")
        print("TRIAID_STORAGE_NAMESPACE_SMOKE_PASS")
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    main()
