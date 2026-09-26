"""Run the complete FIN backend locally and independently of any desktop window."""
from __future__ import annotations
import os
import re
import subprocess
import sys
from pathlib import Path
from .config import HOST,PORT,admin_secret,data_dir,log_dir,session_secret
from .durability import verify

def _source_revision()->str|None:
    packaged=Path(__file__).parent/"BUILD_REVISION"
    if packaged.is_file():
        value=packaged.read_text(encoding="ascii").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}",value):
            raise RuntimeError("Invalid packaged source revision")
        return value
    root=Path(__file__).resolve().parents[2]
    try:
        p=subprocess.run(["git","-C",str(root),"rev-parse","HEAD"],
                         capture_output=True,text=True,timeout=5,check=True)
        value=p.stdout.strip().lower()
        return value if re.fullmatch(r"[0-9a-f]{40}",value) else None
    except (OSError,subprocess.SubprocessError):
        return None

def configure()->None:
    session_secret()
    root=data_dir()
    log_dir()
    for name in (
        "TRIAID_SUPABASE_PERSISTENCE_URL","TRIAID_SUPABASE_TOKEN",
        "RAILWAY_DEPLOYMENT_ID","RAILWAY_GIT_COMMIT_SHA",
        "TRIAID_DEPLOY_REVISION","TRIAID_DEPLOY_REV","TRIAID_V2_REV",
    ):
        os.environ.pop(name,None)
    os.environ["TRIAID_LOCAL_DESKTOP_MODE"]="1"
    os.environ["TRIAID_RUNTIME_PROFILE"]="local_desktop"
    os.environ["TRIAID_STORAGE_BACKEND"]="file"
    os.environ["TRIAID_DATA_DIR"]=str(root)
    os.environ["TRIAID_ADMIN_TOKEN"]=admin_secret()
    os.environ["TRIAID_DATA_AUTOMATION"]="1"
    os.environ["TRIAID_DECISION_AUTOMATION"]="1"
    os.environ["TRIAID_CALENDAR_SYNC"]="1"
    os.environ.setdefault("TRIAID_STARTUP_MAINTENANCE","1")
    os.environ.setdefault("TRIAID_LONG_RESEARCH_BOOTSTRAP","1")
    # Installation CI audits the release, never reuse a Railway audit receipt.
    os.environ["TRIAID_RELEASE_AUDIT_REQUIRED"]="0"
    proof=verify(root)
    os.environ["TRIAID_LOCAL_STORAGE_PROBE_ROOT"]=proof["root"]
    os.environ["TRIAID_LOCAL_STORAGE_RESTART_VERIFIED"]="1" if proof["verified_across_starts"] else "0"
    os.environ["TRIAID_LOCAL_STORAGE_PROBE_STATUS"]=proof["status"]
    rev=_source_revision()
    if rev:
        for name in ("TRIAID_DEPLOY_REVISION","TRIAID_DEPLOY_REV","TRIAID_V2_REV"):
            os.environ[name]=rev

def main()->int:
    configure()
    import uvicorn
    print(f"TRIAID FIN LOCAL: private server at {HOST}:{PORT}",flush=True)
    uvicorn.run("local_desktop.app:app",host=HOST,port=PORT,workers=1,
                access_log=False,log_level="warning",reload=False)
    return 0

if __name__=="__main__":
    sys.exit(main())
