from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


BASE=(sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:18080").rstrip("/")
ADMIN_TOKEN=os.getenv("TRIAID_ADMIN_TOKEN","").strip()
MUTATING_SMOKE=os.getenv("TRIAID_PRODUCTION_SMOKE_MUTATIONS","0").strip()=="1"
EXPECTED_STORAGE=os.getenv("TRIAID_PRODUCTION_SMOKE_EXPECTED_STORAGE","supabase").strip().lower() or "supabase"
EXPECTED_DURABILITY=os.getenv(
    "TRIAID_PRODUCTION_SMOKE_EXPECTED_DURABILITY",
    "PERSISTENT" if EXPECTED_STORAGE=="supabase" else "EPHEMERAL",
).strip().upper()
RESULTS=[]


def call(method,path,body=None,expected=(200,),timeout=45):
    url=BASE+path
    data=None
    headers={"accept":"application/json"}
    if ADMIN_TOKEN:
        headers["x-triaid-admin-token"]=ADMIN_TOKEN
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
assert health["storage_backend"]==EXPECTED_STORAGE
assert health["storage_durability"]==EXPECTED_DURABILITY
if EXPECTED_STORAGE=="supabase":
    assert health["storage_persistence_confirmed"] is True
assert health["startup_maintenance_enabled"] is False
assert health["decision_automation_enabled"] is True
assert health["broker_execution_enabled"] is False
assert health["official_trading_calendar_version"]=="official-trading-calendar@0.2.0"
assert health["calendar_sync_version"]=="official-trading-calendar-sync@0.1.1"
expected_revision=(
    os.getenv("TRIAID_DEPLOY_REVISION","").strip()
    or os.getenv("TRIAID_DEPLOY_REV","").strip()
)
if expected_revision:
    assert health["deployment"]["source_revision"]==expected_revision

openapi=call("GET","/openapi.json")
paths=set(openapi.get("paths") or {})
expected_paths={
    "/","/health","/api/status","/api/storage/status",
    "/api/live/run/{market_id}","/api/live/run-all","/api/run",
    "/api/runs","/api/runs/{run_id}","/api/latest/{market_id}",
    "/api/runs/{run_id}/outcome","/api/daily","/api/curves",
    "/api/experiments/cn/prospective/status",
    "/api/experiments/cn/prospective",
    "/api/experiments/cn/prospective/latest",
    "/api/experiments/cn/prospective/{experiment_id}",
    "/api/us-return-max/status","/api/us-return-max/latest","/api/us-return-max/history",
    "/api/recovery-wave/status","/api/recovery-wave/latest","/api/recovery-wave/history",
    "/api/strategies","/api/strategy-population/rules/{market_id}",
    "/api/population-state/{market_id}","/api/evolution",
    "/api/evolution/propose","/api/evolution/promote/{version}",
    "/api/strategy-evolution","/api/strategy-evolution/propose/{market_id}",
    "/api/strategy-evolution/promote/{market_id}/{version}",
    "/api/market-data/status","/api/market-data/capabilities",
    "/api/market-data/providers","/api/market-data/products",
    "/api/market-data/live-indicators/{market_id}","/api/market-data/activity/{market_id}",
    "/api/market-data/strategy-context/{market_id}",
    "/api/market-data/trading-calendar",
    "/api/market-data/trading-calendar/{market_id}",
    "/api/market-data/trading-calendar-sync",
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
assert "TRIAID FIN" in home and "TRIAID 相对收益差" in home

status=call("GET","/api/status")
assert status["architecture_version"].startswith("fin-evolution-lab@")
if expected_revision:
    assert status["deployment"]["source_revision"]==expected_revision
    assert status["deployment"]==health["deployment"]
assert status["strategy_registry_count"]==33
assert status["module_manifest"]["recovery_wave_core"]=="recovery-wave-core@0.3.0"
assert status["module_manifest"]["recovery_wave_ledger"]=="recovery-wave-ledger@0.2.0"
assert status["module_manifest"]["capital_capacity"]=="capital-capacity-layer@0.1.0"
assert status["module_manifest"]["us_return_max"]=="us-return-max-route@0.3.0"
assert status["module_manifest"]["us_return_max_ledger"]=="us-return-max-ledger@0.1.0"

storage=call("GET","/api/storage/status")
assert storage["backend"]["backend"]==EXPECTED_STORAGE
assert storage["durability"]==EXPECTED_DURABILITY

prospective_status=call("GET","/api/experiments/cn/prospective/status")
assert prospective_status["version"]=="cn-prospective-controls@0.3.0"
prospective_rows=call("GET","/api/experiments/cn/prospective?limit=5")
assert isinstance(prospective_rows,list)
if prospective_rows:
    prospective_latest=call("GET","/api/experiments/cn/prospective/latest")
    assert prospective_latest["experiment_id"]==prospective_rows[-1]["experiment_id"]
    assert prospective_latest["design"]["no_future_information"] is True
    prospective_detail=call("GET",f"/api/experiments/cn/prospective/{prospective_latest['experiment_id']}")
    assert prospective_detail["experiment_id"]==prospective_latest["experiment_id"]

us_return_status=call("GET","/api/us-return-max/status")
assert us_return_status["version"]=="us-return-max-ledger@0.1.0"
assert us_return_status["integrity"]["passed"] is True
us_return_history=call("GET","/api/us-return-max/history?limit=5")
assert isinstance(us_return_history,list)
if us_return_history:
    us_return_latest=call("GET","/api/us-return-max/latest")
    assert us_return_latest["decision_id"]==us_return_history[-1]["decision_id"]
    assert us_return_latest["route_version"]=="us-return-max-route@0.3.0"
    # Persisted decision rows are cryptographically frozen and may predate the
    # current enum names. Production smoke validates their integrity and semantic
    # guard, while deterministic route smoke validates the current selector enums.
    metric_semantics=str(us_return_latest.get("selection_metric_semantics") or "")
    assert "annualized historical strategy state-return estimate" in metric_semantics
    assert "not a calibrated future-return forecast" in metric_semantics
    assert len(us_return_latest["target_strategy_weights"])==1
    assert abs(sum(us_return_latest["target_strategy_weights"].values())-1.0)<1e-12
    assert us_return_latest["selected_strategy_id"] in us_return_latest["max_return_tie_set"]
    assert us_return_latest["capital_capacity"]["capital_sleeves_usd"]==[100000,1000000,10000000,100000000]
    assert len(us_return_latest["capital_capacity"]["sleeves"])==4
    assert all(x["starting_cash_only"] is True for x in us_return_latest["capital_capacity"]["sleeves"])

recovery_status=call("GET","/api/recovery-wave/status?market_id=CN")
assert recovery_status["version"]=="recovery-wave-ledger@0.2.0"
assert recovery_status["integrity"]["passed"] is True
recovery_history=call("GET","/api/recovery-wave/history?market_id=CN&limit=5")
assert isinstance(recovery_history,list)
if recovery_history:
    recovery_latest=call("GET","/api/recovery-wave/latest?market_id=CN")
    assert recovery_latest["decision_id"]==recovery_history[-1]["decision_id"]
    assert recovery_latest["core_version"]=="recovery-wave-core@0.3.0"
    assert recovery_latest["data_scope"]["constituent_micro_available"] is False
    assert recovery_latest["execution_discipline"]["same_bar_execution_allowed"] is False
    assert recovery_latest["second_order"]["capital_discipline"]=="TOTAL_RISK_BUDGET_SCALED_BY_STRONGEST_PROSPECTIVE_RECOVERY_EVIDENCE"
    assert recovery_latest["second_order"]["effective_risk_budget"]<=recovery_latest["second_order"]["base_risk_budget"]+1e-12
    assert recovery_latest["trade_opinions"]
    cap=recovery_latest["capital_capacity"]
    assert cap["enabled"] is True
    assert cap["version"]=="capital-capacity-layer@0.1.0"
    assert cap["capital_sleeves_cny"]==[100000,1000000,10000000,100000000]
    assert len(cap["sleeves"])==4
    assert all(x["starting_cash_only"] is True for x in cap["sleeves"])
    assert all(x["liquidity_data_complete"] is True for x in cap["sleeves"])

runs_before=call("GET","/api/runs?limit=1000")
assert isinstance(runs_before,list)

for market in ("US","CN"):
    latest=call("GET",f"/api/latest/{market}")
    assert latest["market"]["market_id"]==market
    daily=call("GET",f"/api/daily?market_id={market}")
    assert isinstance(daily,dict)
    if market=="US":
        assert daily["us_return_max"]["report_version"]=="us-return-max-ledger@0.1.0"
        assert daily["us_return_max"]["route_version"]=="us-return-max-route@0.3.0"
        assert daily["us_return_max"]["integrity"]["passed"] is True
        assert daily["us_return_max"]["latest_decision"]["capital_capacity"]["capital_sleeves_usd"]==[100000,1000000,10000000,100000000]
    if market=="CN":
        assert daily["prospective_experiment"]["report_version"]=="cn-prospective-controls@0.3.0"
        assert str(daily["prospective_experiment"]["protocol_version"]).startswith("cn-prospective-controls@")
        assert daily["prospective_experiment"]["strategy_determination"]
        assert "daily_fluctuation" in daily["prospective_experiment"]
        assert "current_portfolio_cumulative_returns" in daily["prospective_experiment"]
        assert daily["recovery_wave"]["report_version"]=="recovery-wave-ledger@0.2.0"
        assert daily["recovery_wave"]["integrity"]["passed"] is True
        assert daily["recovery_wave"]["latest_decision"]["core_version"]=="recovery-wave-core@0.3.0"
        assert daily["recovery_wave"]["latest_decision"]["trade_opinions"]
        assert daily["recovery_wave"]["latest_decision"]["capital_capacity"]["capital_sleeves_cny"]==[100000,1000000,10000000,100000000]
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
for market in ("US","CN"):
    live_indicators=call("GET",f"/api/market-data/live-indicators/{market}")
    assert live_indicators["market_id"]==market
    assert isinstance(live_indicators.get("instruments") or [],list)
    activity=call("GET",f"/api/market-data/activity/{market}?limit=20")
    assert activity["market_id"]==market
    assert isinstance(activity["refresh_plan"],dict)
    assert isinstance(activity["events"],list)
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
assert calendar["version"]=="official-trading-calendar@0.2.0"
assert calendar["markets"]["US"]["coverage_years"]==[2026,2027,2028]
assert calendar["markets"]["CN"]["coverage_years"]==[2026]
calendar_sync=call("GET","/api/market-data/trading-calendar-sync")
assert calendar_sync["version"]=="official-trading-calendar-sync@0.1.1"
assert calendar_sync["policy"].startswith("OFFICIAL_ONLY")

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

# Production preflight is read-only by default. A mutating smoke must be
# explicitly opted in and should only be used against an isolated backend.
fcur=freq["markets"]["US"]["REALTIME"]
old_level=int(fcur["level"])
old_lock=fcur.get("manual_lock")
if MUTATING_SMOKE:
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
if EXPECTED_STORAGE=="supabase":
    assert obs_status["persistent"] is True
transitions=call("GET","/api/market-data/transitions?limit=5")
assert isinstance(transitions,list)

# Fetch fresh provider data without writing observations during production
# preflight. The explicit POST refresh path is covered by deterministic smoke tests.
for market in ("US","CN"):
    if MUTATING_SMOKE:
        refreshed=call("POST",f"/api/market-data/refresh/{market}/DAILY",timeout=60)
        assert refreshed["market_id"]==market
        snap=call("GET",f"/api/market-data/snapshot/{market}/DAILY",timeout=30)
    else:
        snap=call("GET",f"/api/market-data/snapshot/{market}/DAILY?refresh=true",timeout=60)
    assert snap["market_id"]==market and snap["points"]>=300
    context=call("GET",f"/api/market-data/strategy-context/{market}",timeout=30)
    assert context["market_id"]==market
    assert context["currency"]==("USD" if market=="US" else "CNY")
    assert context["last_trading_day"]
    assert context["instruments"]
    assert len(context["strategies"])==(29 if market=="US" else 33)
    assert context["strategies"]["P28_CASH"]["assets"]==[]
    assert abs(float(context["strategies"]["P28_CASH"]["cash_weight"])-1.0)<1e-12
    for sid,row in context["strategies"].items():
        assert 0.0<=float(row["cash_weight"])<=1.0+1e-9
        for asset in row["assets"]:
            assert float(asset["latest_price"])>0.0
            assert "last_trading_day_change" in asset
            assert 0.0<float(asset["weight"])<=1.0+1e-9

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
    time_sensitive=mode in {"REALTIME","PREOPEN"}
    row=call(
        "GET",
        f"/api/market-data/instrument/{market}/{symbol}/{mode}",
        expected=(200,503) if time_sensitive else (200,),
        timeout=60,
    )
    if isinstance(row,dict) and "detail" in row:
        assert time_sensitive
        assert (
            "insufficient_points" in str(row["detail"])
            or "all_providers_failed" in str(row["detail"])
            or "not_supported" in str(row["detail"])
        )
        continue
    assert row["market_id"]==market and row["symbol"]==symbol and row["points"]>=2

# Explicitly unsupported CN auction remains a clean 503, not fabricated data.
call("GET","/api/market-data/instrument/CN/510300.SS/PREOPEN",expected=(503,),timeout=20)

# L1 endpoints must respond cleanly even if the authorized provider is absent.
for market in ("US","CN"):
    q=call("GET",f"/api/market-data/quotes/{market}",timeout=20)
    assert "available" in q

created={"runs":[]}
prospective_after=None
if MUTATING_SMOKE:
    # Mutating contract checks are intentionally opt-in. Never enable this against
    # the production persistence namespace.
    call("POST","/api/run",body={},expected=(422,))
    call("POST","/api/runs/__smoke_missing__/outcome",body={"realized_returns":{}},expected=(404,))
    call("POST","/api/evolution/promote/__smoke_missing__",body={},expected=(404,))
    call("POST","/api/strategy-evolution/propose/XX",expected=(400,))
    call("POST","/api/strategy-evolution/promote/US/__smoke_missing__",body={},expected=(404,))

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

    runs_after=call("GET","/api/runs?limit=1000")
    after_ids={x["run_id"] for x in runs_after}
    for item in created["runs"]:
        assert item["run_id"] in after_ids

    prospective_after=call("GET","/api/experiments/cn/prospective/latest")
    assert str(prospective_after["protocol_version"]).startswith("cn-prospective-controls@")
    assert prospective_after["design"]["horizons_trading_days"]==[3,5,10]
    assert prospective_after["design"]["no_future_information"] is True
    assert prospective_after["design"]["no_post_result_retuning"] is True
    assert len(prospective_after["pool"])>=2
    assert "TRIAID_STATE_TRANSITION" in prospective_after["control_rankings"]
else:
    # The strongest production-integrity assertion: preflight must not add or
    # replace persistent research runs.
    runs_after=call("GET","/api/runs?limit=1000")
    before_ids=[x["run_id"] for x in runs_before]
    after_ids=[x["run_id"] for x in runs_after]
    assert after_ids==before_ids,"read-only production smoke changed persistent run ledger"

# Latency guardrails. External market-data calls get a wider allowance.
slow=[x for x in RESULTS if x[3]>40]
assert not slow,f"endpoint calls exceeded 40s: {slow}"
control=[x for x in RESULTS if "/instrument/" not in x[1] and "/refresh/" not in x[1] and "refresh=true" not in x[1]]
control_slow=[x for x in control if x[3]>12]
assert not control_slow,f"control endpoints exceeded 12s: {control_slow}"

print("TRIAID_PRODUCTION_API_SMOKE_PASS")
print({
    "cases":len(RESULTS),
    "max_seconds":round(max(x[3] for x in RESULTS),3),
    "storage":EXPECTED_STORAGE,
    "persistent":EXPECTED_DURABILITY=="PERSISTENT",
    "mutating_smoke":MUTATING_SMOKE,
    "persistent_run_ledger_unchanged":not MUTATING_SMOKE,
    "live_runs":[x["run_id"] for x in created["runs"]],
    "prospective_experiment_id":prospective_after["experiment_id"] if prospective_after else None,
    "prospective_source_run_id":prospective_after["source_run_id"] if prospective_after else None,
})
