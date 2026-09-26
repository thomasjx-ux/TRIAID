"""Real Uvicorn subprocess test: loopback auth, unique scheduler and restart durability."""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent

def _port()->int:
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]

def _get(base:str,path:str,cookie:str|None=None):
    headers={"Host":base.removeprefix("http://")}
    if cookie: headers["Cookie"]=f"triaid_local_session={cookie}"
    request=urllib.request.Request(base+path,headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request,timeout=5) as response:
            raw=response.read()
            return response.status,json.loads(raw) if response.headers.get("content-type","").startswith("application/json") else raw.decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code,exc.read().decode("utf-8","replace")

def check()->None:
    with tempfile.TemporaryDirectory(prefix="triaid-process-smoke-") as tmp:
        port=_port()
        base=f"http://127.0.0.1:{port}"
        env={**os.environ,
             "TRIAID_LOCAL_HOME":tmp,
             "TRIAID_DESKTOP_PORT":str(port),
             "TRIAID_LOCAL_TEST_MODE":"1"}
        logfile=Path(tmp)/"subprocess-smoke.log"
        for attempt in (1,2):
            with logfile.open("ab") as output:
                process=subprocess.Popen(
                    [sys.executable,"-m","local_desktop.server"],
                    cwd=str(ROOT),env=env,
                    stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                )
                try:
                    deadline=time.monotonic()+60
                    while time.monotonic()<deadline:
                        if process.poll() is not None:
                            raise AssertionError("Server exited early: "+logfile.read_text(encoding="utf-8",errors="replace")[-2500:])
                        try:
                            status,ping=_get(base,"/desktop/ping")
                            if status==200 and ping.get("service")=="TRIAID_FIN_LOCAL":
                                break
                        except (OSError,ValueError):
                            pass
                        time.sleep(0.3)
                    else:
                        raise AssertionError("Server startup timed out: "+logfile.read_text(encoding="utf-8",errors="replace")[-2500:])
                    session=(Path(tmp)/".desktop-session").read_text(encoding="ascii").strip()
                    assert _get(base,"/api/status")[0]==401
                    status,health=_get(base,"/health/live",session)
                    assert status==200 and health["broker_execution_enabled"] is False
                    status,storage=_get(base,"/desktop/storage-health",session)
                    assert status==200 and storage["backend"]=="file"
                    assert storage["local_disk_probe"]["verified_across_starts"]==(attempt==2)
                    status,clocks=_get(base,"/api/ui/market-clocks",session)
                    assert status==200
                    assert {row["market_id"] for row in clocks["markets"]}=={"US","CN","HK"}
                    status,page=_get(base,"/",session)
                    assert status==200 and "strategyRows" in page and "TRIAID" in page

                    # A concurrent server cannot enter the same data directory.
                    second=subprocess.run(
                        [sys.executable,"-m","local_desktop.server"],
                        cwd=str(ROOT),env=env,capture_output=True,text=True,timeout=20,
                    )
                    assert second.returncode!=0, "Second backend bypassed the exclusive lock"
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
        print("TRIAID_LOCAL_DESKTOP_PROCESS_SMOKE_PASS: two real starts, exclusive lock, session auth, 3 market clocks, persisted disk")

if __name__=="__main__":
    check()
