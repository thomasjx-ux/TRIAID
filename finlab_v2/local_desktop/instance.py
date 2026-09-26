"""Cross-platform exclusive local-instance lock acquired before importing TRIAID."""
from __future__ import annotations
import os
from contextlib import contextmanager
from .config import home

@contextmanager
def exclusive_instance():
    root=home()
    path=root/".runtime.lock"
    if path.is_symlink():
        raise RuntimeError("Local runtime lock must not be a symlink")
    fd=os.open(str(path),os.O_CREAT|os.O_RDWR,0o600)
    try:
        if os.name=="nt":
            import msvcrt
            if os.fstat(fd).st_size==0:
                os.write(fd,b"\0")
            os.lseek(fd,0,os.SEEK_SET)
            try:
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            except OSError as exc:
                raise RuntimeError("TRIAID local research runtime is already running") from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("TRIAID local research runtime is already running") from exc
        try:
            yield
        finally:
            if os.name=="nt":
                os.lseek(fd,0,os.SEEK_SET)
                msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(fd,fcntl.LOCK_UN)
    finally:
        os.close(fd)
