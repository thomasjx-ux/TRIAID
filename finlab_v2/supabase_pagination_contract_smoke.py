from __future__ import annotations

from triaid_fin.storage_backend import SupabaseStorageBackend, StorageBackendError

backend=object.__new__(SupabaseStorageBackend)
calls=[]

def fake_call(payload:dict)->dict:
    calls.append(dict(payload))
    action=payload["action"]
    cursor=str(payload.get("cursor") or "")
    if action=="list_objects":
        if not cursor:
            return {"keys":["runs/a.json","runs/b.json"],"next_cursor":"runs/b.json"}
        if cursor=="runs/b.json":
            return {"keys":["runs/c.json"],"next_cursor":None}
    if action=="read_objects":
        if not cursor:
            return {
                "objects":[{"key":"runs/a.json","content":"A"}],
                "next_cursor":"runs/a.json",
            }
        if cursor=="runs/a.json":
            return {
                "objects":[{"key":"runs/b.json","content":"B"}],
                "next_cursor":None,
            }
    raise AssertionError(payload)

backend._call=fake_call

assert backend.list_names("runs/",".json")==[
    "runs/a.json","runs/b.json","runs/c.json"
]
assert calls[0]["limit"]==100
assert "cursor" not in calls[0]
assert calls[1]["cursor"]=="runs/b.json"

calls.clear()
assert backend.list_texts("runs/",".json")=={
    "runs/a.json":"A",
    "runs/b.json":"B",
}
assert calls[0]["limit"]==20
assert calls[1]["cursor"]=="runs/a.json"

loop=object.__new__(SupabaseStorageBackend)
loop._call=lambda payload:{
    "keys":["runs/a.json"],
    "next_cursor":"same",
}
try:
    loop._paged_objects("list_objects","runs/",page_limit=10)
except StorageBackendError as exc:
    assert "supabase_pagination_cursor_loop" in str(exc)
else:
    raise AssertionError("pagination cursor loop was not fenced")

print("TRIAID_SUPABASE_PAGINATION_CONTRACT_SMOKE_PASS")
