"""Verify local data-directory persistence across independent process starts.

A successful check does not prove survival of sudden power loss or restore a backup.
"""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .config import data_dir

MARKER=".local-durability-probe.json"

def _digest(root:str,boot_id:str,nonce:str)->str:
    return hashlib.sha256(f"{root}\n{boot_id}\n{nonce}".encode()).hexdigest()

def verify(root:Path,boot_id:str|None=None)->dict:
    if root.is_symlink():
        raise RuntimeError("Local research data directory must not be a symlink")
    root.mkdir(parents=True,exist_ok=True)
    root=root.resolve(strict=True)
    marker=root/MARKER
    if marker.is_symlink():
        raise RuntimeError("Local durability marker must not be a symlink")
    previous=None
    if marker.exists():
        try:
            previous=json.loads(marker.read_text(encoding="utf-8"))
            digest=_digest(previous["root"],previous["boot_id"],previous["nonce"])
            if previous["root"]!=str(root) or not secrets.compare_digest(previous["checksum"],digest):
                raise ValueError("invalid local marker")
        except (OSError,ValueError,TypeError,KeyError) as exc:
            raise RuntimeError("Existing local-disk proof is invalid; investigate before starting") from exc
    boot_id=boot_id or str(uuid.uuid4())
    payload={
        "version":1,"root":str(root),"boot_id":boot_id,
        "nonce":secrets.token_hex(24),
        "verified_at_utc":datetime.now(timezone.utc).isoformat(),
    }
    payload["checksum"]=_digest(payload["root"],payload["boot_id"],payload["nonce"])
    tmp=root/f".local-durability-{uuid.uuid4().hex}.tmp"
    try:
        fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as out:
            json.dump(payload,out,sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        tmp.replace(marker)
        if os.name!="nt":
            fd=os.open(str(root),os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
            try: os.fsync(fd)
            finally: os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)
    if json.loads(marker.read_text(encoding="utf-8"))!=payload:
        raise RuntimeError("Local-disk probe readback failed")
    restored=bool(previous and previous["boot_id"]!=boot_id)
    return {
        "status":"VERIFIED_AFTER_RESTART" if restored else "PENDING_SECOND_START",
        "verified_across_starts":restored,
        "root":str(root),
        "verified_at_utc":payload["verified_at_utc"],
        "power_loss_recovery_tested":False,
        "independent_backup_tested":False,
    }

def main()->int:
    print(json.dumps(verify(data_dir()),ensure_ascii=False,sort_keys=True))
    return 0

if __name__=="__main__":
    sys.exit(main())
