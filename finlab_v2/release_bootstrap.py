from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO="thomasjx-ux/TRIAID"
SHA=os.getenv("TRIAID_RUNTIME_SOURCE_SHA","").strip()
MODE=(sys.argv[1] if len(sys.argv)>1 else "build").strip().lower()

if not SHA:
    raise SystemExit("TRIAID_RUNTIME_SOURCE_SHA is required")
if any(ch not in "0123456789abcdefABCDEF" for ch in SHA) or len(SHA)<7:
    raise SystemExit("TRIAID_RUNTIME_SOURCE_SHA must be a git commit SHA")

BASE=Path("/tmp")/f"triaid-release-{SHA[:12]}"
ZIP=Path("/tmp")/f"triaid-release-{SHA[:12]}.zip"
SRC=BASE/"repo"
MARKER=BASE/"source.sha"

def prepare()->Path:
    fin=SRC/"finlab_v2"
    if MARKER.exists() and MARKER.read_text(encoding="utf-8").strip()==SHA and fin.exists():
        return fin

    shutil.rmtree(BASE,ignore_errors=True)
    ZIP.unlink(missing_ok=True)
    BASE.mkdir(parents=True,exist_ok=True)

    url=f"https://github.com/{REPO}/archive/{SHA}.zip"
    print("TRIAID_RELEASE_BOOTSTRAP_FETCH",SHA,url,flush=True)
    with urllib.request.urlopen(url,timeout=60) as response, ZIP.open("wb") as out:
        shutil.copyfileobj(response,out)

    extract=BASE/"extract"
    extract.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(ZIP) as zf:
        zf.extractall(extract)

    roots=[p for p in extract.iterdir() if p.is_dir()]
    if len(roots)!=1:
        raise RuntimeError(f"unexpected archive roots: {[p.name for p in roots]}")
    shutil.move(str(roots[0]),str(SRC))
    MARKER.write_text(SHA+"\n",encoding="utf-8")

    fin=SRC/"finlab_v2"
    if not fin.exists():
        raise RuntimeError("finlab_v2 missing from pinned release archive")
    return fin

def run(cmd:list[str],cwd:Path)->None:
    print("TRIAID_RELEASE_BOOTSTRAP_RUN"," ".join(cmd),flush=True)
    subprocess.run(cmd,cwd=str(cwd),check=True,env=os.environ.copy())

src=prepare()

if MODE=="build":
    requirements=src/"requirements.txt"
    if requirements.exists():
        run([sys.executable,"-m","pip","install","-r",str(requirements)],src)
    run([sys.executable,"release_audit.py","build"],src)
    print("TRIAID_PINNED_BUILD_AUDIT_PASS",SHA,flush=True)
    raise SystemExit(0)

if MODE=="start":
    os.environ["TRIAID_DEPLOY_REVISION"]=SHA
    os.environ["TRIAID_DEPLOY_REV"]=SHA
    os.environ["TRIAID_V2_REV"]=SHA
    os.chdir(src)
    print("TRIAID_PINNED_RUNTIME_START",SHA,str(src),flush=True)
    os.execvpe("sh",["sh","start.sh"],os.environ)

raise SystemExit(f"unknown mode: {MODE}")
