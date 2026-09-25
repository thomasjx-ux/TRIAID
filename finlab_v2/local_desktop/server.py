"""Run the complete TRIAID backend continuously on loopback, independent of the UI."""
from __future__ import annotations

import os
import sys

from .config import HOST, PORT, admin_secret, data_dir, log_dir, session_secret


def configure() -> None:
    session_secret()
    root = data_dir()
    log_dir()
    # Fail closed against accidentally reusing cloud persistence credentials.
    os.environ["TRIAID_LOCAL_DESKTOP_MODE"] = "1"
    os.environ["TRIAID_STORAGE_BACKEND"] = "file"
    os.environ["TRIAID_DATA_DIR"] = str(root)
    os.environ["TRIAID_ADMIN_TOKEN"] = admin_secret()
    os.environ["TRIAID_DATA_AUTOMATION"] = "1"
    os.environ["TRIAID_DECISION_AUTOMATION"] = "1"
    os.environ["TRIAID_CALENDAR_SYNC"] = "1"
    os.environ.setdefault("TRIAID_STARTUP_MAINTENANCE", "1")
    os.environ.setdefault("TRIAID_LONG_RESEARCH_BOOTSTRAP", "1")
    # Local releases need their own full preflight before use; a Railway
    # audit receipt must not be treated as a local release gate.
    os.environ["TRIAID_RELEASE_AUDIT_REQUIRED"] = "0"
    for cloud_only in (
        "TRIAID_SUPABASE_PERSISTENCE_URL", "TRIAID_SUPABASE_TOKEN",
        "RAILWAY_DEPLOYMENT_ID", "RAILWAY_GIT_COMMIT_SHA",
    ):
        os.environ.pop(cloud_only, None)


def main() -> int:
    configure()
    import uvicorn

    print(f"TRIAID FIN LOCAL: starting private research server on {HOST}:{PORT}", flush=True)
    uvicorn.run(
        "local_desktop.app:app",
        host=HOST,
        port=PORT,
        workers=1,  # exactly one scheduler and one official research writer
        access_log=False,  # never log the one-time browser bootstrap URL
        log_level="warning",
        reload=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
