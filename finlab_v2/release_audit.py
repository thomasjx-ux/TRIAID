from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent
MODE=(sys.argv[1] if len(sys.argv)>1 else "build").strip().lower()
RECEIPT_PATH=Path(os.getenv(
    "TRIAID_RELEASE_AUDIT_RECEIPT_PATH",
    "/tmp/triaid_release_audit.json" if MODE=="runtime" else "/tmp/triaid_build_audit.json",
))
BASE=os.getenv("TRIAID_POSTDEPLOY_SMOKE_BASE","http://127.0.0.1:8080").rstrip("/")

BUILD_CASES=[
    "selftest.py",
    "backend_audit_regression.py",
    "manual_preview_isolation_smoke.py",
    "bootstrap_persistence_guard_smoke.py",
    "startup_readonly_contract_smoke.py",
    "primary_revision_migration_smoke.py",
    "strategy_contract_smoke.py",
    "n_market_multi_account_smoke.py",
    "provider_adjustment_smoke.py",
    "provider_freshness_smoke.py",
    "tushare_auction_smoke.py",
    "close_transition_regression.py",
    "prospective_experiment_smoke.py",
    "recovery_wave_smoke.py",
    "capital_capacity_smoke.py",
    "us_return_max_smoke.py",
    "adaptive_alpha_promotion_smoke.py",
    "core_evolution_validation_smoke.py",
    "strategy_evolution_validation_smoke.py",
    "daily_report_change_attribution_smoke.py",
    "long_cycle_hypothesis_smoke.py",
    "cross_market_crash_smoke.py",
    "latent_hazard_smoke.py",
    "policy_curve_smoke.py",
    "hazard_prospective_smoke.py",
    "risk_warning_smoke.py",
    "risk_control_smoke.py",
    "hk_market_smoke.py",
    "hk_high_frequency_degradation_smoke.py",
    "ui_smoke.py",
    "market_switch_fastpath_smoke.py",
    "market_ui_readability_smoke.py",
    "ui_table_contract_smoke.py",
    "ui_copy_quality_smoke.py",
    "rendered_home_js_smoke.py",
    "calendar_sync_config_smoke.py",
    "runtime_env_config_smoke.py",
    "production_smoke_contract_static.py",
    "deployment_reproducibility_smoke.py",
    "manual_preview_guard_smoke.py",
    "hk_tencent_5m_aggregation_smoke.py",
    "storage_runtime_fence_smoke.py",
]

RUNTIME_BOOTSTRAPS=[
    "hk_market_live_bootstrap.py",
    "policy_hazard_live_bootstrap.py",
]

RUNTIME_REQUIRED_PATHS=[
    "/health/live",
    "/api/status",
    "/api/audit/status",
    "/api/storage/status",
    "/api/market-data/status",
    "/api/market-data/registry",
    "/api/accounts/status",
    "/api/ui/market-clocks",
    "/api/risk-warning/latest",
    "/api/risk-control/latest",
    "/api/strategy-population/rules/US",
    "/api/strategy-population/rules/CN",
    "/api/strategy-population/rules/HK",
    "/api/decision-scheduler/events?market_id=US&limit=1",
    "/api/decision-scheduler/events?market_id=CN&limit=1",
    "/api/decision-scheduler/events?market_id=HK&limit=1",
    "/",
]

def write_receipt(payload:dict)->None:
    RECEIPT_PATH.parent.mkdir(parents=True,exist_ok=True)
    tmp=RECEIPT_PATH.with_suffix(RECEIPT_PATH.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True),encoding="utf-8")
    tmp.replace(RECEIPT_PATH)

def run_case(script:str)->dict:
    path=ROOT/script
    if not path.exists():
        return {"name":script,"passed":False,"returncode":127,"error":"MISSING_AUDIT_SCRIPT"}
    started=time.monotonic()
    cp=subprocess.run(
        [sys.executable,str(path)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    elapsed=round(time.monotonic()-started,3)
    return {
        "name":script,
        "passed":cp.returncode==0,
        "returncode":cp.returncode,
        "elapsed_seconds":elapsed,
        "stdout_tail":"\n".join(cp.stdout.splitlines()[-8:]),
        "stderr_tail":"\n".join(cp.stderr.splitlines()[-8:]),
    }

def structural_checks()->list[dict]:
    rows=[]
    def check(name:str,condition:bool,detail=None):
        rows.append({"name":name,"passed":bool(condition),"detail":detail})
    start=(ROOT/"start.sh").read_text(encoding="utf-8")
    gate=(ROOT/"build_gate.sh").read_text(encoding="utf-8")
    app=(ROOT/"app.py").read_text(encoding="utf-8")
    post=(ROOT/"postdeploy_runtime_smoke.py").read_text(encoding="utf-8")
    check("build_gate_single_orchestrator","release_audit.py build" in gate,gate)
    check("runtime_audit_is_blocking","release_audit.py runtime" in start and "wait \"$SERVER_PID\"" in start,start)
    check("critical_audit_not_echo_only","TRIAID_POSTDEPLOY_RUNTIME_SMOKE_FAILED" not in start and "TRIAID_RISK_CENTER_FULL_AUDIT_FAILED" not in start,start)
    check("liveness_endpoint_present",'@app.get("/health/live")' in app,None)
    check("readiness_audit_gate_present","TRIAID_RELEASE_AUDIT_REQUIRED" in app and "release_audit" in app,None)
    check("audit_status_api_present",'@app.get("/api/audit/status")' in app,None)
    check("railway_git_identity_contract","RAILWAY_GIT_COMMIT_SHA" in app and "identity_verified" in app,None)
    check("runtime_smoke_uses_liveness",'/health/live' in post,None)
    gateway=ROOT.parent/"gateway"/"gateway.py"
    if gateway.exists():
        gateway_text=gateway.read_text(encoding="utf-8")
        check("gateway_risk_quality_collapse_contract","RISK_QUALITY_OPEN_FIXED" in gateway_text and "quality_collapsed" in gateway_text,None)
        check("gateway_home_cache_bypass_contract",'Cache-Control","no-store, no-cache, must-revalidate, max-age=0"' in gateway_text,None)
    else:
        check("gateway_contract_out_of_scope",True,{"state":"NOT_IN_RUNTIME_BUILD_CONTEXT","path":str(gateway)})
    check("build_case_manifest_unique",len(BUILD_CASES)==len(set(BUILD_CASES)),BUILD_CASES)
    missing=[x for x in BUILD_CASES if not (ROOT/x).exists()]
    check("all_manifest_scripts_exist",not missing,missing)
    return rows

def http_get(path:str,timeout:float=20.0):
    req=urllib.request.Request(
        BASE+path,
        headers={"accept":"application/json","user-agent":"TRIAID-RELEASE-AUDIT/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req,timeout=timeout) as response:
        raw=response.read()
        ctype=response.headers.get("content-type") or ""
        text=raw.decode("utf-8","replace")
        if "json" in ctype.lower():
            return response.status,json.loads(text) if text else None
        return response.status,text

def wait_liveness(timeout_seconds:float=120.0):
    deadline=time.monotonic()+timeout_seconds
    last=None
    while time.monotonic()<deadline:
        try:
            code,payload=http_get("/health/live",timeout=3)
            if code==200 and isinstance(payload,dict) and payload.get("ok") is True:
                return payload
            last=f"{code}:{payload}"
        except Exception as exc:
            last=f"{type(exc).__name__}:{exc}"
        time.sleep(1)
    raise RuntimeError(f"LIVENESS_TIMEOUT:{last}")

def runtime_checks()->list[dict]:
    rows=[]
    def check(name:str,condition:bool,detail=None):
        rows.append({"name":name,"passed":bool(condition),"detail":detail})

    live=wait_liveness()
    check("process_liveness",live.get("ok") is True,live)

    maintenance_deadline=time.monotonic()+120
    maintenance=live.get("startup_maintenance_receipt") or {}
    while maintenance.get("state") not in {"COMPLETED","FAILED","DISABLED"} and time.monotonic()<maintenance_deadline:
        time.sleep(1)
        _,live=http_get("/health/live",timeout=5)
        maintenance=(live or {}).get("startup_maintenance_receipt") or {}
    check("startup_maintenance_complete",maintenance.get("state") in {"COMPLETED","DISABLED"},maintenance)

    for script in RUNTIME_BOOTSTRAPS:
        result=run_case(script)
        rows.append({"name":"runtime_bootstrap:"+script,"passed":result["passed"],"detail":result})

    for risk_path in ("/api/risk-warning/latest","/api/risk-control/latest"):
        risk_deadline=time.monotonic()+180
        while time.monotonic()<risk_deadline:
            try:
                code,_=http_get(risk_path,timeout=10)
                if code==200:
                    break
            except Exception:
                pass
            time.sleep(2)

    payloads={}
    for path in RUNTIME_REQUIRED_PATHS:
        try:
            code,payload=http_get(path)
            payloads[path]=payload
            check("http:"+path,code==200,{"status":code})
        except Exception as exc:
            check("http:"+path,False,f"{type(exc).__name__}:{exc}")

    status=payloads.get("/api/status") or {}
    storage=payloads.get("/api/storage/status") or {}
    market_status=payloads.get("/api/market-data/status") or {}
    market_registry=payloads.get("/api/market-data/registry") or {}
    account_registry=payloads.get("/api/accounts/status") or {}
    market_clocks=payloads.get("/api/ui/market-clocks") or {}
    risk_warning=payloads.get("/api/risk-warning/latest") or {}
    risk_control=payloads.get("/api/risk-control/latest") or {}
    home=payloads.get("/") or ""

    registered_markets=[str(x).upper() for x in (status.get("markets") or [])]
    base_markets={"US","CN","HK"}
    check(
        "base_markets_present",
        base_markets.issubset(set(registered_markets)),
        registered_markets,
    )
    check(
        "registered_markets_unique",
        len(registered_markets)==len(set(registered_markets)),
        registered_markets,
    )
    strategy_count=status.get("strategy_registry_count")
    check(
        "strategy_registry_baseline_or_higher",
        isinstance(strategy_count,int) and strategy_count>=33,
        strategy_count,
    )
    registry_rows=market_registry.get("markets") or []
    registry_ids={str(x.get("market_id") or "").upper() for x in registry_rows if isinstance(x,dict)}
    check(
        "market_registry_matches_status",
        set(registered_markets)==registry_ids,
        {"status":registered_markets,"registry":sorted(registry_ids)},
    )
    accounts=(account_registry.get("accounts") or {})
    pools=(account_registry.get("strategy_pools") or {})
    check("global_account_present","GLOBAL" in accounts,sorted(accounts))
    check("global_strategy_pool_present","GLOBAL" in pools,sorted(pools))

    clock_rows=market_clocks.get("markets") or []
    clock_map={
        str(row.get("market_id") or "").upper():row
        for row in clock_rows if isinstance(row,dict)
    }
    check("market_clock_base_market_coverage",base_markets.issubset(set(clock_map)),sorted(clock_map))
    expected_timezones={"US":"America/New_York","CN":"Asia/Shanghai","HK":"Asia/Hong_Kong"}
    for market,timezone_name in expected_timezones.items():
        row=clock_map.get(market) or {}
        phase=str(row.get("session_phase") or "")
        check(f"{market}_market_clock_timezone",row.get("timezone")==timezone_name,row)
        check(f"{market}_market_clock_phase_present",phase in {"OPEN","PREOPEN","BREAK","POSTCLOSE","CLOSED","CALENDAR_UNAVAILABLE"},row)
        check(f"{market}_market_clock_green_semantics",bool(row.get("is_open"))==(phase=="OPEN"),row)
        check(f"{market}_market_clock_local_time_present",bool(row.get("local_iso")),row)

    deployment=status.get("deployment") or {}
    railway_sha=deployment.get("railway_git_commit_sha")
    declared_sha=deployment.get("declared_source_revision")
    runtime_sha=deployment.get("runtime_revision")
    check("railway_git_commit_sha_present",bool(railway_sha),deployment)
    check("declared_source_revision_present",bool(declared_sha),deployment)
    check("runtime_revision_present",bool(runtime_sha),deployment)
    check("declared_source_matches_railway",bool(railway_sha and declared_sha and railway_sha==declared_sha),deployment)
    check("runtime_revision_matches_railway",bool(railway_sha and runtime_sha and railway_sha==runtime_sha),deployment)
    check("deployment_identity_verified",deployment.get("identity_verified") is True,deployment)

    backend=storage.get("backend") or {}
    backend_name=backend.get("backend") if isinstance(backend,dict) else backend
    durability=storage.get("durability")
    check("storage_backend_known",backend_name in {"supabase","file"},{"backend":backend_name,"durability":durability})
    if backend_name=="supabase":
        check("production_storage_persistent",durability=="PERSISTENT",durability)

    check("market_data_status_present",bool(market_status),None if market_status else "missing")
    for market in ("US","CN","HK"):
        rules=payloads.get(f"/api/strategy-population/rules/{market}") or {}
        check(f"{market}_rules_market_id",str(rules.get("market_id") or "").upper()==market,rules.get("market_id"))
        events=payloads.get(f"/api/decision-scheduler/events?market_id={market}&limit=1")
        check(f"{market}_scheduler_api",isinstance(events,list),type(events).__name__)

    market_rows=(risk_control.get("market_states") or risk_control.get("three_market_state") or [])
    risk_market_ids=[str(x.get("market") or "").upper() for x in market_rows if isinstance(x,dict)]
    check(
        "risk_control_base_market_coverage",
        base_markets.issubset(set(risk_market_ids)),
        risk_market_ids,
    )
    check(
        "risk_control_registered_market_scope",
        set(risk_market_ids).issubset(set(registered_markets)),
        {"risk":risk_market_ids,"registered":registered_markets},
    )
    overall=(risk_warning.get("overall") or {})
    score=overall.get("risk_pressure_index")
    check("risk_warning_score_present",isinstance(score,(int,float)),score)
    if isinstance(score,(int,float)):
        check("risk_warning_score_range",0.0<=float(score)<=100.0,score)
    check("risk_warning_no_production_action",risk_warning.get("production_action")=="NONE" and risk_warning.get("applied_to_weights") is False,{"action":risk_warning.get("production_action"),"applied":risk_warning.get("applied_to_weights")})
    rce=risk_control.get("risk_control_experiment") or {}
    check("risk_control_shadow_only",risk_control.get("shadow_only") is True,risk_control.get("shadow_only"))
    check("risk_control_no_production_action",rce.get("production_action")=="NONE" and rce.get("applied_to_weights") is False,{"action":rce.get("production_action"),"applied":rce.get("applied_to_weights")})

    if isinstance(home,str):
        for marker in (
            "TRIAID FIN",
            "TRIAID",
            "风险中心",
            "立即运行（预览）",
            'id="riskDataQuality"',
            'id="riskDataGaps"',
        ):
            check("ui_marker:"+marker,marker in home,None if marker in home else "missing")
        check("ui_no_raw_json_dump","JSON.stringify(d,null,2)" not in home,None)
        for marker in ('id="homeSummary"','id="homeSummaryPurpose"','id="homeSummaryDecision"','id="homeSummaryValidation"','id="homeSummaryRisk"','data-clock-market="US"','data-clock-market="CN"','data-clock-market="HK"','id="marketHero"'):
            check("ui_market_identity_marker:"+marker,marker in home,None if marker in home else "missing")
        risk_pos=home.find('id="riskWarningPanel"')
        specialty_positions=[
            home.find('id="usReturnMaxPanel"'),
            home.find('id="prospectivePanel"'),
            home.find('id="recoveryWavePanel"'),
        ]
        check(
            "ui_market_specific_content_precedes_cross_market_risk",
            risk_pos>max(specialty_positions) and min(specialty_positions)>=0,
            {"risk_pos":risk_pos,"specialty_positions":specialty_positions},
        )

    maintenance=live.get("startup_maintenance_receipt")
    if isinstance(maintenance,dict):
        check("startup_maintenance_not_failed",maintenance.get("state")!="FAILED",maintenance)

    risk_audit=run_case("risk_center_full_audit.py")
    rows.append({"name":"risk_center_full_audit","passed":risk_audit["passed"],"detail":risk_audit})

    smoke=run_case("postdeploy_runtime_smoke.py")
    rows.append({"name":"postdeploy_runtime_smoke","passed":smoke["passed"],"detail":smoke})

    return rows

def finish(mode:str,rows:list[dict])->int:
    failed=[row for row in rows if not row.get("passed")]
    receipt={
        "audit":"TRIAID_RELEASE_AUDIT_CHAIN_V1",
        "mode":mode,
        "passed":not failed,
        "required_check_count":len(rows),
        "passed_check_count":len(rows)-len(failed),
        "failed_check_count":len(failed),
        "failed_checks":[row.get("name") for row in failed],
        "source_identity":{
            "railway_git_commit_sha":os.getenv("RAILWAY_GIT_COMMIT_SHA","").strip() or None,
            "declared_source_revision":(
                os.getenv("TRIAID_DEPLOY_REVISION","").strip()
                or os.getenv("TRIAID_DEPLOY_REV","").strip()
                or None
            ),
            "runtime_revision":os.getenv("TRIAID_V2_REV","").strip() or None,
        },
        "checks":rows,
        "completed_at_unix":time.time(),
    }
    write_receipt(receipt)
    print(
        "TRIAID_RELEASE_AUDIT_"+("PASS" if receipt["passed"] else "FAIL"),
        json.dumps(
            {
                "mode":mode,
                "required":receipt["required_check_count"],
                "passed":receipt["passed_check_count"],
                "failed":receipt["failed_check_count"],
                "failed_checks":receipt["failed_checks"],
                "receipt_path":str(RECEIPT_PATH),
            },
            ensure_ascii=False,
            separators=(",",":"),
        ),
        flush=True,
    )
    return 0 if receipt["passed"] else 1

if MODE=="build":
    rows=structural_checks()
    compile_case=subprocess.run(
        [sys.executable,"-m","py_compile","app.py",*sorted(str(p.relative_to(ROOT)) for p in (ROOT/"triaid_fin").glob("*.py")),*sorted(p.name for p in ROOT.glob("*.py"))],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    rows.append({
        "name":"python_compile_all",
        "passed":compile_case.returncode==0,
        "detail":{"returncode":compile_case.returncode,"stderr_tail":"\n".join(compile_case.stderr.splitlines()[-8:])},
    })
    for script in BUILD_CASES:
        rows.append(run_case(script))
    raise SystemExit(finish("build",rows))

if MODE=="runtime":
    try:
        rows=runtime_checks()
    except Exception as exc:
        rows=[{"name":"runtime_audit_exception","passed":False,"detail":f"{type(exc).__name__}:{exc}"}]
    raise SystemExit(finish("runtime",rows))

raise SystemExit(f"unknown audit mode: {MODE}")
