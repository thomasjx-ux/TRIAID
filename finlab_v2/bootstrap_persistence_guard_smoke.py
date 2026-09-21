from __future__ import annotations

import os

from bootstrap_live import require_bootstrap_storage_isolation

original_backend=os.environ.get("TRIAID_STORAGE_BACKEND")
original_allow=os.environ.get("TRIAID_BOOTSTRAP_ALLOW_PERSISTENT")
try:
    os.environ["TRIAID_STORAGE_BACKEND"]="supabase"
    os.environ.pop("TRIAID_BOOTSTRAP_ALLOW_PERSISTENT",None)
    try:
        require_bootstrap_storage_isolation()
    except RuntimeError as exc:
        assert "refuses non-file storage" in str(exc)
    else:
        raise AssertionError("bootstrap guard accepted production persistence")

    os.environ["TRIAID_STORAGE_BACKEND"]="file"
    require_bootstrap_storage_isolation()

    os.environ["TRIAID_STORAGE_BACKEND"]="supabase"
    os.environ["TRIAID_BOOTSTRAP_ALLOW_PERSISTENT"]="1"
    require_bootstrap_storage_isolation()
finally:
    if original_backend is None:
        os.environ.pop("TRIAID_STORAGE_BACKEND",None)
    else:
        os.environ["TRIAID_STORAGE_BACKEND"]=original_backend
    if original_allow is None:
        os.environ.pop("TRIAID_BOOTSTRAP_ALLOW_PERSISTENT",None)
    else:
        os.environ["TRIAID_BOOTSTRAP_ALLOW_PERSISTENT"]=original_allow

print("TRIAID_BOOTSTRAP_PERSISTENCE_GUARD_SMOKE_PASS")
