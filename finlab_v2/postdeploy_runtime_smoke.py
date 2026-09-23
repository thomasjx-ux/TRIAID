from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE=os.getenv("TRIAID_POSTDEPLOY_SMOKE_BASE","http://127.0.0.1:8080").rstrip("/")
EXPECTED_REVISION=(
    os.getenv("TRIAID_DEPLOY_REVISION","").strip()
    or os.getenv("TRIAID_DEPLOY_REV","").strip()
)


def call(path:str,expected=(200,),timeout=12):
    url=BASE+path
    req=urllib.request.Request(
        url,
        headers={"accept":"application/json","user-agent":"TRIAID-POSTDEPLOY-SMOKE/1.0"},
        method="GET",
    )
    t0=time.monotonic()
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            raw=response.read()
            code=response.status
            ctype=response.headers.get("content-type") or ""
    except urllib.error.HTTPError as exc:
        raw=exc.read()
        code=exc.code
        ctype=exc.headers.get("content-type") or ""
    elapsed=time.monotonic()-t0
    if code not in expected:
        raise AssertionError(f"GET {path} -> {code}, expected {expected}, body={raw[:300]!r}")
    print("TRIAID_POSTDEPLOY_SMOKE_CASE",path,code,round(elapsed,3),flush=True)
    text=raw.decode("utf-8","replace")
    if "json" in ctype.lower():
        return json.loads(text) if text else None
    return text


deadline=time.monotonic()+90
while True:
    try:
        health=call("/health",timeout=3)
        break
    except Exception:
        if time.monotonic()>=deadline:
            raise
        time.sleep(1)

assert health["ok"] is True
assert health["broker_execution_enabled"] is False
if EXPECTED_REVISION:
    assert health["deployment"]["source_revision"]==EXPECTED_REVISION

status=call("/api/status")
assert status["markets"]==["US","CN","HK"]
assert status["strategy_registry_count"]==33
if EXPECTED_REVISION:
    assert status["deployment"]["source_revision"]==EXPECTED_REVISION

storage=call("/api/storage/status")
backend_payload=storage.get("backend")
backend_name=(
    str(backend_payload.get("backend") or "")
    if isinstance(backend_payload,dict)
    else str(backend_payload or "")
)
assert backend_name in {"supabase","file"}

rules_hk=call("/api/strategy-population/rules/HK")
assert str(rules_hk.get("market_id") or "").upper()=="HK"

hk_events=call("/api/decision-scheduler/events?market_id=HK&limit=1")
assert isinstance(hk_events,list)

market_status=call("/api/market-data/status")
assert market_status

runs=call("/api/runs?limit=1")
assert isinstance(runs,list)

openapi=call("/openapi.json")
paths=set(openapi.get("paths") or {})
for path in (
    "/api/live/run/{market_id}",
    "/api/live/run-all",
    "/api/decision-scheduler/events",
    "/api/market-data/status",
    "/api/risk-warning/latest",
    "/api/risk-control/latest",
):
    assert path in paths,path

home=call("/")
assert "TRIAID FIN" in home
assert "立即运行（预览）" in home
assert "港股" in home

print(
    "TRIAID_POSTDEPLOY_RUNTIME_SMOKE_PASS",
    json.dumps(
        {
            "revision":EXPECTED_REVISION or None,
            "markets":status["markets"],
            "storage_backend":backend_name,
            "hk_scheduler_api":True,
        },
        ensure_ascii=False,
        separators=(",",":"),
    ),
    flush=True,
)
