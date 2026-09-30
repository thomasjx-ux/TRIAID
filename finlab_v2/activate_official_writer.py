from __future__ import annotations

import json

from triaid_fin.storage_backend import SupabaseStorageBackend, build_storage_backend


def main()->None:
    backend=build_storage_backend()
    if not isinstance(backend,SupabaseStorageBackend):
        raise RuntimeError("official_writer_activation_requires_supabase_backend")
    status=backend.activate_official_writer()
    print(
        "TRIAID_OFFICIAL_WRITER_ACTIVATED",
        json.dumps(status,ensure_ascii=False,separators=(",",":")),
        flush=True,
    )


if __name__=="__main__":
    main()
