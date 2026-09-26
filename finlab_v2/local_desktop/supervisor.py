"""Keep the private local research runtime alive after the desktop window closes.

Install as a per-user login startup entry, never as a privileged service.
"""
from __future__ import annotations
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from .client import is_our_server
from .config import HOST,PORT,log_dir

STOP=False

def _stop(_signum,_frame)->None:
    global STOP
    STOP=True

def _other_process_on_port()->bool:
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((HOST,PORT))==0

def main()->int:
    signal.signal(signal.SIGINT,_stop)
    if hasattr(signal,"SIGTERM"):
        signal.signal(signal.SIGTERM,_stop)
    root=(Path(sys.executable).resolve().parent if getattr(sys,"frozen",False)
          else Path(__file__).resolve().parent.parent)
    logfile=log_dir()/"supervisor.log"
    delay=2
    while not STOP:
        if is_our_server():
            delay=2
            time.sleep(10)
            continue
        if _other_process_on_port():
            with logfile.open("a",encoding="utf-8") as output:
                output.write("STOP: expected loopback port belongs to another process\n")
            return 2
        with logfile.open("a",encoding="utf-8") as output:
            output.write("Starting local research runtime\n")
        log=log_dir()/"server.log"
        with log.open("ab") as output:
            process=subprocess.Popen(
                ([sys.executable,"--server"] if getattr(sys,"frozen",False)
                 else [sys.executable,"-m","local_desktop.server"]),
                cwd=str(root),stdin=subprocess.DEVNULL,
                stdout=output,stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0),
            )
            running_since=time.monotonic()
            while not STOP and process.poll() is None:
                time.sleep(2)
            if STOP and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill()
        if STOP: break
        if time.monotonic()-running_since>300:
            delay=2
        with logfile.open("a",encoding="utf-8") as output:
            output.write(f"Runtime exited {process.returncode}; retry after {delay}s\n")
        time.sleep(delay)
        delay=min(60,delay*2)
    return 0

if __name__=="__main__":
    sys.exit(main())
