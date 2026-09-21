from __future__ import annotations

import os

from triaid_fin.store import RunStore

expected=(os.getenv("TRIAID_STORAGE_BACKEND","").strip().lower() or "supabase")
if expected!="supabase":
    raise RuntimeError(
        "production_storage_readonly_probe requires TRIAID_STORAGE_BACKEND=supabase"
    )

store=RunStore()
before_ids=[r.run_id for r in store.list_runs()]
status=store.status()
after_ids=[r.run_id for r in store.list_runs()]

assert status["backend"]["backend"]=="supabase"
assert status["durability"]=="PERSISTENT"
assert status["backend"]["persistence_probe"]["confirmed_across_deployments"] is True
assert status["integrity_ok"] is True
assert after_ids==before_ids

print("TRIAID_PRODUCTION_STORAGE_READONLY_PROBE_PASS")
print({
    "backend":status["backend"]["backend"],
    "durability":status["durability"],
    "run_count":len(before_ids),
    "integrity_ok":status["integrity_ok"],
})
