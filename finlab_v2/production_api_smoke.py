from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


BASE=(sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:18080").rstrip("/")
RESULTS=[]


def call(method,path,body=None,expected=(200,),timeout=45):
    url=BASE+path
    data=None
    headers={"accept":"application/json"}
    if body is not None:
        data=json.dumps(body).encode("utf-8")
        headers["content-type"]="application/json"
    req=urllib.request.Request(url,data=data,headers=headers,method=method)
    t0=time.monotonic()
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            raw=response.read()
            code=response.status
    except urllib.error.HTTPError as exc:
        raw=exc.read()
        code=exc.code
    elapsed=time.monotonic()-t0
    if code not in expected:
        raise AssertionError(f"{method} {path} -> {code}, expected {expected}, body={raw[:500]!r}")
    payload=None
    if raw:
        content=raw.decode("utf-8","replace")
        try:
            payload=json.loads(content)
        except Exception:
            payload=content
    RESULTS.append((method,path,code,elapsed))
    print("TRIAID_API_SMOKE_CASE",method,path,code,round(elapsed,3))
    return payload


# Wait for temporary uvicorn.
deadline=time.monotonic()+30
while True:
    try:
        health=call("GET","/health",expected=(200,),timeout=3)
        break
    except Exception:
        if time.monotonic()>=deadline:
            raise
        time.sleep(0.5)

assert health["ok"] is True
assert health["storage_backend"]=="supabase"
assert health["storage_durability"]=="PERSISTENT"
assert health["storage_persistence_confirmed"] is True
assert health["decision_automation_enabled"] is True
assert health["broker_execution_enabled"] is False

openapi=call("GET","/openapi.json")
paths=set(openapi.get("paths") or {})
expected_paths={
    "/","/health","/api/status","/api/storage/status",
    "/api/live/run/{market_id}","/api/live/run-all","/api/run",
    "/api/runs","/api/runs/{run_id}","/api/latest/{market_id}",
    "/api/runs/{run_id}/outcome","/api/daily","/api/curves",
    "/api/strategies","/api/strategy-population/rules/{market_id}",
    "/api/population-state/{market_id}","/api/evolution",
    "/api/evolution/propose","/api/evolution/promote/{version}",
    "/api/strategy-evolution","/api/strategy-evolution/propose/{market_id}",
    "/api/strategy-evolution/promote/{market_id}/{version}",
    "/api/market-data/status","/api/market-data/capabilities",
    "/api/market-data/providers","/api/market-data/products",
    "/api/market-data/trading-calendar",
    "/api/market-data/trading-calendar/{market_id}",
    "/api/market-data/frequency-policy",
    "/api/market-data/frequency-policy/{market_id}/{mode}/set-level",
    "/api/market-data/frequency-policy/{market_id}/{mode}/set-interval",
    "/api/market-data/frequency-policy/{market_id}/{mode}/unlock",
    "/api/market-data/frequency-policy/{market_id}/{mode}/evidence",
    "/api/market-data/quotes/{market_id}",
    "/api/market-data/instrument/{market_id}/{symbol}/{mode}",
    "/api/market-data/observations","/api/market-data/observation-status",
    "/api/market-data/transitions",
    "/api/market-data/snapshot/{market_id}/{mode}",
    "/api/market-data/refresh/{market_id}/{mode}",
    "/api/decision-scheduler/status",
    "/api/decision-scheduler/events",
}
missing=sorted(expected_paths-paths)
assert not missing,f"missing OpenAPI paths: {missing}"

home=call("GET","/")
assert "TRIAID FIN" in home and "TRIAID 增益" in home

status=call("GET","/api/status")
assert status["architecture_version"].startswith("fin-evolution-lab@")
assert status["strategy_registry_count"]==33

storage=call("GET","/api/storage/status")
assert storage["backend"]["backend"]=="supabase"
assert storage["durability"]=="PERSISTENT"

runs_before=call("GET","/api/runs?limit=20")
assert isinstance(runs_before,list)

for market in ("US","CN"):
    latest=call("GET",f"/api/latest/{market}")
    assert latest["market"]["market_id"]==market
    daily=call("GET",f"/api/daily?market_id={market}")
    assert isinstance(daily,dict)
    curves=call("GET",f"/api/curves?market_id={market}")
    assert isinstance(curves,list)
    cards=call("GET",f"/api/strategies?market_id={market}&lang=zh")
    assert len(cards)==(29 if market=="US" else 33)
    rules=call("GET",f"/api/strategy-population/rules/{market}")
    assert isinstance(rules,dict)
    pop=call("GET",f"/api/population-state/{market}")
    assert isinstance(pop,dict)
    se=call("GET",f"/api/strategy-evolution?market_id={market}")
    assert isinstance(se,dict)

cards_en=call("GET","/api/strategies?market_id=US&lang=en")
assert len(cards_en)==29

evo=call("GET","/api/evolution")
assert evo.get("active_version")

md=call("GET","/api/market-data/status")
assert md["automation_enabled"] is False  # temporary smoke server only
assert md["decision_scheduler"]["enabled"] is True
assert md["decision_scheduler"]["broker_execution_enabled"] is False
decision_status=call("GET","/api/decision-scheduler/status")
assert decision_status["enabled"] is True
assert decision_status["broker_execution_enabled"] is False
decision_events=call("GET","/api/decision-scheduler/events?limit=5")
assert isinstance(decision_events,list)
caps=call("GET","/api/market-data/capabilities")
assert caps["US"]["DAILY"]["supported"] is True
assert caps["CN"]["DAILY"]["supported"] is True
providers=call("GET","/api/market-data/providers")
assert providers["registry"]["chains"]["US:DAILY"]==["research_bars","sina_us_backup"]
assert providers["registry"]["chains"]["CN:DAILY"]==["research_bars","tencent_cn_backup"]
products=call("GET","/api/market-data/products")
assert products["US"]["BAR_DAILY"]["available"] is True
assert products["CN"]["BAR_DAILY"]["available"] is True

calendar=call("GET","/api/market-data/trading-calendar")
assert calendar["version"]=="official-trading-calendar@0.1.0"
assert calendar["markets"]["US"]["coverage_years"]==[2026,2027,2028]
assert calendar["markets"]["CN"]["coverage_years"]==[2026]

us_closed=call("GET","/api/market-data/trading-calendar/US?date=2026-07-03")
assert us_closed["calendar_known"] is True
assert us_closed["is_trading_day"] is False
assert us_closed["reason"]=="OFFICIAL_EXCHANGE_HOLIDAY"

us_early=call("GET","/api/market-data/trading-calendar/US?date=2026-11-27")
assert us_early["is_trading_day"] is True
assert us_early["early_close"] is True
assert us_early["early_close_time"]=="13:00"

cn_closed=call("GET","/api/market-data/trading-calendar/CN?date=2026-09-25")
assert cn_closed["calendar_known"] is True
assert cn_closed["is_trading_day"] is False

cn_open=call("GET","/api/market-data/trading-calendar/CN?date=2026-09-28")
assert cn_open["is_trading_day"] is True

cn_unknown=call("GET","/api/market-data/trading-calendar/CN?date=2027-01-04")
assert cn_unknown["calendar_known"] is False
assert cn_unknown["is_trading_day"] is False
assert cn_unknown["reason"]=="CALENDAR_YEAR_UNAVAILABLE"
freq=call("GET","/api/market-data/frequency-policy")
assert freq["principle"].startswith("START_HIGHEST")

# Round-trip one frequency control without changing its effective level/lock.
fcur=freq["markets"]["US"]["REALTIME"]
old_level=int(fcur["level"])
old_lock=fcur.get("manual_lock")
query=urllib.parse.urlencode({
    "level":old_level,
    "lock":"true" if old_lock else "false",
    "reason":(old_lock or {}).get("reason","production-smoke-preserve"),
})
fset=call("POST",f"/api/market-data/frequency-policy/US/REALTIME/set-level?{query}")
assert int(fset["level"])==old_level

# Read-only telemetry streams.
obs=call("GET","/api/market-data/observations?limit=5")
assert isinstance(obs,list)
obs_status=call("GET","/api/market-data/observation-status")
assert obs_status["persistent"] is True
transitions=call("GET","/api/market-data/transitions?limit=5")
assert isinstance(transitions,list)

# Explicit fresh daily data through the whole provider->hub->observation path.
for market in ("US","CN"):
    refreshed=call("POST",f"/api/market-data/refresh/{market}/DAILY",timeout=60)
    assert refreshed["market_id"]==market
    snap=call("GET",f"/api/market-data/snapshot/{market}/DAILY",timeout=30)
    assert snap["market_id"]==market and snap["points"]>=300

# On-demand instruments, including intraday modes.
instrument_cases=[
    ("US","AAPL","DAILY"),
    ("CN","600519.SS","DAILY"),
    ("US","SPY","INTRADAY"),
    ("CN","510300.SS","INTRADAY"),
    ("US","SPY","REALTIME"),
    ("CN","510300.SS","REALTIME"),
    ("US","SPY","PREOPEN"),
]
for market,symbol,mode in instrument_cases:
    row=call("GET",f"/api/market-data/instrument/{market}/{symbol}/{mode}",timeout=60)
    assert row["market_id"]==market and row["symbol"]==symbol and row["points"]>=2

# Explicitly unsupported CN auction remains a clean 503, not fabricated data.
call("GET","/api/market-data/instrument/CN/510300.SS/PREOPEN",expected=(503,),timeout=20)

# L1 endpoints must respond cleanly even if the authorized provider is absent.
for market in ("US","CN"):
    q=call("GET",f"/api/market-data/quotes/{market}",timeout=20)
    assert "available" in q

# Error-contract wiring for mutating endpoints without changing research state.
call("POST","/api/run",body={},expected=(422,))
call("POST","/api/runs/__smoke_missing__/outcome",body={"realized_returns":{}},expected=(404,))
call("POST","/api/evolution/promote/__smoke_missing__",body={},expected=(404,))
call("POST","/api/strategy-evolution/propose/XX",expected=(400,))
call("POST","/api/strategy-evolution/promote/US/__smoke_missing__",body={},expected=(404,))

# Real research decision path: both markets, then poll persistent run records.
created=call("POST","/api/live/run-all",expected=(202,),timeout=20)
assert len(created["runs"])==2
for item in created["runs"]:
    run_id=item["run_id"]
    deadline=time.monotonic()+120
    final=None
    while time.monotonic()<deadline:
        final=call("GET",f"/api/runs/{run_id}",timeout=20)
        if final["status"] not in {"CREATED","FETCHING_DATA"}:
            break
        time.sleep(1)
    assert final is not None
    assert final["status"] in {"DECISION_READY_AWAITING_OUTCOME","NO_NEW_DATA","VERIFIED"}
    assert final["status"]!="FAILED"

runs_after=call("GET","/api/runs?limit=50")
after_ids={x["run_id"] for x in runs_after}
for item in created["runs"]:
    assert item["run_id"] in after_ids

# Latency guardrails. External market-data calls get a wider allowance.
slow=[x for x in RESULTS if x[3]>40]
assert not slow,f"endpoint calls exceeded 40s: {slow}"
control=[x for x in RESULTS if "/instrument/" not in x[1] and "/refresh/" not in x[1]]
control_slow=[x for x in control if x[3]>12]
assert not control_slow,f"control endpoints exceeded 12s: {control_slow}"

print("TRIAID_PRODUCTION_API_SMOKE_PASS")
print({
    "cases":len(RESULTS),
    "max_seconds":round(max(x[3] for x in RESULTS),3),
    "storage":"supabase",
    "persistent":True,
    "live_runs":[x["run_id"] for x in created["runs"]],
})
