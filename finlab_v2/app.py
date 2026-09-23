from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from triaid_fin.contracts import OutcomeRequest, RunRequest
from triaid_fin.engine import EvolutionLabEngine
from triaid_fin.decision_api import build_decision_router
from triaid_fin.account_api import build_account_router
from triaid_fin.decision_scheduler import DecisionScheduler
from triaid_fin.market_api import build_market_data_router
from triaid_fin.market_runtime import MarketDataAutomation
from triaid_fin.market_registry import MARKET_REGISTRY, market_ids, normalize_market_id
from triaid_fin.trading_calendar import VERSION as TRADING_CALENDAR_VERSION, official_session_phase, trading_day_info
from triaid_fin.trading_calendar_sync import TradingCalendarSync

engine=EvolutionLabEngine()
decision_scheduler=DecisionScheduler(engine)
calendar_sync=TradingCalendarSync(engine.store)
market_automation=MarketDataAutomation(engine,decision_scheduler)

def require_admin_token(x_triaid_admin_token:str|None=Header(default=None))->None:
    expected=os.getenv("TRIAID_ADMIN_TOKEN","").strip()
    if not expected:
        raise HTTPException(status_code=503,detail="admin mutation disabled: TRIAID_ADMIN_TOKEN not configured")
    if not x_triaid_admin_token or not secrets.compare_digest(x_triaid_admin_token,expected):
        raise HTTPException(status_code=403,detail="admin authorization required")

async def supervise_market_automation()->None:
    while True:
        try:
            await market_automation.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            market_automation.supervisor_restarts+=1
            market_automation.errors["SUPERVISOR"]=f"{type(exc).__name__}:{exc}"
            print(
                "TRIAID_MARKET_AUTOMATION_SUPERVISOR_RESTART",
                market_automation.supervisor_restarts,
                market_automation.errors["SUPERVISOR"],
            )
            await asyncio.sleep(5)

async def bootstrap_startup_maintenance(app:FastAPI)->None:
    """Run restart recovery after the HTTP app is ready.

    Startup maintenance is operational hygiene, not a prerequisite for serving
    health/status traffic. Keeping it off the lifespan critical path prevents
    remote storage latency or reference bootstrapping from delaying readiness.
    """
    app.state.startup_maintenance_receipt={
        "event":"STALE_RUN_RECOVERY",
        "applied":False,
        "state":"RUNNING",
    }
    try:
        receipt=await asyncio.to_thread(engine.recover_stale_runs)
        primary_references={}
        for market_id in market_ids():
            try:
                primary_references[market_id]=await asyncio.to_thread(
                    engine.ensure_primary_reference,
                    market_id,
                )
            except Exception as exc:
                primary_references[market_id]={
                    "market_id":market_id,
                    "created":False,
                    "reason":"PRIMARY_REFERENCE_BOOTSTRAP_ERROR",
                    "error":f"{type(exc).__name__}:{exc}",
                }
        receipt["primary_references"]=primary_references
        receipt["state"]="COMPLETED"
        app.state.startup_maintenance_receipt=receipt
        print(
            "TRIAID_STARTUP_MAINTENANCE_BACKGROUND_PASS",
            receipt.get("recovered_count"),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        app.state.startup_maintenance_receipt={
            "event":"STALE_RUN_RECOVERY",
            "applied":False,
            "state":"FAILED",
            "error":f"{type(exc).__name__}:{exc}",
        }
        print(
            "TRIAID_STARTUP_MAINTENANCE_BACKGROUND_FAILED",
            f"{type(exc).__name__}:{exc}",
        )



async def bootstrap_long_horizon_research()->None:
    try:
        long_cycle=await asyncio.to_thread(engine.long_cycle_hypothesis_run,False)
        print(
            "TRIAID_LONG_CYCLE_BACKGROUND_PASS",
            long_cycle.get("experiment_id"),
            long_cycle.get("as_of"),
            ((long_cycle.get("hypotheses") or {}).get("downturn_confirmation") or {}).get("state"),
            ((long_cycle.get("hypotheses") or {}).get("stretch_vulnerability") or {}).get("state"),
        )
    except Exception as exc:
        print("TRIAID_LONG_CYCLE_BACKGROUND_FAILED",f"{type(exc).__name__}:{exc}")
    try:
        linkage=await asyncio.to_thread(engine.cross_market_crash_run,False)
        print(
            "TRIAID_US_CN_HK_CRASH_LINKAGE_BACKGROUND_PASS",
            linkage.get("experiment_id"),
            linkage.get("as_of"),
            ((linkage.get("detected_crashes") or {}).get("synchronized_pair_rows")),
        )
    except Exception as exc:
        print("TRIAID_US_CN_HK_CRASH_LINKAGE_BACKGROUND_FAILED",f"{type(exc).__name__}:{exc}")
    latent=None
    try:
        latent=await asyncio.to_thread(engine.latent_hazard_run,False)
        print(
            "TRIAID_LATENT_HAZARD_BACKGROUND_PASS",
            latent.get("experiment_id"),
            latent.get("as_of"),
            len(latent.get("top_long_lead_candidates") or []),
            len(latent.get("top_any_lead_candidates") or []),
            json.dumps({
                "top_long_lead_candidates":latent.get("top_long_lead_candidates") or [],
                "top_any_lead_candidates":latent.get("top_any_lead_candidates") or [],
                "event_count":latent.get("event_count"),
                "control_count":latent.get("control_count"),
                "data_completeness":latent.get("data_completeness"),
                "candidate_lead_rows":[
                    row for row in (latent.get("factor_lead_results") or [])
                    if row.get("candidate")
                ],
                "top_long_lead_composites":latent.get("top_long_lead_composites") or [],
                "top_any_lead_composites":latent.get("top_any_lead_composites") or [],
                "candidate_composite_rows":[
                    row for row in (latent.get("composite_lead_results") or [])
                    if row.get("candidate")
                ],
                "statistically_supported_factor_rows":latent.get("statistically_supported_factor_rows") or [],
                "statistically_supported_composite_rows":latent.get("statistically_supported_composite_rows") or [],
            },ensure_ascii=False,sort_keys=True),
        )
    except Exception as exc:
        print("TRIAID_LATENT_HAZARD_BACKGROUND_FAILED",f"{type(exc).__name__}:{exc}")

    policy_curve=None
    try:
        policy_curve=await asyncio.to_thread(engine.policy_curve_run,False)
        print(
            "TRIAID_POLICY_CURVE_BACKGROUND_PASS",
            policy_curve.get("snapshot_id"),
            policy_curve.get("as_of"),
            json.dumps(policy_curve.get("data_quality") or {},sort_keys=True),
        )
    except Exception as exc:
        print("TRIAID_POLICY_CURVE_BACKGROUND_FAILED",f"{type(exc).__name__}:{exc}")

    if latent is not None:
        try:
            frozen=await asyncio.to_thread(engine.hazard_prospective_freeze,latent,policy_curve)
            resolved=await asyncio.to_thread(engine.hazard_prospective_resolve)
            risk_warning=await asyncio.to_thread(engine.risk_warning_run,True)
            risk_control=await asyncio.to_thread(engine.risk_control_run,True)
            print(
                "TRIAID_HAZARD_PROSPECTIVE_BACKGROUND_PASS",
                frozen.get("ledger_id"),
                frozen.get("as_of"),
                frozen.get("current_state",{}).get("state_label"),
                resolved.get("updated_outcomes"),
            )
            print(
                "TRIAID_RISK_WARNING_BACKGROUND_PASS",
                risk_warning.get("warning_id"),
                risk_warning.get("as_of"),
                (risk_warning.get("overall") or {}).get("risk_pressure_index"),
                (risk_warning.get("overall") or {}).get("risk_band"),
                json.dumps(risk_warning.get("horizon_estimates") or {},sort_keys=True),
            )
            print(
                "TRIAID_RISK_CONTROL_BACKGROUND_PASS",
                risk_control.get("experiment_id"),
                risk_control.get("as_of"),
                (risk_control.get("risk_control_experiment") or {}).get("stage"),
                json.dumps(risk_control.get("three_market_state") or [],sort_keys=True),
            )
        except Exception as exc:
            print("TRIAID_HAZARD_PROSPECTIVE_BACKGROUND_FAILED",f"{type(exc).__name__}:{exc}")

@asynccontextmanager
async def lifespan(app:FastAPI):
    tasks=[]
    startup_maintenance_enabled=os.getenv(
        "TRIAID_STARTUP_MAINTENANCE","0"
    ).lower() in {"1","true","on","yes"}
    if startup_maintenance_enabled:
        app.state.startup_maintenance_receipt={
            "event":"STALE_RUN_RECOVERY",
            "applied":False,
            "state":"PENDING",
            "reason":"DEFERRED_UNTIL_APP_READY",
        }
        tasks.append(asyncio.create_task(bootstrap_startup_maintenance(app)))
    else:
        app.state.startup_maintenance_receipt={
            "event":"STALE_RUN_RECOVERY",
            "applied":False,
            "state":"DISABLED",
            "reason":"TRIAID_STARTUP_MAINTENANCE_DISABLED",
        }
    if calendar_sync.enabled:
        tasks.append(asyncio.create_task(calendar_sync.run()))
    if market_automation.enabled:
        tasks.append(asyncio.create_task(supervise_market_automation()))
    if os.getenv("TRIAID_LONG_RESEARCH_BOOTSTRAP","1").lower() not in {"0","false","off","no"}:
        tasks.append(asyncio.create_task(bootstrap_long_horizon_research()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

def deployment_identity()->dict:
    railway_git_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA","").strip() or None
    declared_source_revision=(
        os.getenv("TRIAID_DEPLOY_REVISION","").strip()
        or os.getenv("TRIAID_DEPLOY_REV","").strip()
        or None
    )
    runtime_revision=os.getenv("TRIAID_V2_REV","").strip() or None
    source_revision=railway_git_commit_sha or declared_source_revision
    declared_matches_railway=(
        True
        if not railway_git_commit_sha or not declared_source_revision
        else declared_source_revision==railway_git_commit_sha
    )
    runtime_matches_railway=(
        True
        if not railway_git_commit_sha or not runtime_revision
        else runtime_revision==railway_git_commit_sha
    )
    return {
        "source_revision":source_revision,
        "railway_git_commit_sha":railway_git_commit_sha,
        "declared_source_revision":declared_source_revision,
        "runtime_revision":runtime_revision,
        "declared_matches_railway":declared_matches_railway,
        "runtime_matches_railway":runtime_matches_railway,
        "identity_verified":bool(
            railway_git_commit_sha
            and declared_source_revision
            and runtime_revision
            and declared_matches_railway
            and runtime_matches_railway
        ),
    }


app=FastAPI(
    title="TRIAID FIN Evolution Lab V2",
    version=engine.architecture_version.split("@",1)[-1],
    lifespan=lifespan,
)
app.include_router(build_market_data_router(engine,market_automation,calendar_sync))
app.include_router(build_decision_router(decision_scheduler))
app.include_router(build_account_router(engine))


def release_audit_status()->dict:
    required=os.getenv("TRIAID_RELEASE_AUDIT_REQUIRED","1").lower() not in {"0","false","off","no"}
    path=Path(os.getenv("TRIAID_RELEASE_AUDIT_RECEIPT_PATH","/tmp/triaid_release_audit.json"))
    if not required:
        return {"required":False,"state":"DISABLED","passed":True,"receipt_path":str(path)}
    if not path.exists():
        return {"required":True,"state":"PENDING","passed":False,"receipt_path":str(path)}
    try:
        receipt=json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "required":True,
            "state":"INVALID",
            "passed":False,
            "receipt_path":str(path),
            "error":f"{type(exc).__name__}:{exc}",
        }
    passed=bool(receipt.get("passed"))
    return {
        "required":True,
        "state":"PASS" if passed else "FAIL",
        "passed":passed,
        "receipt_path":str(path),
        "audit":receipt.get("audit"),
        "required_check_count":receipt.get("required_check_count"),
        "passed_check_count":receipt.get("passed_check_count"),
        "failed_check_count":receipt.get("failed_check_count"),
        "failed_checks":receipt.get("failed_checks") or [],
    }


@app.get("/health/live")
def health_live()->dict:
    storage=engine.store.backend.status()
    probe=storage.get("persistence_probe") or {}
    volume=storage.get("volume") or {}
    return {
        "ok":True,
        "architecture_version":engine.architecture_version,
        "deployment":deployment_identity(),
        "storage_backend":storage.get("backend"),
        "storage_durability":storage.get("durability"),
        "storage_volume_mounted":volume.get("expected_mount_is_mounted"),
        "storage_persistence_confirmed":probe.get("confirmed_across_deployments"),
        "decision_automation_enabled":decision_scheduler.enabled,
        "broker_execution_enabled":False,
        "official_trading_calendar_version":TRADING_CALENDAR_VERSION,
        "calendar_sync_enabled":calendar_sync.enabled,
        "calendar_sync_version":calendar_sync.version,
        "startup_maintenance_enabled":os.getenv(
            "TRIAID_STARTUP_MAINTENANCE","0"
        ).lower() in {"1","true","on","yes"},
        "startup_maintenance_receipt":getattr(
            app.state,"startup_maintenance_receipt",None
        ),
    }


@app.get("/health")
def health():
    payload=health_live()
    audit=release_audit_status()
    payload["release_audit"]=audit
    payload["ok"]=bool(audit.get("passed"))
    if not payload["ok"]:
        return JSONResponse(status_code=503,content=payload)
    return payload


@app.get("/api/status")
def status()->dict:
    return {
        **engine.status(),
        "deployment":deployment_identity(),
    }


@app.get("/api/audit/status")
def audit_status()->dict:
    return release_audit_status()


@app.get("/api/storage/status")
def storage_status()->dict:
    return engine.store.status()


@app.post("/api/live/run/{market_id}", status_code=202)
def live_run(market_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        market_id = normalize_market_id(market_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run,scheduled,claim_reason=engine.claim_manual_preview_run(market_id)
    if scheduled:
        background_tasks.add_task(engine.execute_live, run.run_id, market_id, "MANUAL_PREVIEW")
    return {
        "run_id": run.run_id,
        "status": run.status,
        "market_id": market_id,
        "run_scope":"MANUAL_PREVIEW",
        "evidence_eligible":False,
        "scheduled":scheduled,
        "claim_reason":claim_reason,
    }


@app.post("/api/live/run-all", status_code=202)
def live_run_all(background_tasks: BackgroundTasks) -> dict:
    runs = []
    for market_id in market_ids():
        run,scheduled,claim_reason=engine.claim_manual_preview_run(market_id)
        if scheduled:
            background_tasks.add_task(engine.execute_live, run.run_id, market_id, "MANUAL_PREVIEW")
        runs.append({
            "run_id": run.run_id,
            "status": run.status,
            "market_id": market_id,
            "run_scope":"MANUAL_PREVIEW",
            "evidence_eligible":False,
            "scheduled":scheduled,
            "claim_reason":claim_reason,
        })
    return {"runs": runs,"run_scope":"MANUAL_PREVIEW","evidence_eligible":False}


@app.post("/api/run", status_code=202)
def create_run(request: RunRequest, background_tasks: BackgroundTasks, _admin:None=Depends(require_admin_token)) -> dict:
    run = engine.create_run(request)
    background_tasks.add_task(engine.execute, run.run_id, request)
    return {"run_id": run.run_id, "status": run.status}


@app.get("/api/runs")
def list_runs(market_id: str | None = None, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    rows = engine.all_runs()
    if market_id:
        rows = [r for r in rows if r.market.market_id.upper() == market_id.upper()]
    return [
        {
            "run_id": r.run_id,
            "market_id": r.market.market_id,
            "as_of": r.market.as_of,
            "snapshot_id": r.market.snapshot_id,
            "status": r.status,
            "core_version": r.triaid_decision.core_version if r.triaid_decision else None,
            "experiment_mode": r.market.metadata.get("experiment_mode"),
            "run_scope":r.market.metadata.get("run_scope","OFFICIAL_EVIDENCE"),
            "evidence_eligible":r.market.metadata.get("evidence_eligible") is not False,
            "persistent_record":engine._evidence_eligible_run(r),
            "diagnostic_summary": r.diagnostic_summary,
            "evaluation": r.evaluation.model_dump() if r.evaluation else None,
        }
        for r in rows[-limit:]
    ]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        return engine.get_run(run_id).model_dump()
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="run_id not found") from exc


@app.get("/api/latest/{market_id}")
def latest(market_id: str) -> dict:
    run = engine.latest_run(market_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no run for this market")
    return run.model_dump()


@app.post("/api/runs/{run_id}/outcome")
def submit_outcome(run_id: str, outcome: OutcomeRequest, _admin:None=Depends(require_admin_token)) -> dict:
    try:
        return engine.submit_outcome(run_id, outcome).model_dump()
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="run_id not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/daily")
def daily(
    market_id: str | None = None,
    compact: bool = Query(default=False),
) -> dict:
    return engine.daily_summary(market_id,compact=compact)


@app.get("/api/ui/core")
def ui_core_status()->dict:
    return {
        "version":engine.core.version,
        "architecture_version":engine.architecture_version,
    }


@app.get("/api/ui/market-clocks")
def ui_market_clocks()->dict:
    now_utc=datetime.now(ZoneInfo("UTC"))
    rows=[]
    for market_id in market_ids():
        spec=MARKET_REGISTRY.get(market_id)
        local_now=now_utc.astimezone(ZoneInfo(spec.timezone))
        info=trading_day_info(market_id,local_now)
        phase=official_session_phase(market_id,local_now)
        rows.append({
            "market_id":market_id,
            "timezone":spec.timezone,
            "local_iso":local_now.isoformat(),
            "session_phase":phase,
            "is_open":phase=="OPEN",
            "calendar_known":bool(info.get("calendar_known")),
            "is_trading_day":bool(info.get("is_trading_day")),
            "early_close":bool(info.get("early_close")),
            "early_close_time":info.get("early_close_time"),
            "benchmark":spec.benchmark,
            "currency":spec.currency,
            "assets":list(spec.assets),
            "primary_experiment_mode":str(spec.metadata.get("primary_experiment_mode") or ""),
        })
    return {
        "server_utc":now_utc.isoformat(),
        "markets":rows,
    }


@app.get("/api/curves")
def curves(market_id: str | None = None) -> list[dict]:
    return engine.curves(market_id)


@app.get("/api/experiments/cn/prospective/status")
def cn_prospective_status() -> dict:
    return engine.prospective_experiment_status()


@app.get("/api/experiments/cn/prospective")
def cn_prospective_list(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    return engine.prospective_experiments(limit)


@app.get("/api/experiments/cn/prospective/latest")
def cn_prospective_latest() -> dict:
    row = engine.latest_prospective_experiment()
    if row is None:
        raise HTTPException(status_code=404, detail="no prospective CN experiment")
    return row


@app.get("/api/experiments/cn/prospective/{experiment_id}")
def cn_prospective_detail(experiment_id: str) -> dict:
    try:
        return engine.prospective_experiment_detail(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="prospective experiment not found") from exc


@app.get("/api/us-return-max/status")
def us_return_max_status() -> dict:
    return engine.us_return_max_status()


@app.get("/api/us-return-max/latest")
def us_return_max_latest() -> dict:
    row=engine.latest_us_return_max_decision()
    if row is None:
        raise HTTPException(status_code=404, detail="no US return-max decision")
    return row


@app.get("/api/us-return-max/history")
def us_return_max_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.us_return_max_history(limit)


@app.get("/api/experiments/us/long-cycle/status")
def us_long_cycle_status() -> dict:
    return engine.long_cycle_hypothesis_status()


@app.get("/api/experiments/us/long-cycle/latest")
def us_long_cycle_latest() -> dict:
    row=engine.long_cycle_hypothesis_latest()
    if row is None:
        raise HTTPException(status_code=404, detail="no US long-cycle hypothesis experiment")
    return row


@app.get("/api/experiments/us/long-cycle/history")
def us_long_cycle_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.long_cycle_hypothesis_history(limit)


@app.get("/api/experiments/us-cn-hk/crash-linkage/status")
@app.get("/api/experiments/us-cn/crash-linkage/status")
def us_cn_crash_linkage_status() -> dict:
    return engine.cross_market_crash_status()


@app.get("/api/experiments/us-cn-hk/crash-linkage/latest")
@app.get("/api/experiments/us-cn/crash-linkage/latest")
def us_cn_crash_linkage_latest() -> dict:
    row=engine.cross_market_crash_latest()
    if row is None:
        raise HTTPException(status_code=404, detail="no US-CN crash linkage experiment")
    return row


@app.get("/api/experiments/us-cn-hk/crash-linkage/history")
@app.get("/api/experiments/us-cn/crash-linkage/history")
def us_cn_crash_linkage_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.cross_market_crash_history(limit)


@app.get("/api/experiments/latent-hazard/status")
def latent_hazard_status() -> dict:
    return engine.latent_hazard_status()


@app.get("/api/experiments/latent-hazard/latest")
def latent_hazard_latest() -> dict:
    row=engine.latent_hazard_latest()
    if row is None:
        raise HTTPException(status_code=404, detail="no latent hazard experiment")
    return row


@app.get("/api/experiments/latent-hazard/history")
def latent_hazard_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.latent_hazard_history(limit)


@app.get("/api/experiments/policy-curve/status")
def policy_curve_status() -> dict:
    return engine.policy_curve_status()


@app.get("/api/experiments/policy-curve/latest")
def policy_curve_latest() -> dict:
    row=engine.policy_curve_latest()
    if row is None:
        raise HTTPException(status_code=404, detail="no policy expectation curve snapshot")
    return row


@app.get("/api/experiments/policy-curve/history")
def policy_curve_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.policy_curve_history(limit)


@app.get("/api/experiments/latent-hazard/prospective/status")
def hazard_prospective_status() -> dict:
    return engine.hazard_prospective_status()


@app.get("/api/experiments/latent-hazard/prospective/latest")
def hazard_prospective_latest() -> dict:
    row=engine.hazard_prospective_latest()
    if row is None:
        raise HTTPException(status_code=404, detail="no prospective hazard shadow record")
    return row


@app.get("/api/experiments/latent-hazard/prospective/history")
def hazard_prospective_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.hazard_prospective_history(limit)


@app.get("/api/risk-warning/status")
def risk_warning_status() -> dict:
    return engine.risk_warning_status()


@app.get("/api/risk-warning/latest")
def risk_warning_latest() -> dict:
    row=engine.risk_warning_latest()
    if row is None:
        try:
            row=engine.risk_warning_run(False)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"{type(exc).__name__}:{exc}") from exc
    return row


@app.get("/api/risk-warning/history")
def risk_warning_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.risk_warning_history(limit)


@app.get("/api/risk-control/status")
def risk_control_status() -> dict:
    return engine.risk_control_status()


@app.get("/api/risk-control/latest")
def risk_control_latest() -> dict:
    row=engine.risk_control_latest()
    if row is None:
        try:
            row=engine.risk_control_run(False)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"{type(exc).__name__}:{exc}") from exc
    return row


@app.get("/api/risk-control/history")
def risk_control_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.risk_control_history(limit)


@app.get("/api/recovery-wave/status")
def recovery_wave_status(market_id: str = "CN") -> dict:
    return engine.recovery_wave_status(market_id)


@app.get("/api/recovery-wave/latest")
def recovery_wave_latest(market_id: str = "CN") -> dict:
    row=engine.latest_recovery_wave_decision(market_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no recovery-wave decision")
    return row


@app.get("/api/recovery-wave/history")
def recovery_wave_history(
    market_id: str = "CN",
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return engine.recovery_wave_history(market_id,limit)


@app.get("/api/strategies")
def strategies(
    lang: str = Query(default="zh", pattern="^(zh|en)$"),
    market_id: str | None = Query(default=None),
    run_id: str | None = None,
) -> list[dict]:
    latest_run=None
    if run_id:
        try:
            latest_run=engine.get_run(run_id)
        except (KeyError,FileNotFoundError) as exc:
            raise HTTPException(status_code=404,detail="run_id not found") from exc
        if market_id and latest_run.market.market_id.upper()!=market_id.upper():
            raise HTTPException(status_code=400,detail="run_id market does not match market_id")
        if latest_run.strategy_group is None or latest_run.triaid_decision is None:
            raise HTTPException(status_code=409,detail="run decision is not ready")
    elif market_id:
        market_key=market_id.upper()
        latest_run=engine.latest_decision_run(market_key)
    effective_market=(
        latest_run.market.market_id
        if latest_run
        else market_id
    )
    cards = engine.strategy_population.strategy_cards(lang, effective_market)
    state_map = {s.strategy_id: s for s in latest_run.strategy_states} if latest_run else {}
    group = latest_run.strategy_group if latest_run else None
    decision = latest_run.triaid_decision if latest_run else None
    selected = set(group.members) if group else set()

    out = []
    for card in cards:
        strategy_id = card["strategy_id"]
        state = state_map.get(strategy_id)
        row = dict(card)
        row.update(
            {
                "market_id": latest_run.market.market_id if latest_run else market_id,
                "as_of": latest_run.market.as_of if latest_run else None,
                "run_id":latest_run.run_id if latest_run else None,
                "run_scope":(
                    (latest_run.market.metadata or {}).get("run_scope","OFFICIAL_EVIDENCE")
                    if latest_run else None
                ),
                "evidence_eligible":(
                    (latest_run.market.metadata or {}).get("evidence_eligible") is not False
                    if latest_run else None
                ),
                "lifecycle": state.lifecycle if state else None,
                "expected_net_return": state.expected_net_return if state else None,
                "risk": state.risk if state else None,
                "uncertainty": state.uncertainty if state else None,
                "metrics": state.metrics if state else {},
                "selected": strategy_id in selected,
                "baseline_weight": group.weights.get(strategy_id, 0.0) if group else 0.0,
                "triaid_weight": decision.weights_after.get(strategy_id, 0.0) if decision else 0.0,
                "selection_reason": (
                    getattr(group.reasons[strategy_id], lang)
                    if group and strategy_id in group.reasons
                    else None
                ),
                "triaid_reason": (
                    getattr(decision.reasons[strategy_id], lang)
                    if decision and strategy_id in decision.reasons
                    else None
                ),
            }
        )
        out.append(row)
    return out


@app.get("/api/strategy-population/rules/{market_id}")
def population_rules(market_id: str) -> dict:
    return engine.strategy_population.rules(market_id)


@app.get("/api/population-state/{market_id}")
def population_state(market_id: str) -> dict:
    return engine.population_state.status(market_id)


@app.get("/api/evolution")
def evolution_status() -> dict:
    return engine.evolution_status()


@app.post("/api/evolution/propose")
def evolution_propose(_admin:None=Depends(require_admin_token)) -> dict:
    return engine.propose_core_candidate()


@app.post("/api/evolution/promote/{version}")
def evolution_promote(version: str, validation: dict[str, Any] = Body(default={}), _admin:None=Depends(require_admin_token)) -> dict:
    try:
        return engine.promote_core(version, validation)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="core version not found") from exc





@app.get("/api/strategy-evolution")
def strategy_evolution_status(market_id: str | None = None) -> dict:
    return engine.strategy_evolution_status(market_id)


@app.post("/api/strategy-evolution/propose/{market_id}")
def strategy_evolution_propose(market_id: str, _admin:None=Depends(require_admin_token)) -> dict:
    try:
        market_id=normalize_market_id(market_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return engine.propose_strategy_candidate(market_id)


@app.post("/api/strategy-evolution/promote/{market_id}/{version}")
def strategy_evolution_promote(
    market_id: str,
    version: str,
    validation: dict[str, Any] = Body(default={}),
    _admin:None=Depends(require_admin_token),
) -> dict:
    try:
        market_id=normalize_market_id(market_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return engine.promote_strategy_rules(market_id,version,validation)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy rule version not found") from exc


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TRIAID FIN Evolution Lab V2</title>
<style>
:root{
  font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
  color:#172033;background:#f5f7fa;
  --base:#5f6b7a;--triaid:#1769e0;--good:#138a4b;--bad:#c0392b;--warn:#b9770e;
  --line:#e1e6ec;--card:#fff;--soft-blue:#eef5ff;--soft-green:#edf8f1;--soft-red:#fff1ef;
}
*{box-sizing:border-box}body{margin:0}.wrap{max-width:1280px;margin:auto;padding:24px}
.top{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}
h1{margin:0;font-size:28px}h2{margin:30px 0 12px;font-size:20px}.muted{color:#748091}
.toolbar{display:flex;gap:8px;flex-wrap:wrap}
button,select{border:1px solid #cfd6df;background:#fff;border-radius:9px;padding:9px 13px;cursor:pointer}
button.primary{background:#172033;color:#fff;border-color:#172033}
.statusline{padding:9px 12px;border-radius:9px;background:#fff;border:1px solid var(--line);margin-top:12px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px}
.label{font-size:13px;color:#748091}.value{font-size:26px;font-weight:700;margin-top:6px}.sub{font-size:12px;color:#87909d;margin-top:4px}
.base{color:var(--base)}.triaid{color:var(--triaid)}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.compare{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.compare .card{min-height:112px}.compare .gain{border-width:2px}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.summary .item{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px}
.summary .item b{display:block;margin-top:4px;font-size:15px}
.prospective-panel{display:none;background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:10px}
.prospective-panel.show{display:block}.prospective-head{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}
.prospective-meta{font-size:12px;color:#748091;margin-top:5px}.prospective-kpis{margin-top:10px}
.prospective-kpis .item b{font-variant-numeric:tabular-nums}.prospective-note{font-size:12px;line-height:1.5;color:#5f6b7a;margin-top:10px}
canvas{width:100%;height:270px;background:#fff;border:1px solid var(--line);border-radius:12px}
.legend{display:flex;gap:18px;font-size:12px;margin:8px 0}.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px}
.tablewrap{overflow:auto;max-height:690px;border:1px solid var(--line);border-radius:12px;background:#fff}
table{width:100%;border-collapse:collapse;background:#fff}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #edf0f3;font-size:13px;vertical-align:top}
th{background:#f8fafc;position:sticky;top:0;z-index:1}.selected{background:#f6fbff}.tag{display:inline-block;padding:2px 7px;border-radius:999px;background:#eef1f5;font-size:11px}
.num{white-space:nowrap;font-variant-numeric:tabular-nums}.reason{min-width:320px;line-height:1.45}.strategy-name{font-weight:650}.strategy-hover{cursor:help;text-decoration-line:underline;text-decoration-style:dotted;text-decoration-color:#b8c2cf;text-underline-offset:3px}.market-tip-icon{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;margin-left:5px;border:1px solid #9aa8ba;border-radius:50%;font-size:10px;font-weight:700;color:#5f6f84;cursor:help;vertical-align:1px;background:#fff}
.delta{font-weight:700}.corebox{display:flex;gap:12px;flex-wrap:wrap}.corebox .card{flex:1;min-width:240px}
.small{font-size:12px}.nowrap{white-space:nowrap}
.livegrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.livepanel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px;min-height:250px}
.livehead{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:10px}
.livehead-left{display:flex;align-items:center;gap:8px;font-weight:700}
.pulse{width:10px;height:10px;border-radius:50%;background:#aeb7c4;box-shadow:0 0 0 0 rgba(19,138,75,.35)}
.pulse.on{background:var(--good);animation:pulse 1.8s infinite}.pulse.warn{background:var(--warn)}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(19,138,75,.32)}70%{box-shadow:0 0 0 8px rgba(19,138,75,0)}100%{box-shadow:0 0 0 0 rgba(19,138,75,0)}}
.indexgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px}
.indexitem{border:1px solid #edf0f3;border-radius:9px;padding:10px;background:#fbfcfe}
.indexitem .px{font-size:20px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.indexitem .chg{font-size:12px;margin-top:3px;font-variant-numeric:tabular-nums}
.commandlog{height:205px;overflow:auto;border:1px solid #edf0f3;border-radius:9px;background:#101722;color:#dbe6f5;padding:8px 10px;font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
.cmd{padding:3px 0;border-bottom:1px solid rgba(255,255,255,.06)}.cmd:last-child{border-bottom:0}
.cmdtime{color:#7f93aa}.cmdkind{color:#7fc7ff}.cmdmode{color:#ffd37f}
.live-meta{font-size:11px;color:#748091;margin-bottom:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.has-tip{cursor:help;text-decoration-line:underline;text-decoration-style:dotted;text-decoration-color:#aeb7c4;text-underline-offset:4px}.tip-mark::after{content:' ⓘ';font-size:10px;color:#7f8c9e;text-decoration:none;white-space:nowrap}
tbody tr:hover td{background:#f8fbff}
.home-summary{background:linear-gradient(180deg,#ffffff 0%,#f8fbff 100%);border:1px solid #d9e3f0;border-radius:16px;padding:16px 18px;margin:14px 0 12px;box-shadow:0 4px 18px rgba(31,55,86,.04)}
.home-summary-head{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}
.home-summary-kicker{font-size:11px;font-weight:800;letter-spacing:.08em;color:#1769e0;text-transform:uppercase}
.home-summary h2{font-size:20px;margin:3px 0 5px}
.home-summary-purpose{font-size:13px;line-height:1.6;color:#43546a;max-width:980px}
.home-summary-mode{font-size:11px;line-height:1.45;color:#66717f;background:#eef3f9;border-radius:999px;padding:5px 9px;white-space:nowrap}
.home-summary-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:9px;margin-top:13px}
.home-summary-card{border:1px solid #e4eaf1;border-radius:11px;background:#fff;padding:11px 12px;min-height:82px}
.home-summary-label{display:block;font-size:11px;color:#748091;margin-bottom:5px}
.home-summary-value{font-size:17px;font-weight:800;line-height:1.3;font-variant-numeric:tabular-nums}
.home-summary-value.open{color:#138a4b}.home-summary-value.closed{color:#66717f}
.home-summary-detail{font-size:11px;color:#748091;line-height:1.45;margin-top:4px}
.home-summary-risk{font-size:17px}
.home-summary-story{margin-top:11px;padding-top:10px;border-top:1px solid #e4eaf1;font-size:12px;line-height:1.6;color:#43546a}
.home-summary-guide{margin-top:4px;font-size:11px;color:#748091}
@media(max-width:950px){.home-summary-grid{grid-template-columns:repeat(2,1fr)}.home-summary-head{display:block}.home-summary-mode{display:inline-block;margin-top:8px}}
@media(max-width:600px){.home-summary-grid{grid-template-columns:1fr}}
.market-clock-strip{display:grid;grid-template-columns:repeat(3,minmax(190px,1fr));gap:10px;margin:12px 0}
.market-clock-card{appearance:none;width:100%;text-align:left;border:1px solid var(--line);border-radius:12px;background:#f5f6f8;padding:11px 12px;cursor:pointer;color:inherit}
.market-clock-card:hover{border-color:#b8c4d4;background:#fafcff}
.market-clock-card.selected{border-color:#6f94c9;box-shadow:0 0 0 2px rgba(23,105,224,.08)}
.market-clock-card.open{background:#eef8f2;border-color:#b9dfc9}
.market-clock-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.market-clock-name{font-size:13px;font-weight:750}.market-clock-phase{font-size:11px;color:#6f7b8a}
.market-clock-time{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums;margin-top:5px}
.market-clock-date{font-size:11px;color:#748091;margin-top:2px}
.market-dot{width:9px;height:9px;border-radius:50%;background:#aeb7c4;display:inline-block;margin-right:6px;vertical-align:0}
.market-clock-card.open .market-dot{background:#138a4b;box-shadow:0 0 0 4px rgba(19,138,75,.10)}
.market-hero{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;background:#fff;border:1px solid var(--line);border-radius:14px;padding:15px 16px;margin:10px 0 18px}
.market-hero h2{margin:2px 0 4px;font-size:22px}.market-kicker{font-size:11px;color:#748091;text-transform:uppercase;letter-spacing:.06em}
.market-route{font-size:13px;line-height:1.5;color:#445164}.market-hero-side{text-align:right;min-width:230px}
.market-session-badge{display:inline-flex;align-items:center;padding:4px 9px;border-radius:999px;background:#eef1f5;color:#66717f;font-size:12px;font-weight:750}
.market-session-badge.open{background:#e7f6ed;color:#138a4b}
.market-hero-meta{font-size:11px;color:#748091;margin-top:7px;line-height:1.5}
.market-section-note{font-size:12px;color:#748091;margin:-6px 0 10px}
@media(max-width:760px){.market-clock-strip{grid-template-columns:1fr}.market-hero{display:block}.market-hero-side{text-align:left;min-width:0;margin-top:10px}}
#hoverTip{position:fixed;display:none;z-index:9999;max-width:430px;padding:9px 11px;border-radius:8px;background:#172033;color:#fff;font-size:12px;line-height:1.5;white-space:pre-line;box-shadow:0 8px 24px rgba(0,0,0,.18);pointer-events:none}
.riskpanel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin:16px 0}
.riskhead{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
.riskhead h2{margin:0}.risk-meta{font-size:12px;color:#748091;margin-top:5px}
.risk-overall{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}.risk-score{font-size:34px;font-weight:800;font-variant-numeric:tabular-nums}
.risk-band{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:700;background:#eef1f5}
.risk-band.low{background:#edf8f1;color:#138a4b}.risk-band.elevated{background:#fff7df;color:#946200}.risk-band.high{background:#fff0df;color:#ad5b00}.risk-band.severe{background:#fff1ef;color:#b42318}.risk-band.critical{background:#5a1520;color:#fff}
.risk-number{display:inline-block;border-radius:7px;padding:1px 6px;font-weight:800;font-variant-numeric:tabular-nums}
.risk-number.low{color:#138a4b;background:#edf8f1}.risk-number.elevated{color:#946200;background:#fff7df}.risk-number.high{color:#ad5b00;background:#fff0df}.risk-number.severe{color:#b42318;background:#fff1ef}.risk-number.critical{color:#fff;background:#5a1520}
.risk-cell-number{font-weight:750;font-variant-numeric:tabular-nums;border-radius:5px;padding:2px 5px;display:inline-block}
.risk-cell-number.low{color:#138a4b;background:#edf8f1}.risk-cell-number.elevated{color:#946200;background:#fff7df}.risk-cell-number.high{color:#ad5b00;background:#fff0df}.risk-cell-number.severe{color:#b42318;background:#fff1ef}.risk-cell-number.critical{color:#fff;background:#5a1520}
.risk-color-legend{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px;font-size:11px;color:#6b7685}
.risk-color-legend .risk-band{padding:2px 7px;font-size:10px}
.risk-band-inline{display:inline-block;margin-top:4px;font-weight:700}.risk-band-inline.low{color:#138a4b}.risk-band-inline.elevated{color:#946200}.risk-band-inline.high{color:#ad5b00}.risk-band-inline.severe{color:#b42318}.risk-band-inline.critical{color:#7a1730}
.riskgrid{display:grid;grid-template-columns:repeat(5,minmax(125px,1fr));gap:9px;margin-top:12px}
.riskcell{border:1px solid #edf0f3;border-radius:10px;padding:10px;background:#fbfcfe}.riskcell .rv{font-size:21px;font-weight:750;margin-top:4px;font-variant-numeric:tabular-nums}
.riskdetailgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.riskbox{border:1px solid #edf0f3;border-radius:10px;padding:11px}.riskbox h3{margin:0 0 7px;font-size:14px}
.risklist{margin:0;padding-left:18px;font-size:12px;line-height:1.55}.risklist li{margin:3px 0}
.risk-subgrid{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:8px;margin-top:10px}.risk-sub{border:1px solid #edf0f3;border-radius:8px;padding:9px}.risk-sub b{display:block;font-size:18px;margin-top:3px}
.risknote{font-size:12px;line-height:1.55;color:#5f6b7a;margin-top:10px}
.risk-center-banner{margin-top:10px;padding:10px 12px;border:1px solid #dfe7f2;border-radius:10px;background:#f6f9fd;font-size:12px;line-height:1.55;color:#43546a}
.risk-center-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.risk-center-table{max-height:360px}
.risk-stage{font-weight:750}.risk-stage.watch{color:#946200}.risk-stage.tighten{color:#b42318}.risk-stage.defensive{color:#7a1730}
.chain-active{font-weight:750;color:#b42318}.chain-pending{color:#946200}.chain-normal{color:#138a4b}
@media(max-width:900px){.risk-center-grid{grid-template-columns:1fr}}
@media(max-width:900px){.riskgrid,.risk-subgrid{grid-template-columns:repeat(2,1fr)}.riskdetailgrid{grid-template-columns:1fr}}
@media(max-width:760px){.compare,.livegrid{grid-template-columns:1fr}.wrap{padding:15px}th,td{font-size:12px}.reason{min-width:240px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1 id="title">TRIAID FIN 进化实验台 V2</h1>
      <div class="muted" id="subtitle">真实市场 → 动态策略群 → TRIAID Core → 后验验证 → 持续进化</div>
    </div>
    <div class="toolbar">
      <select id="market" onchange="onMarketChange()"><option value="US">美股 / US</option><option value="CN">A股 / CN</option><option value="HK">港股 / HK</option></select>
      <button class="primary" onclick="runNow()" id="runBtn">立即执行</button>
      <button onclick="runAll()" id="runAllBtn">预览三个市场</button>
      <button onclick="toggleLang()">中文 / English</button>
    </div>
  </div>
  <div class="statusline" id="runStatus">Ready</div>
  <div class="statusline" id="marketScopeStatus" style="display:none"></div>

  <section class="home-summary" id="homeSummary" aria-labelledby="homeSummaryTitle">
    <div class="home-summary-head">
      <div>
        <div class="home-summary-kicker" id="homeSummaryKicker">START HERE</div>
        <h2 id="homeSummaryTitle">第一次看 TRIAID FIN？先看这里</h2>
        <div class="home-summary-purpose" id="homeSummaryPurpose">TRIAID FIN 用真实市场数据动态选择和调整策略权重，再等待真实后验结果验证这些调整是否真正提高了可实现净收益。重点不是预测某一天涨跌，而是把“选择—调整—验证—进化”做成可回溯的长期实验。</div>
      </div>
      <div class="home-summary-mode" id="homeSummaryMode">研究 / Shadow · 不连接券商 · 不自动交易</div>
    </div>
    <div class="home-summary-grid">
      <div class="home-summary-card">
        <span class="home-summary-label" id="homeSummaryMarketLabel">当前查看</span>
        <div class="home-summary-value closed" id="homeSummaryMarket">美股 / US · -</div>
        <div class="home-summary-detail" id="homeSummaryMarketDetail">等待市场时钟</div>
      </div>
      <div class="home-summary-card">
        <span class="home-summary-label" id="homeSummaryDecisionLabel">TRIAID 当前在做什么</span>
        <div class="home-summary-value" id="homeSummaryDecision">等待策略数据</div>
        <div class="home-summary-detail" id="homeSummaryDecisionDetail">读取当前策略群与动态权重</div>
      </div>
      <div class="home-summary-card">
        <span class="home-summary-label" id="homeSummaryValidationLabel">最近一次真实验证</span>
        <div class="home-summary-value" id="homeSummaryValidation">等待真实后验</div>
        <div class="home-summary-detail" id="homeSummaryValidationDetail">只有已经发生的真实结果才进入这里</div>
      </div>
      <div class="home-summary-card">
        <span class="home-summary-label" id="homeSummaryRiskLabel">三市场联动风险</span>
        <div class="home-summary-value" id="homeSummaryRisk">读取中</div>
        <div class="home-summary-detail" id="homeSummaryRiskDetail">US / A股 / 港股联合风险层 · Shadow-only</div>
      </div>
    </div>
    <div class="home-summary-story" id="homeSummaryStory">本页阅读顺序：先确认当前市场 → 看 TRIAID 选了什么、改了什么 → 看真实后验是否增值 → 最后看三市场联合风险与深层实验。</div>
    <div class="home-summary-guide" id="homeSummaryGuide">下面的复杂表格用于追溯证据；第一次使用不需要逐项读完。</div>
  </section>

  <div class="market-clock-strip" id="marketClockStrip" aria-label="Market clocks">
    <button type="button" class="market-clock-card selected" data-clock-market="US" onclick="selectMarket('US')" data-tip="美股纽约时间。用途：确认当前页面是不是处于官方交易时段。绿色时重点看实时价格、调度和盘中变化；灰色时主要看最近完整交易日和冻结结果，不要把非交易状态误判成数据故障。">
      <div class="market-clock-top"><span class="market-clock-name"><span class="market-dot"></span>美股 / US</span><span class="market-clock-phase" id="clockPhaseUS">-</span></div>
      <div class="market-clock-time" id="clockTimeUS">--:--:--</div><div class="market-clock-date" id="clockDateUS">America/New_York</div>
    </button>
    <button type="button" class="market-clock-card" data-clock-market="CN" onclick="selectMarket('CN')" data-tip="A股上海时间。用途：确认现在是否处于连续交易时段。绿色时可判断实时数据和盘中调整链；灰色时优先看最近完整交易日后验，午休或休市本身不代表系统异常。">
      <div class="market-clock-top"><span class="market-clock-name"><span class="market-dot"></span>A股 / CN</span><span class="market-clock-phase" id="clockPhaseCN">-</span></div>
      <div class="market-clock-time" id="clockTimeCN">--:--:--</div><div class="market-clock-date" id="clockDateCN">Asia/Shanghai</div>
    </button>
    <button type="button" class="market-clock-card" data-clock-market="HK" onclick="selectMarket('HK')" data-tip="港股香港时间。用途：确认当前是否处于港股连续交易时段。绿色时关注实时价格和策略状态；灰色时以最近完整交易日结果为准，不应因为休市而期待实时数据继续变化。">
      <div class="market-clock-top"><span class="market-clock-name"><span class="market-dot"></span>港股 / HK</span><span class="market-clock-phase" id="clockPhaseHK">-</span></div>
      <div class="market-clock-time" id="clockTimeHK">--:--:--</div><div class="market-clock-date" id="clockDateHK">Asia/Hong_Kong</div>
    </button>
  </div>

  <section class="market-hero" id="marketHero">
    <div>
      <div class="market-kicker" id="selectedMarketKicker">当前市场</div>
      <h2 id="selectedMarketName">美股 / US</h2>
      <div class="market-route" id="selectedMarketRoute">正在读取当前市场主路线…</div>
    </div>
    <div class="market-hero-side">
      <span class="market-session-badge" id="selectedMarketSession">-</span>
      <div class="market-hero-meta" id="selectedMarketMeta">-</div>
    </div>
  </section>
  <div class="market-section-note" id="marketSectionNote">以下先展示当前所选市场的结果、行情、策略池和该市场专属实验。三市场联动风险中心放在单市场内容之后。</div>

  <h2 id="resultTitle">TRIAID 结果比较</h2>
  <div class="compare">
    <div class="card">
      <div class="label" id="baseReturnLabel">基线组合后验收益</div>
      <div class="value base" id="baseReturn">等待后验</div>
      <div class="sub" id="baseReturnSub">冻结基线</div>
    </div>
    <div class="card">
      <div class="label" id="triaidReturnLabel">TRIAID 配置后验收益</div>
      <div class="value triaid" id="triaidReturn">等待后验</div>
      <div class="sub" id="triaidReturnSub">冻结 TRIAID 配置</div>
    </div>
    <div class="card gain" id="gainCard">
      <div class="label" id="gainLabel">TRIAID 相对收益差</div>
      <div class="value" id="gain">等待后验</div>
      <div class="sub" id="gainSub">TRIAID 配置后验收益 − 基线后验收益</div>
    </div>
  </div>

  <h2 id="liveTitle">市场数据与后台运行指示</h2>
  <div class="livegrid">
    <div class="livepanel">
      <div class="livehead">
        <div class="livehead-left"><span id="marketPulse" class="pulse"></span><span id="indexWindowTitle">最新市场标的窗口</span></div>
        <span class="small muted" id="indexPhase">-</span>
      </div>
      <div class="live-meta" id="indexMeta">等待实时市场数据</div>
      <div class="indexgrid" id="indexRows"></div>
    </div>
    <div class="livepanel">
      <div class="livehead">
        <div class="livehead-left"><span id="activityPulse" class="pulse"></span><span id="activityWindowTitle">后台运行事件</span></div>
        <span class="small muted" id="activityPhase">-</span>
      </div>
      <div class="live-meta" id="scheduleMeta">等待调度状态</div>
      <div class="commandlog" id="commandLog"></div>
    </div>
  </div>

  <h2 id="strategyTitle">策略群与 TRIAID 调整</h2>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th id="thStrategy" class="has-tip">策略</th>
        <th id="thState" class="has-tip">状态</th>
        <th id="thExp" class="has-tip">多周期年化收益估计</th>
        <th id="thRisk" class="has-tip">风险</th>
        <th id="thBase" class="has-tip">基线权重</th>
        <th id="thTriaid" class="has-tip">TRIAID 权重</th>
        <th id="thDelta" class="has-tip">权重变化</th>
        <th id="thWhy" class="has-tip">策略说明与选择原因</th>
      </tr></thead>
      <tbody id="strategyRows"></tbody>
    </table>
  </div>
  <details style="margin-top:12px">
    <summary id="candidatePoolTitle" style="cursor:pointer;color:#748091">查看未入选候选策略池</summary>
    <div class="tablewrap" style="margin-top:10px;max-height:420px">
      <table>
        <thead><tr>
          <th id="cthStrategy" class="has-tip">策略</th>
          <th id="cthState" class="has-tip">状态</th>
          <th id="cthExp" class="has-tip">多周期年化收益估计</th>
          <th id="cthRisk" class="has-tip">风险</th>
          <th id="cthWhy" class="has-tip">说明</th>
        </tr></thead>
        <tbody id="candidateRows"></tbody>
      </table>
    </div>
  </details>

  <h2 id="dailyTitle">最新数据摘要</h2>
  <div class="summary">
    <div class="item"><span class="label" id="regimeLabel">市场状态</span><b id="regime">-</b></div>
    <div class="item"><span class="label" id="runStateLabel">运行状态</span><b id="runState">-</b></div>
    <div class="item"><span class="label" id="selectedNamesLabel">当前入选</span><b id="selectedNames">-</b></div>
    <div class="item"><span class="label" id="dailyAnalysisLabel">最新后验结论</span><b id="dailyAnalysis">-</b></div>
  </div>

  <div class="prospective-panel" id="usReturnMaxPanel">
    <div class="prospective-head">
      <div>
        <b id="usReturnMaxTitle">美股 Return-Max 路线</b>
        <div class="prospective-meta" id="usReturnMaxMeta">-</div>
      </div>
      <span class="tag" id="usReturnMaxStatus">-</span>
    </div>
    <div class="summary prospective-kpis">
      <div class="item"><span class="label" id="usrmExpectedLabel">Return-Max 多周期年化状态估计</span><b id="usrmExpected">-</b></div>
      <div class="item"><span class="label" id="usrmGenericLabel">通用 Core 多周期年化状态估计</span><b id="usrmGeneric">-</b></div>
      <div class="item"><span class="label" id="usrmSpyLabel">SPY 多周期年化状态估计</span><b id="usrmSpy">-</b></div>
      <div class="item"><span class="label" id="usrmRiskLabel">目标风险仓位</span><b id="usrmRisk">-</b></div>
    </div>
    <div class="prospective-note" id="usReturnMaxNote">-</div>
    <h3 id="usrmStrategyTitle">当前冻结策略权重</h3>
    <div class="tablewrap" style="max-height:330px">
      <table>
        <thead><tr><th>策略</th><th>Return-Max 权重</th><th>通用 Core 权重</th></tr></thead>
        <tbody id="usrmStrategyRows"></tbody>
      </table>
    </div>
    <h3 id="usrmAssetTitle">底层 ETF 目标敞口</h3>
    <div class="tablewrap" style="max-height:280px">
      <table>
        <thead><tr><th>ETF</th><th>目标权重</th></tr></thead>
        <tbody id="usrmAssetRows"></tbody>
      </table>
    </div>
    <h3 id="usrmCapitalTitle">四档美元资金规模容量实验</h3>
    <div class="prospective-meta" id="usrmCapitalMeta">-</div>
    <div class="tablewrap" style="max-height:330px">
      <table>
        <thead><tr><th>起始资金</th><th>目标投入</th><th>最大目标仓位/ADV</th><th>最少成交天数</th><th>模型往返成本代理</th></tr></thead>
        <tbody id="usrmCapitalRows"></tbody>
      </table>
    </div>
    <h3 id="usrmRealizedTitle">上一轮真实市场后验与模拟执行容量回顾</h3>
    <div class="tablewrap" style="max-height:360px">
      <table>
        <thead><tr><th>起始资金</th><th>模拟成交比例</th><th>模拟当前净值</th><th>模拟净损益</th><th>模拟净收益率</th><th>模型执行成本</th></tr></thead>
        <tbody id="usrmRealizedRows"></tbody>
      </table>
    </div>
    <h3 id="usrmControlTitle">上一轮冻结配置理论持仓后验路径</h3>
    <div class="tablewrap" style="max-height:330px">
      <table>
        <thead><tr><th>结果日</th><th>Return-Max 累计</th><th>通用 Core 累计</th><th>SPY 累计</th></tr></thead>
        <tbody id="usrmDailyRows"></tbody>
      </table>
    </div>
  </div>

  <div class="prospective-panel" id="prospectivePanel">
    <div class="prospective-head">
      <div>
        <b id="prospectiveTitle">A股前瞻对照实验</b>
        <div class="prospective-meta" id="prospectiveMeta">-</div>
      </div>
      <span class="tag" id="prospectiveStatus">-</span>
    </div>
    <div class="summary prospective-kpis">
      <div class="item"><span class="label" id="prospectiveDaysLabel">已观察交易日</span><b id="prospectiveDays">0</b></div>
      <div class="item"><span class="label" id="prospectiveHoldLabel">最差池累计收益</span><b id="prospectiveHold">-</b></div>
      <div class="item"><span class="label" id="prospectiveTriaidLabel">TRIAID冻结配置累计收益</span><b id="prospectiveTriaid">-</b></div>
      <div class="item"><span class="label" id="prospectiveGapLabel">TRIAID相对最差池</span><b id="prospectiveGap">-</b></div>
    </div>
    <div class="prospective-note" id="prospectiveNote">-</div>
    <h3 id="prospectiveStrategyTitle">策略确定与冻结排序</h3>
    <div class="tablewrap" style="max-height:430px">
      <table>
        <thead><tr>
          <th id="pthStrategy">策略</th>
          <th id="pthPredRank">TRIAID冻结综合排序</th>
          <th id="pthBaseWeight">基线权重</th>
          <th id="pthTriaidWeight">TRIAID权重</th>
          <th id="pthDailyReturn">最近一日</th>
          <th id="pthCumReturn">累计收益</th>
          <th id="pthRealRank">当前实际名次</th>
          <th id="pthReason">确定依据</th>
        </tr></thead>
        <tbody id="prospectiveStrategyRows"></tbody>
      </table>
    </div>
    <h3 id="prospectiveDailyTitle">每日波动轨迹</h3>
    <div class="tablewrap" style="max-height:360px">
      <table>
        <thead id="prospectiveDailyHead"></thead>
        <tbody id="prospectiveDailyRows"></tbody>
      </table>
    </div>
  </div>

  <div class="prospective-panel" id="recoveryWavePanel">
    <div class="prospective-head">
      <div>
        <b id="recoveryWaveTitle">TRIAID 二阶恢复波段研究</b>
        <div class="prospective-meta" id="recoveryWaveMeta">-</div>
      </div>
      <span class="tag" id="recoveryWaveStatus">-</span>
    </div>
    <div class="summary prospective-kpis">
      <div class="item"><span class="label" id="recoveryCashLabel">目标现金权重</span><b id="recoveryCash">-</b></div>
      <div class="item"><span class="label" id="recoveryPrevDaysLabel">上一轮后验交易日</span><b id="recoveryPrevDays">-</b></div>
      <div class="item"><span class="label" id="recoveryPrevReturnLabel">上一轮冻结组合后验收益</span><b id="recoveryPrevReturn">-</b></div>
      <div class="item"><span class="label" id="recoveryPrevGapLabel">上一轮相对等权差值</span><b id="recoveryPrevGap">-</b></div>
    </div>
    <div class="prospective-note" id="recoveryWaveNote">-</div>
    <h3 id="recoveryOpinionTitle">当前冻结研究配置意见</h3>
    <div class="tablewrap" style="max-height:430px">
      <table>
        <thead><tr>
          <th id="rwthProduct">产品</th>
          <th id="rwthAction">研究意见</th>
          <th id="rwthTarget">目标权重</th>
          <th id="rwthChange">本轮调整</th>
          <th id="rwthDrawdown">252日回撤</th>
          <th id="rwthDirection">状态方向</th>
          <th id="rwthHorizon">首个正向历史窗口</th>
          <th id="rwthSpeed">历史收益优势速度/日</th>
          <th id="rwthHitEdge">恢复率优势×样本支持</th>
          <th id="rwthExpected">历史相似状态平均前向收益</th>
          <th id="rwthSamples">样本</th>
        </tr></thead>
        <tbody id="recoveryOpinionRows"></tbody>
      </table>
    </div>
    <h3 id="capitalSleeveTitle">四档人民币资金规模容量实验</h3>
    <div class="prospective-meta" id="capitalSleeveMeta">-</div>
    <div class="tablewrap" style="max-height:330px">
      <table>
        <thead><tr>
          <th id="csthCapital">起始资金</th>
          <th id="csthInvested">目标投入</th>
          <th id="csthParticipation">最大目标仓位/ADV</th>
          <th id="csthDays">最少成交天数</th>
          <th id="csthCost">模型往返成本代理</th>
          <th id="csthPnl">模型波段净损益代理</th>
          <th id="csthReturn">模型波段净收益率代理</th>
        </tr></thead>
        <tbody id="capitalSleeveRows"></tbody>
      </table>
    </div>
    <h3 id="capitalRealizedTitle">上一轮四档资金袖套模拟执行回顾</h3>
    <div class="tablewrap" style="max-height:330px">
      <table>
        <thead><tr>
          <th id="crthCapital">起始资金</th>
          <th id="crthFill">模拟成交比例</th>
          <th id="crthEquity">模拟当前净值</th>
          <th id="crthPnl">模拟净损益</th>
          <th id="crthReturn">模拟净收益率</th>
          <th id="crthCost">累计模型执行成本</th>
          <th id="crthRemaining">未成交目标</th>
        </tr></thead>
        <tbody id="capitalRealizedRows"></tbody>
      </table>
    </div>
    <h3 id="recoveryReviewTitle">上一轮冻结配置真实市场后验</h3>
    <div class="prospective-meta" id="recoveryPreviousMeta">-</div>
    <div class="tablewrap" style="max-height:360px">
      <table>
        <thead><tr>
          <th id="rvrDate">结果日</th>
          <th id="rvrPortfolio">冻结组合当日</th>
          <th id="rvrEqual">等权对照当日</th>
          <th id="rvrCum">冻结组合累计</th>
          <th id="rvrGap">累计差值</th>
        </tr></thead>
        <tbody id="recoveryReviewRows"></tbody>
      </table>
    </div>
  </div>

  <div class="market-section-note" id="crossMarketRiskNote">以下为跨市场联动风险层。它始终联合分析 US、A股、港股，不随上方单市场选择器切换。</div>
  <section class="riskpanel" id="riskWarningPanel">
    <div class="riskhead">
      <div>
        <h2 id="riskWarningTitle">TRIAID 三市场联动风险中心</h2>
        <div class="risk-meta" id="riskWarningMeta">等待风险状态</div>
        <div class="risk-center-banner" id="riskCenterScope">独立于下方美股 / A股 / 港股单市场页面：这里始终联合分析三个市场，并作为风控 shadow 实验输入，不是第四个市场。</div>
      </div>
      <div>
        <div class="risk-overall"><span class="risk-score" id="riskOverallScore">-</span><span>/100</span><span class="risk-band" id="riskOverallBand">-</span></div>
        <div class="risk-meta" id="riskConfidence">-</div>
        <div class="risk-color-legend" id="riskColorLegend">
          <span>颜色阈值：</span>
          <span class="risk-band low">低 0–24.9</span>
          <span class="risk-band elevated">升高 25–44.9</span>
          <span class="risk-band high">高 45–64.9</span>
          <span class="risk-band severe">严重 65–79.9</span>
          <span class="risk-band critical">临界 80–100</span>
        </div>
      </div>
    </div>
    <div class="riskgrid">
      <div class="riskcell"><span class="label" id="riskOverallLabel">当前综合风险</span><div class="rv" id="riskOverallMini">-</div></div>
      <div class="riskcell"><span class="label">20日</span><div class="rv" id="risk20">-</div><div class="small muted" id="risk20Band">-</div></div>
      <div class="riskcell"><span class="label">60日</span><div class="rv" id="risk60">-</div><div class="small muted" id="risk60Band">-</div></div>
      <div class="riskcell"><span class="label">120日</span><div class="rv" id="risk120">-</div><div class="small muted" id="risk120Band">-</div></div>
      <div class="riskcell"><span class="label">250日</span><div class="rv" id="risk250">-</div><div class="small muted" id="risk250Band">-</div></div>
    </div>
    <div class="risk-subgrid">
      <div class="risk-sub"><span class="label" id="riskStructuralLabel">长期结构脆弱</span><b id="riskStructural">-</b></div>
      <div class="risk-sub"><span class="label" id="riskRatesLabel">利率/政策压力</span><b id="riskRates">-</b></div>
      <div class="risk-sub"><span class="label" id="riskTransmissionLabel">跨市场传导</span><b id="riskTransmission">-</b></div>
      <div class="risk-sub"><span class="label" id="riskCreditLabel">信用/流动性</span><b id="riskCredit">-</b></div>
      <div class="risk-sub"><span class="label" id="riskMarketLabel">价格结构恶化</span><b id="riskMarket">-</b></div>
    </div>
    <div class="risk-center-grid">
      <div class="riskbox">
        <h3 id="riskThreeMarketTitle">三市场联动状态</h3>
        <div class="tablewrap risk-center-table">
          <table>
            <thead><tr><th>市场</th><th>252日回撤压力</th><th>63日负向动量特征</th><th>动量异常分位</th><th>63日波动</th><th>风控实验阶段</th></tr></thead>
            <tbody id="riskThreeMarketRows"></tbody>
          </table>
        </div>
      </div>
      <div class="riskbox">
        <h3 id="riskDynamicsTitle">动力链 / 传导路径</h3>
        <div class="tablewrap risk-center-table">
          <table>
            <thead><tr><th>环节</th><th>状态</th><th>强度</th><th>主要证据</th></tr></thead>
            <tbody id="riskDynamicsRows"></tbody>
          </table>
        </div>
      </div>
      <div class="riskbox">
        <h3 id="riskMacroTitle">利率、政策、信用与流动性</h3>
        <div class="tablewrap risk-center-table">
          <table>
            <thead><tr><th>指标</th><th>当前值</th><th>历史状态分位</th></tr></thead>
            <tbody id="riskMacroRows"></tbody>
          </table>
        </div>
      </div>
      <div class="riskbox">
        <h3 id="riskTermTitle">Fed Funds / SOFR 期限曲线</h3>
        <div class="tablewrap risk-center-table">
          <table>
            <thead><tr><th>曲线</th><th>真实合约点</th><th>前端隐含利率</th><th>远端隐含利率</th><th>远端−前端</th></tr></thead>
            <tbody id="riskTermRows"></tbody>
          </table>
        </div>
        <details style="margin-top:8px">
          <summary id="riskCurveDetailTitle" style="cursor:pointer;color:#5f6b7a">展开期限合约明细</summary>
          <div class="tablewrap risk-center-table" style="margin-top:8px">
            <table>
              <thead><tr><th>曲线</th><th>合约月</th><th>代码</th><th>价格</th><th>隐含利率</th></tr></thead>
              <tbody id="riskCurveContractRows"></tbody>
            </table>
          </div>
        </details>
      </div>
      <div class="riskbox">
        <h3 id="riskHistoryTableTitle">历史危机回溯与统计支持</h3>
        <div class="tablewrap risk-center-table">
          <table>
            <thead><tr><th>联合状态</th><th>领先期</th><th>危机命中</th><th>正常误报</th><th>Lift</th><th>p</th><th>q</th><th>稳健性</th></tr></thead>
            <tbody id="riskHistoryRows"></tbody>
          </table>
        </div>
      </div>
      <details class="riskbox">
        <summary id="riskDataQualityTitle" style="cursor:pointer;font-weight:700">数据完整性与降级状态</summary>
        <div class="small" id="riskDataQuality" style="margin-top:8px">-</div>
        <ul class="risklist" id="riskDataGaps"></ul>
      </details>
      <div class="riskbox">
        <h3 id="riskControlTitle">三市场风控 Shadow 实验</h3>
        <div class="risknote" id="riskControlMeta">-</div>
        <div class="tablewrap risk-center-table" style="margin-top:8px">
          <table>
            <thead><tr><th>市场</th><th>实验阶段</th><th>风险敞口倍率候选</th><th>防御敞口底线候选</th><th>是否已作用生产权重</th></tr></thead>
            <tbody id="riskControlRows"></tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="riskdetailgrid">
      <div class="riskbox"><h3 id="riskDriversTitle">主要风险驱动</h3><ul class="risklist" id="riskDrivers"></ul></div>
      <div class="riskbox"><h3 id="riskBlockersTitle">尚未确认 / 风险阻断项</h3><ul class="risklist" id="riskBlockers"></ul></div>
    </div>
    <details style="margin-top:10px">
      <summary id="riskDetailsTitle" style="cursor:pointer;color:#5f6b7a">展开完整证据与验证状态</summary>
      <div class="riskdetailgrid">
        <div class="riskbox"><h3 id="riskHistoricalTitle">历史支持</h3><div class="small" id="riskHistorical">-</div></div>
        <div class="riskbox"><h3 id="riskProspectiveTitle">前瞻验证</h3><div class="small" id="riskProspective">-</div><div class="small muted" id="riskLedgerDetail" style="margin-top:6px">-</div></div>
        <div class="riskbox"><h3 id="riskEscalationTitle">升级条件</h3><ul class="risklist" id="riskEscalation"></ul></div>
        <div class="riskbox"><h3 id="riskDeescalationTitle">降级条件</h3><ul class="risklist" id="riskDeescalation"></ul></div>
      </div>
      <div class="risknote" id="riskSourceStatus">-</div>
      <div class="risknote" id="riskSemantics">风险指数是状态/证据压力评分，不是股灾概率；风险预警层不自动改变组合权重。</div>
    </details>
  </section>

  <h2 id="overviewTitle">当前状态</h2>
  <div class="grid">
    <div class="card"><div class="label" id="dateLabel">最新数据日</div><div class="value" id="date">-</div></div>
    <div class="card"><div class="label" id="coreLabel">通用 Core</div><div class="value triaid" id="core">-</div></div>
    <div class="card"><div class="label" id="selectedLabel">当前入选策略数</div><div class="value" id="selectedCount">-</div></div>
    <div class="card"><div class="label" id="cumLabel">累计单期超额和</div><div class="value" id="cumExcess">-</div></div>
  </div>

  <h2 id="curveTitle">连续回顾</h2>
  <div class="legend">
    <span><span class="dot" style="background:#6f7782"></span><span id="legendBase">策略群基线</span></span>
    <span><span class="dot" style="background:#1769e0"></span><span id="legendTriaid">TRIAID</span></span>
  </div>
  <canvas id="curve" width="1220" height="270"></canvas>

  <h2 id="evolutionTitle">Core 进化状态</h2>
  <div class="corebox">
    <div class="card">
      <div class="label" id="evoObservedLabel">已后验评价运行</div>
      <div class="value" id="evoObserved">0</div>
      <div class="sub" id="evoMean">尚无可评价结果</div>
    </div>
    <div class="card">
      <div class="label" id="evoNegLabel">负相对收益差比例</div>
      <div class="value" id="evoNeg">-</div>
      <div class="sub" id="evoNote">Core 会根据持续后验评价形成候选改进</div>
    </div>
    <div class="card">
      <div class="label" id="evoCandidateLabel">下一步</div>
      <div class="sub" id="evoLast">尚无 Candidate</div>
      <br><button onclick="propose()" id="proposeBtn">生成 Candidate Core</button>
    </div>
  </div>
</div>
<div id="hoverTip" role="tooltip"></div>

<script>
let lang='zh';
let strategyMarketContext={};
let strategyNameIndex={US:{},CN:{},HK:{}};
let previewRunIds={US:null,CN:null,HK:null};
const el=id=>document.getElementById(id);
const T={
 zh:{
  title:'TRIAID FIN 进化实验台 V2',subtitle:'真实市场 → 动态策略群 → TRIAID Core → 后验验证 → 持续进化',
  result:'TRIAID 结果比较',baseReturn:'基线组合后验收益',triaidReturn:'TRIAID 配置后验收益',gain:'TRIAID 相对收益差',
  live:'市场数据与后台运行指示',indexWindow:'最新市场标的窗口',activityWindow:'后台运行事件',
  baseSub:'冻结基线',triaidSub:'冻结 TRIAID 配置',gainSub:'TRIAID 配置后验收益 − 基线后验收益',
  overview:'当前状态',date:'最新数据日',core:'通用 Core',selected:'当前入选策略数',cum:'累计单期超额和',
  curve:'连续回顾',legendBase:'策略群基线',legendTriaid:'TRIAID',
  daily:'最新数据摘要',regime:'市场状态',runState:'运行状态',selectedNames:'当前入选',analysis:'最新后验结论',
  usReturnMax:'美股 Return-Max 路线',usrmExpected:'Return-Max 多周期年化状态估计',usrmGeneric:'通用 Core 多周期年化状态估计',usrmSpy:'SPY 多周期年化状态估计',usrmRisk:'目标风险仓位',usrmStrategy:'当前冻结策略权重',usrmAsset:'底层 ETF 目标敞口',usrmCapital:'四档美元资金规模容量实验',usrmRealized:'上一轮真实市场后验与模拟执行容量回顾',usrmControl:'上一轮冻结配置理论持仓后验路径',
  prospective:'A股前瞻对照实验',prospectiveDays:'已观察交易日',prospectiveHold:'最差池累计收益',prospectiveTriaid:'TRIAID冻结配置累计收益',prospectiveGap:'TRIAID相对最差池',
  prospectiveStrategy:'策略确定与冻结排序',prospectiveDaily:'每日波动轨迹',predRank:'TRIAID冻结综合排序',dailyReturn:'最近一日',cumReturn:'累计收益',realRank:'当前实际名次',detReason:'确定依据',
  recoveryWave:'TRIAID 二阶恢复波段研究',recoveryCash:'目标现金权重',recoveryPrevDays:'上一轮后验交易日',recoveryPrevReturn:'上一轮冻结组合后验收益',recoveryPrevGap:'上一轮相对等权差值',
  recoveryOpinion:'当前冻结研究配置意见',recoveryReview:'上一轮冻结配置真实市场后验',product:'产品',action:'研究意见',target:'目标权重',change:'本轮调整',drawdown:'252日回撤',direction:'状态方向',horizon:'首个正向历史窗口',speed:'历史收益优势速度/日',hitEdge:'恢复率优势×样本支持',expected:'历史相似状态平均前向收益',samples:'样本',resultDate:'结果日',portfolioDay:'冻结组合当日',equalDay:'等权对照当日',portfolioCum:'冻结组合累计',gapCum:'累计差值',
  capitalSleeve:'四档人民币资金规模容量实验',capitalRealized:'上一轮四档资金袖套模拟执行回顾',capital:'起始资金',invested:'目标投入',participation:'最大目标仓位/ADV',days:'最少成交天数',cost:'模型往返成本代理',pnl:'模型波段净损益代理',netReturn:'模型波段净收益率代理',fill:'模拟成交比例',equity:'模拟当前净值',realizedPnl:'模拟净损益',realizedReturn:'模拟净收益率',executionCost:'累计模型执行成本',remaining:'未成交目标',
  strategies:'当前策略群与 TRIAID 调整',candidatePool:'查看未入选候选策略池',strategy:'策略',state:'状态',exp:'多周期年化收益估计',risk:'风险',
  before:'基线权重',after:'TRIAID 权重',delta:'权重变化',why:'策略说明与选择原因',
  evolution:'Core 进化状态',observed:'已后验评价运行',negative:'负相对收益差比例',next:'下一步',
  noEval:'等待下一交易日后验',noResult:'尚无可评价结果',evoNote:'Core 会根据持续后验评价形成候选改进',
  noCandidate:'尚无 Candidate',propose:'生成 Candidate Core',run:'立即运行（预览）',runAll:'预览三个市场',
  running:'已创建即时预览，后台正在读取真实市场数据；该运行不进入正式证据链。',pending:'当前决策已生成，等待下一交易日结果。',
  preview:'即时预览，仅展示当前策略状态和TRIAID权重，不写入正式证据、后验、进化或前瞻实验。',
  positive:'TRIAID 本期后验收益高于基线',negativeResult:'TRIAID 本期后验收益低于基线，需要回看权重调整归因',flat:'TRIAID 本期后验收益与基线基本一致'
 },
 en:{
  title:'TRIAID FIN Evolution Lab V2',subtitle:'Real market → Dynamic strategy population → TRIAID Core → Outcome validation → Continuous evolution',
  result:'TRIAID Result Comparison',baseReturn:'Baseline portfolio posterior return',triaidReturn:'TRIAID allocation posterior return',gain:'TRIAID relative return gap',
  live:'Market Data and Backend Runtime',indexWindow:'Latest Market Instrument Window',activityWindow:'Backend Runtime Events',
  baseSub:'Frozen baseline',triaidSub:'Frozen TRIAID allocation',gainSub:'TRIAID allocation posterior return − baseline posterior return',
  overview:'Current State',date:'Latest market date',core:'Generic Core',selected:'Selected strategy count',cum:'Sum of period excess returns',
  curve:'Continuous Review',legendBase:'Strategy-group baseline',legendTriaid:'TRIAID',
  daily:'Latest Data Summary',regime:'Market regime',runState:'Run status',selectedNames:'Selected now',analysis:'Latest posterior conclusion',
  usReturnMax:'US Return-Max Route',usrmExpected:'Return-Max multi-window annualized state estimate',usrmGeneric:'Generic Core multi-window annualized state estimate',usrmSpy:'SPY multi-window annualized state estimate',usrmRisk:'Target risk exposure',usrmStrategy:'Current Frozen Strategy Weights',usrmAsset:'Underlying ETF Target Exposure',usrmCapital:'Four-Tier USD Capital Capacity Experiment',usrmRealized:'Prior Real-Market Outcome and Simulated Execution Review',usrmControl:'Prior Frozen-Allocation Theoretical-Holdings Posterior Path',
  prospective:'CN Prospective Control Experiment',prospectiveDays:'Observed trading days',prospectiveHold:'Worst-pool cumulative return',prospectiveTriaid:'Frozen TRIAID cumulative return',prospectiveGap:'TRIAID vs worst pool',
  prospectiveStrategy:'Strategy Determination and Frozen Ranking',prospectiveDaily:'Daily Fluctuation Path',predRank:'TRIAID frozen composite rank',dailyReturn:'Latest day',cumReturn:'Cumulative return',realRank:'Current realized rank',detReason:'Determination basis',
  recoveryWave:'TRIAID Second-Order Recovery Wave Research',recoveryCash:'Target cash weight',recoveryPrevDays:'Prior posterior trading days',recoveryPrevReturn:'Prior frozen-portfolio posterior return',recoveryPrevGap:'Prior gap vs equal-weight control',
  recoveryOpinion:'Current Frozen Research Allocation Opinion',recoveryReview:'Prior Frozen-Allocation Real-Market Posterior',product:'Product',action:'Research opinion',target:'Target weight',change:'This decision change',drawdown:'252-day drawdown',direction:'State direction',horizon:'First positive historical window',speed:'Historical return-edge/day',hitEdge:'Recovery-rate edge × support',expected:'Historical-analogue mean forward return',samples:'Samples',resultDate:'Outcome date',portfolioDay:'Frozen portfolio day',equalDay:'Equal-weight day',portfolioCum:'Frozen portfolio cumulative',gapCum:'Cumulative gap',
  capitalSleeve:'Four-Tier CNY Capital Capacity Experiment',capitalRealized:'Prior Four-Sleeve Simulated Execution Review',capital:'Starting capital',invested:'Target invested',participation:'Max target notional / ADV',days:'Minimum execution days',cost:'Modeled round-trip cost proxy',pnl:'Modeled wave net P&L proxy',netReturn:'Modeled wave net-return proxy',fill:'Simulated fill ratio',equity:'Simulated current equity',realizedPnl:'Simulated net P&L',realizedReturn:'Simulated net return',executionCost:'Cumulative modeled execution cost',remaining:'Unfilled target',
  strategies:'Current Strategy Group and TRIAID Adjustments',candidatePool:'View unselected candidate pool',strategy:'Strategy',state:'State',exp:'Multi-window annualized state estimate',risk:'Risk',
  before:'Baseline weight',after:'TRIAID weight',delta:'Weight change',why:'Strategy explanation and selection reason',
  evolution:'Core Evolution State',observed:'Posterior-evaluated runs',negative:'Negative relative-return-gap rate',next:'Next step',
  noEval:'Awaiting next-period outcome',noResult:'No evaluated outcome yet',evoNote:'Core forms candidate improvements from continuous posterior evaluations',
  noCandidate:'No Candidate yet',propose:'Generate Candidate Core',run:'Run Preview',runAll:'Preview Three Markets',
  running:'Manual preview created. Real market data is being processed; this run does not enter the official evidence chain.',pending:'Current decision is ready and awaiting the next market outcome.',
  preview:'Manual preview only. It displays current strategy state and TRIAID weights without entering official evidence, posterior, evolution or prospective experiments.',
  positive:'TRIAID posterior return was above baseline in the latest evaluated run',negativeResult:'TRIAID posterior return was below baseline; weight-adjustment attribution should be reviewed',flat:'TRIAID posterior return was approximately in line with baseline'
 }
};
const TIP={
 zh:{
  strategy:'策略名称和策略编号。当前策略群主表只显示真正获得配置权重的策略。',
  state:'策略生命周期：ACTIVE=正式参与配置；SHADOW=只做真实前瞻验证、不获得生产权重；REDUCED=降级观察；FROZEN=暂停；CANDIDATE=候选阶段。',
  exp:'基于当前时点可见的 21/63/126/252 日策略已实现净收益，分别按日均收益×252年化后加权得到的状态收益估计。它不是标的未来涨跌幅，也不是经过前瞻校准的未来收益预测。',
  risk:'策略最近63个可用日收益的年化波动率，计算为总体标准差×√252；若不足63日则使用现有可用历史。数值越大表示收益路径越不稳定，不代表亏损概率。',
  before:'Strategy Population 完成选群后冻结的基线目标权重，用作TRIAID权重调整的同一时点对照。',
  after:'TRIAID Core 根据当前市场状态、策略风险与不确定性形成的冻结目标权重，不代表券商已成交持仓。',
  delta:'TRIAID冻结目标权重 − 基线目标权重。正数表示相对基线增配，负数表示减配。',
  why:'说明这个策略做什么、为什么在当前运行进入策略群，以及TRIAID为什么相对基线增配或减配。',
  candidateWhy:'说明候选策略的核心逻辑和适用市场。未入选策略不会获得当前正式配置权重。',
  active:'ACTIVE：已通过当前准入条件，可以正式参与策略群并获得生产权重。',
  shadow:'SHADOW：只记录真实未来表现进行前瞻验证，暂时不获得任何正式配置权重。',
  reduced:'REDUCED：策略被降级观察，仍可能保留少量权重，但正在接受进一步验证。',
  frozen:'FROZEN：策略已冻结，暂停进入正式策略群。',
  candidate:'CANDIDATE：候选阶段，尚未满足进入正式策略群的证据要求。',
  research:'RESEARCH：研究阶段，只用于开发和验证。',
  retired:'RETIRED：已退出当前策略体系，除非出现新的证据，否则不再参与选群。',
  preview_ready:'PREVIEW_READY：人工即时预览已经完成。结果只用于当前页面查看，不进入正式证据链、后验评价、生命周期累计或Core进化。'
 },
 en:{
  strategy:'Strategy name and ID. The main group table shows only strategies that actually receive allocation weight.',
  state:'Strategy lifecycle: ACTIVE=eligible for live allocation; SHADOW=prospective observation only with no live weight; REDUCED=degraded monitoring; FROZEN=paused; CANDIDATE=pre-admission.',
  exp:'A multi-window annualized state-return estimate built from realized 21/63/126/252-day strategy net returns, with each window annualized as mean daily return × 252 and then weighted. It is not a forecast of the underlying asset price move or a calibrated future-return prediction.',
  risk:'Annualized volatility of the latest 63 available strategy-return observations, computed as population standard deviation × √252; shorter available history is used when fewer than 63 observations exist. It is not loss probability.',
  before:'Frozen baseline target weight assigned by Strategy Population at the same decision time, used as the control for TRIAID reweighting.',
  after:'Frozen target weight after TRIAID Core maximizes the current realizable net-return proxy within market-state, liquidity, capacity and concentration constraints. It is not an executed broker position.',
  delta:'Frozen TRIAID target weight minus baseline target weight. Positive means more allocation than baseline; negative means less.',
  why:'Explains what the strategy does, why it entered the current run’s group, and why TRIAID increased or reduced it relative to baseline.',
  candidateWhy:'Explains the candidate strategy logic and suitable conditions. Unselected strategies receive no current live allocation.',
  active:'ACTIVE: eligible for the live strategy group and production allocation.',
  shadow:'SHADOW: prospectively tracked on real future data but receives no production allocation.',
  reduced:'REDUCED: downgraded for further observation and may retain only limited allocation.',
  frozen:'FROZEN: paused and excluded from the live strategy group.',
  candidate:'CANDIDATE: not yet supported by enough evidence for live admission.',
  research:'RESEARCH: development and validation only.',
  retired:'RETIRED: removed from the current strategy system unless new evidence justifies reconsideration.',
  preview_ready:'PREVIEW_READY: the manual preview is complete. It is display-only and does not enter official evidence, posterior evaluation, lifecycle accumulation or Core evolution.'
 }
};

const TABLE_HEADER_TIPS={
 zh:{
  '策略':'当前策略名称或策略编号。鼠标移到具体策略状态上还可查看生命周期说明。',
  '状态':'当前策略或任务的状态。ACTIVE、SHADOW、FROZEN 等状态代表不同的准入和运行阶段。',
  '多周期年化收益估计':'基于当前时点可见的21/63/126/252日策略已实现净收益，将各窗口日均收益×252年化后加权得到。负值表示历史状态收益估计为负，不表示标的还会下跌相同比例。',
  '风险':'策略最近63个可用日收益的年化波动率，计算为总体标准差×√252；若不足63日则使用现有可用历史。它不是亏损概率。',
  '介入前':'TRIAID Core 介入前，由基础策略群或对照方案给出的配置权重。',
  'TRIAID后':'TRIAID 根据当前状态、风险和不确定性重新评估后的配置权重。',
  '权重变化':'TRIAID冻结目标权重减去基线目标权重。正数表示相对基线增配，负数表示减配。',
  '策略说明与选择原因':'策略的核心逻辑、适用状态、进入当前策略群的原因以及 TRIAID 调整原因。',
  '说明':'当前行策略或结果的补充解释，包括适用条件、状态和选择依据。',
  'Return-Max权重':'美股 Return-Max 路线当前冻结的策略权重。当前排序信号是多周期历史净收益的年化状态估计，不应解释为已校准未来收益预测。',
  '通用Core权重':'通用 TRIAID Core 在同一时点给出的对照策略权重，用于与 Return-Max 路线比较。',
  'ETF':'策略层目标权重展开后的底层ETF标的。表中目标敞口不代表已经成交或当前券商持仓。',
  '目标权重':'当前冻结决策建议配置到该产品或资产的目标比例，不代表已经自动成交。',
  '起始资金':'本资金袖套用于容量和成本评估的初始账户规模。',
  '目标投入':'按当前目标权重计划投入风险资产的名义资金规模。',
  '最大目标仓位/ADV':'对资金袖套逐产品计算完整目标名义仓位÷近20日价格×成交量得到的近似ADV，再取最大值。它是容量压力比，不等于实际单日成交比例。',
  '最少成交天数':'逐产品按目标名义仓位÷(近20日近似ADV×参与率上限)向上取整，再取最大值。它是假定当前流动性水平延续时的容量估计。',
  '模型往返成本代理':'按完整目标仓位、基础成本与sqrt(参与率)冲击模型计算单边进入成本后乘2得到。未包含券商个性化费率，也未建模未来退出时流动性变化。',
  '成交比例':'当前资金袖套已完成的目标成交比例。',
  '模拟成交比例':'基于后续真实成交量和冻结参与率规则模拟得到的成交比例，不代表券商真实成交。',
  '模拟当前净值':'基于真实后续价格路径与模型化成交/成本计算的模拟账户价值，不是券商账户净值。',
  '模拟净损益':'模拟当前净值减去起始资金后的净损益。',
  '模拟净收益率':'基于真实后续价格与模型化成交/成本计算的模拟净收益率。',
  '模型执行成本':'本轮由基础成本和流动性冲击代理估算的执行成本，不是券商实际收费。',
  '累计模型执行成本':'截至当前由成交容量与冲击模型累计估算的执行成本，不是券商实际收费。',
  '未成交目标':'受容量或参与率约束尚未完成的目标名义仓位。',
  '结果日':'该行真实后验结果对应的完整交易日。',
  'Return-Max累计':'从冻结决策开始，Return-Max 组合截至该日的累计净收益。',
  '通用Core累计':'从同一起点开始，通用 Core 对照组合截至该日的累计净收益。',
  'SPY累计':'同期 SPY 买入持有对照的累计收益。',
  'TRIAID冻结综合排序':'登记时冻结的无拟合系数等权Borda排序，综合多周期年化状态收益估计、20日动量、风险、不确定性，以及可用时的状态方向。后续结果不能倒推修改。',
  '基线权重':'当前运行由Strategy Population在TRIAID重加权前冻结的基线目标权重。它是研究配置，不代表券商真实持仓。',
  'TRIAID权重':'当前运行或前瞻实验冻结的TRIAID目标权重。后续结果不能倒推修改，也不代表券商真实持仓。',
  '最近一日':'最新一个完整交易日该策略的真实单日收益。',
  '累计收益':'从实验登记或冻结起点到当前的累计真实收益。',
  '当前实际名次':'根据已经发生的真实累计收益计算出的当前实际排序。',
  '确定依据':'冻结时选择、排序或配置该策略所使用的证据和规则。',
  '产品':'当前恢复波段决策对应的 ETF 或可交易产品。',
  '研究意见':'本轮冻结的研究配置意见，例如增配、减配、持有或观察，不会自动发送券商订单。',
  '本轮调整':'目标权重相对上一轮目标权重的变化幅度。',
  '252日回撤':'当前价格相对近252个交易日最高价的回落幅度。',
  '状态方向':'由5/20/63日动量的短中期加速度符号组合得到，映射为IMPROVING_FAST、IMPROVING、DETERIORATING或MIXED。',
  '首个正向历史窗口':'在3/5/10/20日候选窗口中，历史相似状态首次同时满足正恢复率优势、正平均收益优势和正中位前向收益的最早窗口。它不是对未来反转日期的确定预测。',
  '历史收益优势速度/日':'所选历史窗口的平均收益优势除以窗口交易日数，用于排序，不等同于未来每日价格上涨速度。',
  '恢复率优势×样本支持':'历史相似状态正收益率相对无条件正收益率的优势，再乘以样本支持因子min(1,相似样本数/20)。',
  '历史相似状态平均前向收益':'所选历史窗口内相似状态样本的平均前向收益。它是历史条件统计，不是保证收益，实际结果以后续完整交易日后验为准。',
  '样本':'形成当前历史相似状态统计所使用的有效样本数量。',
  '模型波段净损益代理':'目标名义仓位×历史相似状态平均前向收益，再减去模型往返成本代理。未包含券商真实成交，且后端口径为before_timing_delay，未扣除多日建仓造成的时点偏移。',
  '模型波段净收益率代理':'模型波段净损益代理÷起始资金。它未包含券商真实成交，也未扣除多日建仓造成的时点偏移。',
  '冻结组合当日':'冻结的 TRIAID 组合在该完整交易日的真实收益。',
  '等权对照当日':'同一组产品等权对照在该完整交易日的真实收益。',
  '冻结组合累计':'从冻结决策起点到该日的 TRIAID 组合累计收益。',
  '累计差值':'冻结组合累计收益减去等权对照累计收益。',
  '交易日':'该行后验路径对应的完整交易日。',
  '最差池':'登记时冻结的最差策略池对照，不允许根据后续结果换成员。',
  'TRIAID':'登记时冻结的 TRIAID 配置或其对应的真实后验表现。',
  '差值':'TRIAID 与当前表格对照方案之间的收益或指标差异。',
  '目标风险仓位':'当前路线允许配置到风险资产的目标比例，其余部分保留为现金或防守资产。'
 },
 en:{
  'Strategy':'Current strategy name or ID. Hovering a lifecycle badge provides additional state detail.',
  'State':'Current strategy or task state. ACTIVE, SHADOW, FROZEN and related states represent different admission and operating stages.',
  'Multi-windowannualizedstateestimate':'Multi-window annualized state-return estimate from realized 21/63/126/252-day strategy net returns. Each window uses mean daily return × 252 before weighting. It is not an underlying-price forecast or a calibrated future-return prediction.',
  'Risk':'Annualized volatility of the latest 63 available strategy-return observations, computed as population standard deviation × √252; shorter available history is used when fewer than 63 observations exist. It is not loss probability.',
  'BeforeTRIAID':'Baseline allocation before TRIAID Core intervention.',
  'AfterTRIAID':'Allocation after TRIAID maximizes realizable net return within current market and execution constraints.',
  'Weightchange':'Frozen TRIAID target weight minus baseline target weight. Positive means more allocation than baseline; negative means less.',
  'Explanation':'Supporting explanation for the row, including logic, conditions and selection rationale.',
  'Return-Maxweight':'Frozen strategy weight from the US Return-Max route. Its ranking signal is the multi-window annualized state-return estimate, not a calibrated future-return forecast.',
  'GenericCoreweight':'Contemporaneous generic TRIAID Core allocation used as the control for the Return-Max route.',
  'ETF':'Underlying ETF produced by expanding strategy-level target weights. Displayed target exposure does not imply an executed or current broker position.',
  'Targetweight':'Frozen target allocation for this product or asset; it does not imply that a broker order has been sent.',
  'Startingcapital':'Initial account size used for capacity and transaction-cost evaluation.',
  'Targetinvested':'Target notional amount allocated to risky assets under the frozen decision.',
  'Maxtargetnotional/ADV':'Maximum across products of full target notional divided by approximate 20-day ADV notional (close × reported volume). It is a capacity-pressure ratio, not an actual one-day fill rate.',
  'Minimumexecutiondays':'Maximum across products of ceil(target notional ÷ (approximate 20-day ADV notional × participation cap)), assuming current liquidity persists.',
  'Modeledround-tripcostproxy':'Two times the modeled entry cost for the full target notional using base cost plus sqrt(participation) impact. Broker-specific fees and future exit-liquidity changes are excluded.',
  'Simulatedfillratio':'Share of the target position completed under the frozen simulated-fill rules using subsequently observed real volume; not a broker fill ratio.',
  'Simulatedcurrentequity':'Simulated account value based on subsequent real market moves plus modeled fills and execution costs; not broker-account equity.',
  'SimulatednetP&L':'Simulated current equity minus starting capital.',
  'Simulatednetreturn':'Simulated net return relative to starting capital after modeled fills and execution costs.',
  'Modeledexecutioncost':'Execution cost estimated from the base-cost and liquidity-impact model; not an actual broker fee.',
  'Cumulativemodeledexecutioncost':'Cumulative execution cost estimated by the capacity and market-impact model; not actual broker charges.',
  'Remainingtarget':'Target notional position not yet completed because of capacity or participation constraints.',
  'Resultdate':'Complete trading day represented by this realized outcome row.',
  'Return-Maxcumulative':'Cumulative net return of the frozen Return-Max portfolio since the decision date.',
  'GenericCorecumulative':'Cumulative net return of the generic Core control from the same starting point.',
  'SPYcumulative':'Cumulative SPY buy-and-hold return over the same period.',
  'TRIAIDfrozencompositerank':'Coefficient-free equal-weight Borda rank frozen at registration across the multi-window annualized state-return estimate, 20-day momentum, risk, uncertainty, and state direction when available. Future outcomes cannot retune it.',
  'Baselineweight':'Baseline target weight frozen by Strategy Population before TRIAID reweighting for the current run. It is a research allocation, not a broker position.',
  'TRIAIDweight':'TRIAID target weight frozen for the current run or prospective registration. Future outcomes cannot retune it, and it is not a broker position.',
  'Latestday':'Realized return for the latest complete trading day.',
  'Cumulativereturn':'Realized cumulative return since experiment registration or decision freeze.',
  'Currentrealizedrank':'Current ranking computed from realized cumulative returns observed so far.',
  'Rationale':'Evidence and rule used to select, rank or allocate the strategy at freeze time.',
  'Product':'ETF or executable product covered by the current recovery-wave decision.',
  'Researchopinion':'Frozen research allocation opinion such as add, reduce, hold or observe; no broker order is generated.',
  '252-daydrawdown':'Current decline from the highest price over the latest 252 trading days.',
  'Statedirection':'A label derived from the sign pattern of short-vs-medium and medium-vs-long momentum acceleration using 5/20/63-day momentum.',
  'Firstpositivehistoricalwindow':'Earliest among the 3/5/10/20-day windows where historical analogues simultaneously have positive hit-rate edge, positive mean-return edge and positive median forward return. It is not a certain future reversal date.',
  'Historicalreturn-edge/day':'Mean-return edge in the selected historical-analogue window divided by its trading-day horizon. It is a ranking statistic, not a forecast of daily price appreciation.',
  'Recovery-rateedge×support':'Historical positive-rate edge versus the unconditional rate, multiplied by support min(1, analogue sample count / 20).',
  'Historical-analoguemeanforwardreturn':'Mean forward return across the selected historical-analogue samples for that horizon. It is a historical conditional statistic, not a guaranteed future return.',
  'Samples':'Number of valid historical analogue samples supporting the current statistic.',
  'ModeledwavenetP&Lproxy':'Target notional × historical-analogue mean forward return minus the modeled round-trip cost proxy. The backend field is before_timing_delay, so multi-day entry timing is not deducted.',
  'Modeledwavenet-returnproxy':'Modeled wave net P&L proxy divided by starting capital. It excludes broker execution and does not deduct multi-day entry timing delay.',
  'Frozenportfoliodaily':'Realized daily return of the frozen TRIAID portfolio for this complete trading day.',
  'Equal-weightcontroldaily':'Realized daily return of the equal-weight control over the same products.',
  'Frozenportfoliocumulative':'Cumulative return of the frozen TRIAID portfolio since the decision date.',
  'Cumulativegap':'Frozen portfolio cumulative return minus equal-weight control cumulative return.',
  'Tradingday':'Complete trading day represented by this row.',
  'Worstpool':'Worst-strategy control pool frozen at registration; membership cannot be changed after outcomes are observed.',
  'TRIAID':'Frozen TRIAID allocation or its realized prospective performance.',
  'Gap':'Difference between TRIAID and the control shown in this table.'
 }
};

const UI_TIPS={
 zh:{
  resultTitle:'只展示已经完成后验评价的同周期对照结果。基线与TRIAID必须来自同一冻结决策与同一结果期。',
  liveTitle:'展示最新可用市场数据与后台调度活动。数据可能来自实时源或最近完整日线，是否实时以数据源、会话阶段和延迟字段为准。',
  strategyTitle:'主表展示当前获得配置权重的策略。策略名称旁的信息标识可查看底层标的最新可用价和最近完整交易日涨跌幅。',
  dailyTitle:'展示页面所选市场最新数据日对应的状态，以及最近一轮已有后验的比较结果。它不等同于自然日今天，也不把尚未发生的结果当成已实现收益。',
  overviewTitle:'当前运行状态摘要。所有日期、Core和策略数量均来自当前持久化运行记录。',
  curveTitle:'只使用已完成EVALUATED后验的运行。两条权益曲线逐期复利；累计单期超额和是各期excess_return的算术和，不等于两条权益曲线的最终差值。',
  evolutionTitle:'统计已完成后验的TRIAID干预结果，并据此形成候选Core。生成Candidate不会自动晋升为生产Core。',
  baseReturnLabel:'当前结果卡的基线组合真实后验收益。A股压力实验使用登记时冻结的最差策略池；其他运行使用对应冻结基线。无后验时不显示收益。',
  triaidReturnLabel:'与基线完全同一结果期内，冻结TRIAID配置按已发生策略收益计算的后验收益。它不是当前时点预测值，也不代表券商账户实际成交收益。',
  gainLabel:'同一后验结果期内，TRIAID配置后验收益减去对应基线后验收益。它是同周期相对收益差，不自动等同于因果增益。',
  regimeLabel:'由最新完整市场数据计算的离散市场状态标签，例如risk_on_trend、risk_off、stress_high_vol或mixed。',
  runStateLabel:'当前最新运行记录的执行状态，不代表投资结果好坏。',
  selectedNamesLabel:'当前策略群中实际获得配置权重的策略。未入选候选策略不计入。',
  dailyAnalysisLabel:'基于最近一轮已完成后验的TRIAID相对基线结果。若尚无后验，则显示等待结果。',
  usrmExpectedLabel:'将当前冻结Return-Max策略权重乘以各策略多周期年化状态收益估计后得到。底层信号来自21/63/126/252日已实现策略净收益，不是经校准的未来收益预测。',
  usrmGenericLabel:'用同一时点通用Core权重对相同多周期年化状态收益估计加权得到，用作同时间截面对照。',
  usrmSpyLabel:'SPY买入持有策略在同一多周期历史窗口口径下的年化状态收益估计，不表示SPY未来必然按该比例涨跌。',
  usrmRiskLabel:'1减去冻结决策的现金剩余权重，表示目标风险资产暴露比例，不代表杠杆倍数。',
  prospectiveDaysLabel:'冻结A股前瞻实验后已经发生并被纳入评价的完整交易日数量，以及预设观察窗口。',
  prospectiveHoldLabel:'登记时冻结的最差策略池等权对照，从冻结起点到当前的真实累计后验收益。',
  prospectiveTriaidLabel:'登记时冻结的TRIAID静态配置，从冻结起点到当前的真实累计后验收益。',
  prospectiveGapLabel:'TRIAID冻结配置累计收益减去最差池累计收益。',
  recoveryCashLabel:'当前冻结研究配置中未分配到风险产品的目标现金权重。它是目标配置，不代表真实账户现金余额。',
  recoveryPrevDaysLabel:'上一轮冻结研究配置之后，已经纳入真实市场后验的完整交易日数量。',
  recoveryPrevReturnLabel:'上一轮冻结目标权重直接作用于后续真实产品收益得到的理论持仓累计后验收益。它不含容量分批成交，容量执行结果在下方模拟执行表单独报告。',
  recoveryPrevGapLabel:'上一轮冻结目标权重理论持仓累计后验收益减去同一组产品等权对照累计收益。',
  dateLabel:'当前页面所选市场最新运行记录对应的市场数据日期。',
  coreLabel:'页面展示的通用TRIAID Core版本。美股Return-Max主路线另有独立路线逻辑，通用Core在该路线中同时作为对照。',
  selectedLabel:'当前策略群中selected=true的策略数量，不等于整个候选策略注册表数量。',
  cumLabel:'所有已完成后验运行的单期excess_return算术求和。它不是复利收益，也不是两条权益曲线最终值之差。',
  evoObservedLabel:'当前持久化记录中evaluation.status=EVALUATED的运行数量。这里的后验评价只表示结果已经发生并被计算，不表示外部验证通过。',
  evoNegLabel:'已后验评价运行中excess_return小于0的比例，即TRIAID配置后验收益低于对应基线的运行占比。',
  evoCandidateLabel:'Candidate Core由已评价结果触发生成，仍需replay、holdout、shadow和audit检查全部通过后才可晋升。',
  usReturnMaxTitle:'美股研究主路线。当前选择规则严格按多周期年化状态收益估计排序，并做容量与执行成本模拟；不自动发送券商订单。',
  prospectiveTitle:'A股冻结前瞻对照。成员、排序和权重在登记时冻结，后续真实结果不能用于倒改。',
  recoveryWaveTitle:'A股恢复波段Shadow研究路线。研究配置和参数冻结，只用后续完整交易日做后验评价，不生成券商订单。',
  indexWindowTitle:'显示当前数据源能提供的最新市场标的价格。盘外或实时源不可用时可能退回最近完整日线，因此这里使用“最新”而不是保证“实时”，并应同时查看数据源和延迟。',
  activityWindowTitle:'后台市场数据刷新、调度和运行事件的记录。这里是系统事件流水，不是券商交易指令。',
  usrmStrategyTitle:'冻结时的策略层权重。Return-Max与通用Core来自同一数据截面，便于对照。',
  usrmAssetTitle:'策略权重展开到SPY、QQQ、IWM、TLT、GLD等底层ETF后的冻结目标敞口。它不代表已成交或当前券商持仓。',
  usrmCapitalTitle:'用四档美元资金规模在相同冻结决策下评估ADV容量、最少成交天数和模型化冲击成本。',
  usrmRealizedTitle:'使用后续真实市场价格和成交量进行后验，再按冻结规则模拟成交与成本。这里不是券商真实账户成交记录。',
  usrmControlTitle:'同一冻结起点下，以冻结策略权重直接作用于后续真实策略收益得到的理论持仓累计后验路径。它不包含容量分批成交，与模拟执行结果分开报告。',
  prospectiveStrategyTitle:'冻结时的策略成员、无拟合系数等权Borda综合排序和权重，以及随后已经发生的真实累计收益排名。',
  prospectiveDailyTitle:'冻结实验后每个完整交易日的真实策略收益及组合累计路径。',
  recoveryOpinionTitle:'当前Shadow Core的冻结研究配置，不生成真实券商订单。',
  capitalSleeveTitle:'用四档人民币资金规模对同一冻结研究配置进行容量和模型化执行成本评估。',
  capitalRealizedTitle:'基于后续真实价格与成交量、冻结参与率上限和冲击模型得到的模拟成交结果，不是券商成交回单。',
  recoveryReviewTitle:'上一轮冻结目标权重直接作用于后续真实产品收益得到的理论持仓后验路径，并与同产品等权对照比较。它不是券商真实成交记录。'
 },
 en:{
  resultTitle:'Shows only completed same-period posterior evaluations. Baseline and TRIAID results must come from the same frozen decision and outcome period.',
  liveTitle:'Shows the latest available market data and backend scheduling activity. Data may be realtime or the latest complete daily bar; realtime status depends on provider, session phase and displayed data age.',
  strategyTitle:'The main table shows strategies with current allocation weight. Hover the info mark beside a strategy name for latest available underlying prices and last complete trading-day changes.',
  dailyTitle:'Shows state for the selected market’s latest data date and the latest evaluable posterior comparison. It is not necessarily natural-calendar today, and unobserved outcomes are not presented as realized returns.',
  overviewTitle:'Current runtime summary from persisted run records.',
  curveTitle:'Uses only EVALUATED runs. Equity curves compound period returns; the sum of period excess returns is an arithmetic sum and is not the ending gap between the two equity curves.',
  evolutionTitle:'Summarizes completed posterior intervention results and can form Candidate Cores. Creating a Candidate does not promote it automatically.',
  baseReturnLabel:'Realized posterior return of the baseline portfolio for the displayed completed evaluation. The CN stress route uses the worst pool frozen at registration; other runs use their corresponding frozen baseline.',
  triaidReturnLabel:'Posterior return of the frozen TRIAID allocation computed from strategy returns over exactly the same outcome period as the baseline. It is not broker-account realized P&L.',
  gainLabel:'TRIAID allocation posterior return minus baseline posterior return for the same evaluated period. It is a same-period relative return gap, not automatically a causal uplift estimate.',
  regimeLabel:'Discrete market-state label computed from the latest complete market data.',
  runStateLabel:'Execution state of the latest run record, not an investment-performance rating.',
  selectedNamesLabel:'Strategies currently receiving allocation weight in the strategy group.',
  dailyAnalysisLabel:'Conclusion from the latest completed posterior comparison of TRIAID versus baseline.',
  usrmExpectedLabel:'Weighted value of the strategy-level multi-window annualized state-return estimates under the frozen Return-Max weights. Inputs are realized 21/63/126/252-day strategy net returns, not a calibrated future-return forecast.',
  usrmGenericLabel:'The same state-return estimates weighted by the contemporaneous Generic Core allocation for comparison.',
  usrmSpyLabel:'SPY buy-and-hold state-return estimate under the same multi-window historical convention. It is not a forecast that SPY will move by that percentage.',
  usrmRiskLabel:'One minus frozen cash residual weight, i.e. target risky-asset exposure rather than leverage.',
  prospectiveDaysLabel:'Number of complete post-freeze trading days already included in the CN prospective evaluation, alongside its preset horizons.',
  prospectiveHoldLabel:'Realized cumulative posterior return of the worst-pool equal-weight control frozen at registration.',
  prospectiveTriaidLabel:'Realized cumulative posterior return of the static TRIAID allocation frozen at registration.',
  prospectiveGapLabel:'Frozen TRIAID cumulative return minus worst-pool cumulative return.',
  recoveryCashLabel:'Target cash weight left unallocated to risky products in the current frozen research allocation. It is not a broker-account cash balance.',
  recoveryPrevDaysLabel:'Number of complete post-freeze trading days already included in the real-market posterior for the prior research allocation.',
  recoveryPrevReturnLabel:'Cumulative theoretical-holdings posterior return obtained by applying the prior frozen target weights directly to subsequent real product returns. Capacity fills are evaluated separately in the simulated-execution table.',
  recoveryPrevGapLabel:'Prior frozen-target theoretical-holdings cumulative posterior return minus the equal-weight control over the same products.',
  dateLabel:'Market-data date attached to the latest run for the selected market.',
  coreLabel:'Displayed Generic TRIAID Core version. The US Return-Max route has separate logic and also uses this Generic Core as a control.',
  selectedLabel:'Count of strategies with selected=true in the current strategy group, not the size of the full registered candidate universe.',
  cumLabel:'Arithmetic sum of period excess_return across completed evaluations. It is not compounded performance and not the ending equity-curve difference.',
  evoObservedLabel:'Number of persisted runs with evaluation.status=EVALUATED. Posterior-evaluated means the outcome has occurred and was computed; it does not mean external validation passed.',
  evoNegLabel:'Share of posterior-evaluated runs with excess_return below zero, meaning the TRIAID allocation posterior return was below its corresponding baseline.',
  evoCandidateLabel:'A Candidate Core still requires replay, holdout, shadow and audit checks before promotion.',
  usReturnMaxTitle:'US research primary route. Selection ranks the multi-window annualized state-return estimate and then models capacity and execution cost. No broker orders are sent.',
  prospectiveTitle:'Frozen CN prospective control. Members, ranks and weights are frozen at registration and cannot be rewritten from later outcomes.',
  recoveryWaveTitle:'CN recovery-wave Shadow research route. Research allocations and parameters are frozen and evaluated only on subsequent complete trading days; no broker orders are sent.',
  indexWindowTitle:'Latest prices available from the current provider. Outside market hours or when realtime data are unavailable, the view may fall back to the latest complete daily bar, so this is labeled latest rather than guaranteed realtime.',
  activityWindowTitle:'Backend refresh, scheduling and runtime events. This is a system event stream, not broker trade instructions.',
  usrmStrategyTitle:'Frozen strategy-level weights for Return-Max and Generic Core from the same data snapshot.',
  usrmAssetTitle:'Frozen target ETF exposure after expanding strategy weights into SPY, QQQ, IWM, TLT and GLD. It is not an executed or current broker position.',
  usrmCapitalTitle:'Four USD capital tiers evaluating ADV capacity, minimum execution days and modeled impact cost under one frozen decision.',
  usrmRealizedTitle:'Posterior evaluation on subsequent real market prices and volumes with simulated fills and modeled costs. This is not a broker account execution record.',
  usrmControlTitle:'Theoretical-holdings cumulative posterior paths obtained by applying frozen strategy weights directly to subsequent real strategy returns. They exclude staged capacity fills and are reported separately from simulated execution.',
  prospectiveStrategyTitle:'Frozen members, coefficient-free equal-weight Borda composite rank and weights at registration, plus realized cumulative-return ranks after outcomes occur.',
  prospectiveDailyTitle:'Realized strategy returns and portfolio cumulative paths for each complete post-freeze trading day.',
  recoveryOpinionTitle:'Current frozen Shadow Core research allocation. No live broker order is generated.',
  capitalSleeveTitle:'Four CNY capital tiers evaluating capacity and modeled execution cost for the same frozen research allocation.',
  capitalRealizedTitle:'Simulated fills based on subsequent real prices and volumes, frozen participation caps and the impact model. Not broker fills.',
  recoveryReviewTitle:'Theoretical-holdings posterior path obtained by applying the prior frozen target weights directly to subsequent real product returns, compared with an equal-weight control. It is not a broker execution record.'
 }
};

function uiTipUseGuide(id){
 const zh=lang==='zh';
 const posterior=new Set(['resultTitle','baseReturnLabel','triaidReturnLabel','gainLabel','dailyAnalysisLabel','prospectiveHoldLabel','prospectiveTriaidLabel','prospectiveGapLabel','recoveryPrevReturnLabel','recoveryPrevGapLabel','curveTitle']);
 const runtime=new Set(['liveTitle','indexWindowTitle','activityWindowTitle','dateLabel','runStateLabel','overviewTitle']);
 const strategy=new Set(['strategyTitle','selectedNamesLabel','selectedLabel','usrmStrategyTitle','prospectiveStrategyTitle','recoveryOpinionTitle']);
 const capacity=new Set(['usrmCapitalTitle','usrmRealizedTitle','capitalSleeveTitle','capitalRealizedTitle','usrmRiskLabel','recoveryCashLabel']);
 const evolution=new Set(['evolutionTitle','evoObservedLabel','evoNegLabel','evoCandidateLabel']);
 const context=new Set(['regimeLabel','coreLabel','usrmExpectedLabel','usrmGenericLabel','usrmSpyLabel','prospectiveDaysLabel','recoveryPrevDaysLabel','cumLabel']);
 const route=new Set(['usReturnMaxTitle','prospectiveTitle','recoveryWaveTitle']);
 const exposure=new Set(['usrmAssetTitle']);
 const control=new Set(['usrmControlTitle']);
 const dailyPath=new Set(['dailyTitle','prospectiveDailyTitle','recoveryReviewTitle']);
 if(posterior.has(id))return zh
  ? '怎么用：先看同一冻结时点的基线与TRIAID，再看相对收益差和累计路径。若优势只来自单个结果期、随后迅速反转，不能算稳定增值；只有多个已完成后验方向一致，才值得进入规则复核。'
  : 'How to use: compare TRIAID with the control frozen at the same decision time, then inspect the return gap and cumulative path. If the edge comes from one outcome period and quickly reverses, it is not stable value-add; only repeated completed posteriors justify rule review.';
 if(runtime.has(id))return zh
  ? '怎么用：先核对市场数据日、数据源、交易阶段和延迟，再读策略结果。若时间戳停滞、当前交易阶段与数据更新不一致或provider异常，先按数据/运行故障处理，不把它解释成策略信号。'
  : 'How to use: verify market-data date, provider, session phase and delay before reading strategy results. If timestamps stall, updates disagree with the session phase or the provider is abnormal, treat it as a data/runtime fault rather than a strategy signal.';
 if(strategy.has(id))return zh
  ? '怎么用：核对谁被选中、基线权重→TRIAID权重改了多少、选择理由是什么。若加权没有冻结时理由，或后续已完成后验持续落后基线，就进入降权/退出规则复核。'
  : 'How to use: check who was selected, the baseline→TRIAID weight change, and the frozen-time rationale. If added weight lacks a frozen rationale or completed posteriors repeatedly lag the control, review downgrade/exit rules.';
 if(capacity.has(id))return zh
  ? '怎么用：看ADV占用、最少成交天数、模拟成本、fill ratio和剩余未成交目标。若成本吞掉预期优势、成交周期过长或长期无法完成目标仓位，这条路线在该资金规模下就不算可实现。'
  : 'How to use: inspect ADV usage, minimum execution days, modeled cost, fill ratio and remaining target notional. If costs consume the expected edge, execution takes too long, or the target cannot be filled, the route is not realizable at that capital size.';
 if(evolution.has(id))return zh
  ? '怎么用：看已完成后验数量、负相对收益比例和Candidate状态。Candidate只有在 replay、holdout、shadow、前瞻与audit全部通过后才可晋升；任一项未通过就保持原Core。'
  : 'How to use: inspect evaluated-run count, negative relative-return rate and Candidate status. A Candidate may promote only after replay, holdout, shadow, prospective and audit checks all pass; otherwise keep the current Core.';
 if(context.has(id))return zh
  ? '怎么用：先确认市场、数据日、Core版本和比较口径属于同一快照。不是同一市场/日期/冻结版本的数据不要直接横向比较；先统一口径再看收益或风险。'
  : 'How to use: first confirm market, data date, Core version and comparison convention belong to the same snapshot. Do not compare across different markets/dates/frozen versions until the convention is aligned.';
 if(route.has(id))return zh
  ? '怎么用：先确认当前市场采用哪条主实验路线、谁是冻结对照、后验从什么时候开始计。若页面所示路线与当前市场不匹配，或对照不是同一冻结时点，先视为配置/展示错误。'
  : 'How to use: confirm which primary experiment route the market uses, what the frozen control is, and when posterior evaluation starts. If the displayed route does not match the market or the control was not frozen at the same time, treat it as a configuration/display fault.';
 if(exposure.has(id))return zh
  ? '怎么用：看策略权重最终落到哪些ETF/资产，以及风险资产、债券、黄金和现金各占多少。若底层敞口加总与策略层目标风险敞口不一致，先检查策略→资产映射，不继续解读收益。'
  : 'How to use: see which ETFs/assets the strategy weights ultimately create and how risky assets, bonds, gold and cash are split. If underlying exposure does not reconcile with the strategy-level target, inspect the strategy→asset mapping before interpreting returns.';
 if(control.has(id))return zh
  ? '怎么用：确认“TRIAID赢了谁”。对照必须与TRIAID同一冻结时点、同一结果期、同一收益口径；只要对照中途改变，后验差值就失去可比性。'
  : 'How to use: verify exactly what TRIAID is being compared against. The control must share the same freeze time, outcome period and return convention; if the control changes midstream, the posterior gap is no longer comparable.';
 if(dailyPath.has(id))return zh
  ? '怎么用：沿交易日逐项看收益贡献，确认累计结果是不是被某一天或少数几天主导。若去掉极端单日后优势消失，先按脆弱结果处理，不把累计数字直接当成稳定能力。'
  : 'How to use: inspect day-by-day contribution and check whether the cumulative result is dominated by one or a few days. If the edge disappears without an extreme day, treat it as fragile rather than stable capability.';
 return zh
  ? '该项目尚未配置专用用途说明；这是UI文案缺陷，不应依赖这段提示做判断。'
  : 'This item lacks a dedicated usage explanation; treat that as a UI copy defect and do not rely on this tooltip for a decision.';
}
function applyUiTooltips(){
 Object.entries(UI_TIPS[lang]||{}).forEach(([id,tip])=>{
  const node=el(id); if(!node)return;
  const full=tip+'\\n'+uiTipUseGuide(id);
  node.classList.add('has-tip','tip-mark');
  node.dataset.tip=full;
  node.setAttribute('aria-label',(node.textContent||'').trim()+' — '+full);
 });
}

function normalizeHeaderLabel(text){
 return String(text||'').replace(/\\s+/g,'').replace(/[：:]/g,'').trim();
}
const TABLE_HEADER_TIPS_EXTRA={
 zh:{
  '市场':'该行对应的市场。US=美股，CN=A股，HK=港股。',
  '252日回撤压力':'当前基准相对近252个交易日高点的回撤压力幅度。数值越大表示距离阶段高点越远，不是未来跌幅预测。',
  '63日负向动量特征':'定义为负的63日历史收益率，也就是 -r63。正值表示近63日累计收益为负，负值表示近63日累计收益为正。它是历史状态特征，不是未来63日收益预测。',
  '动量异常分位':'当前63日负向动量在历史状态中的分位位置。越接近100%表示当前负向动量在历史样本中越异常。',
  '63日波动':'近63个可用交易日的年化波动状态，用于衡量价格路径不稳定程度，不等于亏损概率。',
  '风控实验阶段':'三市场风控Shadow实验当前阶段。仅研究与约束候选，不自动修改生产权重。',
  '环节':'跨市场动力链中的一个状态节点，例如结构脆弱、政策重定价、债券波动或市场传导。',
  '强度':'该动力链节点对应的状态分数或分位证据，口径随节点而定。',
  '主要证据':'触发或支持当前动力链状态的核心观测字段。',
  '指标':'当前宏观、利率、信用或流动性观测指标。',
  '当前值':'最新可用观测值。单位由具体指标定义，不同指标不可直接横向比较数值大小。',
  '历史状态分位':'当前指标在可用历史样本中的状态分位，用于判断相对异常程度。',
  '曲线':'Fed Funds 或 SOFR 等利率预期曲线类别。',
  '真实合约点':'当前曲线中实际取得并通过质量检查的期货合约月份数量。',
  '前端隐含利率':'曲线较近端有效合约价格换算出的隐含利率。',
  '远端隐含利率':'曲线较远端有效合约价格换算出的隐含利率。',
  '远端−前端':'远端隐含利率减前端隐含利率，用于描述期限曲线方向，不是政策结果预测。',
  '合约月':'对应利率期货合约的交割月份。',
  '代码':'外部数据源使用的合约或标识代码。',
  '价格':'最新可用合约价格，不等同于利率本身。',
  '隐含利率':'由期货价格按该合约规则换算的市场隐含利率。',
  '联合状态':'由多个风险因子共同构成并冻结定义的复合状态。',
  '领先期':'历史回溯中从该状态出现到危机观察窗口的交易日距离。',
  '危机命中':'历史危机样本中该联合状态出现的比例。',
  '正常误报':'非危机控制样本中同一状态出现的比例。',
  'Lift':'危机命中率相对正常误报率的提升幅度，用于比较区分度，不是概率。',
  'p':'Fisher精确检验的原始p值。',
  'q':'多重比较校正后的BH q值。',
  '稳健性':'留一事件等稳健性检查结果，用于判断结论是否依赖单个历史事件。',
  '实验阶段':'当前Shadow风控实验状态，不代表生产组合已经执行调整。',
  '风险敞口倍率候选':'Shadow实验提出的风险资产目标敞口倍率候选，1.00表示不缩放。',
  '防御敞口底线候选':'Shadow实验提出的最低防御资产配置比例候选。',
  '是否已作用生产权重':'明确标记Shadow风控结果是否真正写入生产权重。当前系统设计应保持为否。',
  'ETF':'当前研究路线使用的ETF或可交易产品标识。',
  '目标权重':'冻结决策中该ETF的目标配置比例，不代表券商真实成交持仓。',
  '当前实际名次':'根据已经发生的真实后验结果重新排序的当前名次，不会反向修改冻结决策。',
  '交易日':'该行对应的完整交易日。只展示满足该实验后验评价口径的数据日。',
  '最差池':'冻结时定义的最差策略池或对照池表现，用作前瞻比较，不会以后验结果倒推重选。',
  '差值':'当前方案相对对照方案的收益或指标差值。正负方向以对应表格标题定义为准。'
 },
 en:{
  'Market':'Market represented by this row: US, CN A-shares, or HK equities.',
  '252d drawdown stress':'Current benchmark drawdown pressure relative to its recent 252-session peak. It is not a forecast of future downside.',
  '63d negative momentum':'The 63-session negative-momentum state feature used by the risk model. It is historical state evidence, not a forward return forecast.',
  'Momentum percentile':'Historical percentile of the current negative-momentum state.',
  '63d volatility':'Annualized volatility state over the latest 63 available sessions. It measures path instability, not loss probability.',
  'Risk-control stage':'Current stage of the three-market Shadow risk-control experiment. It does not automatically alter production weights.',
  'Lift':'Difference in occurrence between crisis-event samples and normal controls. It is not a probability.',
  'p':'Raw Fisher exact-test p-value.',
  'q':'Benjamini-Hochberg multiple-testing adjusted q-value.',
  'Tradingday':'The complete trading day represented by the row, subject to the experiment’s posterior-evaluation eligibility rules.',
  'Worstpool':'The frozen worst-pool/control result used for prospective comparison; it is not reselected using later outcomes.',
  'Gap':'Difference between the current route and its stated control. Direction follows the table’s metric definition.'
 }
};

function tooltipUseGuide(label){
 const raw=String(label||'').trim();
 const key=normalizeHeaderLabel(raw).toLowerCase();
 const zh=lang==='zh';
 const has=(...words)=>words.some(w=>key.includes(normalizeHeaderLabel(w).toLowerCase()));
 if(has('状态','state','阶段','stage','研究意见','action','opinion'))return zh
  ? '怎么用：先确认这行能否进入正式比较。ACTIVE/EVALUATED可看正式后验；SHADOW/CANDIDATE只看验证进度；FROZEN/RETIRED不应参与当前配置。状态没成熟时，后面的高收益数字不能作为晋级依据。'
  : 'How to use: first determine whether the row is eligible for formal comparison. ACTIVE/EVALUATED can support posterior review; SHADOW/CANDIDATE are validation-only; FROZEN/RETIRED should not participate in current allocation.';
 if(has('权重','weight','敞口','exposure','调整','change','delta'))return zh
  ? '怎么用：直接比较基线权重、TRIAID权重和Δ权重，确认系统到底加了谁、减了谁。再把Δ与冻结时理由、风险、容量和后验收益差对应起来；无法解释的权重变化应进入审计。'
  : 'How to use: compare baseline weight, TRIAID weight and delta to see exactly what increased or decreased. Reconcile the delta with frozen rationale, risk, capacity and posterior return gap; unexplained changes require audit.';
 if(has('收益','return','损益','p&l','净值','equity','差值','gap','累计','cumulative','名次','rank'))return zh
  ? '怎么用：只和同一冻结时点、同一结果期的对照比较。看单期差值后必须继续看累计路径和实际名次；若优势只靠一次异常收益，不能据此升级策略。'
  : 'How to use: compare only against a control from the same freeze time and outcome period. After the period gap, inspect cumulative path and realized rank; an edge driven by one abnormal return does not justify promotion.';
 if(has('风险','risk','回撤','drawdown','波动','volatility','动量','momentum','压力','stress','分位','percentile'))return zh
  ? '怎么用：把这一列当成“异常来自哪里”的定位器。先看它是否相对自身历史异常，再去主驱动/跨市场/信用流动性里找确认；单个高分只触发追查，不直接触发权重变化。'
  : 'How to use: use this column to locate where abnormality comes from. Check whether it is unusual versus its own history, then seek confirmation in drivers, transmission and credit/liquidity; one high metric triggers investigation, not a weight change.';
 if(has('成本','cost','adv','成交','fill','流动性','liquidity','资金','capital','天数','days','投入','invested','目标仓位','notional'))return zh
  ? '怎么用：看该资金规模下是否真能成交。重点核对ADV占用、成交天数、fill ratio、模型成本和剩余目标；如果成本吃掉收益优势或目标长期填不满，这个方案就应降规模、延长执行或淘汰。'
  : 'How to use: test whether the position can actually be executed at this capital size. Focus on ADV usage, execution days, fill ratio, modeled cost and remaining target; if cost consumes the edge or the target cannot be filled, reduce scale, extend execution or reject the route.';
 if(has('样本','sample','p','q','lift','支持','support','命中','hit','稳健','robust'))return zh
  ? '怎么用：按“样本数 → 命中/误报 → Lift → p/q → 留一稳健性 → 前瞻成熟度”顺序读。任一关键环节不支持，就把它保留为候选证据，不升级成已验证结论。'
  : 'How to use: read in order: sample size → hit/false-positive rate → lift → p/q → leave-one-out robustness → prospective maturity. If a key step is unsupported, keep it as candidate evidence rather than a validated claim.';
 if(has('日期','交易日','date','day','市场','market','产品','product','etf','指标','indicator','曲线','curve','合约','contract'))return zh
  ? '怎么用：用它核对对象和时间是否一致。遇到异常先检查市场、交易日、数据源、合约月份/产品代码，再判断模型；时间或对象错位时其余列没有可比性。'
  : 'How to use: verify object and timestamp alignment. When something looks abnormal, first check market, trading day, provider and contract/product identity; if these are misaligned, the remaining columns are not comparable.';
 if(has('原因','依据','说明','rationale','why','explanation','evidence','证据'))return zh
  ? '怎么用：先读冻结时理由，再读后来真实后验，检查“当时为什么选”与“后来实际发生什么”是否一致。若长期不一致，修规则；不能用后来结果反向改写当时理由。'
  : 'How to use: read the frozen-time rationale first, then the later realized posterior and compare “why selected” with “what happened.” Persistent mismatch calls for rule revision; later outcomes must not rewrite the original rationale.';
 return zh
  ? '该表头没有专用用途说明；视为UI契约缺失，不应只凭这一列做判断。'
  : 'This header lacks dedicated usage guidance; treat it as a UI-contract gap and do not make a decision from this column alone.';
}
function tableHeaderTipMeta(label){
 const raw=String(label||'').trim();
 const key=normalizeHeaderLabel(raw);
 const extra=(TABLE_HEADER_TIPS_EXTRA[lang]||{})[key];
 if(extra)return {text:extra+'\\n'+tooltipUseGuide(raw),explicit:true};
 const local=TABLE_HEADER_TIPS[lang]||{};
 if(local[key])return {text:local[key]+'\\n'+tooltipUseGuide(raw),explicit:true};
 const fallbackOther=lang==='zh'?(TABLE_HEADER_TIPS.en||{}):(TABLE_HEADER_TIPS.zh||{});
 if(fallbackOther[key])return {text:fallbackOther[key]+'\\n'+tooltipUseGuide(raw),explicit:true};
 if(!raw)return {text:lang==='zh'?'缺少表头说明；这是UI契约缺失。':'Missing header explanation; this is a UI-contract gap.',explicit:false};
 return {
  text:(lang==='zh'
   ? '“'+raw+'”尚未配置专用定义和使用说明；这是UI契约缺失。'
   : '“'+raw+'” does not yet have a dedicated definition and usage guide; this is a UI-contract gap.'),
  explicit:false
 };
}
function tableHeaderTip(label){return tableHeaderTipMeta(label).text}
function applyTableHeaderTooltips(root=document){
 root.querySelectorAll('table th').forEach(th=>{
  const label=(th.dataset.headerLabel||th.textContent||'').trim();
  if(!label)return;
  const meta=tableHeaderTipMeta(label);
  th.classList.add('has-tip','tip-mark');
  th.dataset.tip=meta.text;
  th.dataset.tipContract=meta.explicit?'explicit':'fallback';
  th.setAttribute('aria-label',label+' — '+th.dataset.tip);
 });
}

function fmtPct(x){return x===null||x===undefined?'-':(100*x).toFixed(2)+'%'}
function fmtMoney(x){if(x===null||x===undefined)return '-';const v=Number(x);if(!Number.isFinite(v))return '-';return '¥'+v.toLocaleString(undefined,{maximumFractionDigits:0})}
function fmtUsd(x){if(x===null||x===undefined)return '-';const v=Number(x);if(!Number.isFinite(v))return '-';return "$"+v.toLocaleString(undefined,{maximumFractionDigits:0})}
function signedPct(x){if(x===null||x===undefined)return '-';const v=100*x;return (v>0?'+':'')+v.toFixed(2)+'%'}
function cls(x){return x>1e-12?'good':x<-1e-12?'bad':''}
function esc(x){return String(x??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function fmtPrice(x,currency){
 const v=Number(x);if(!Number.isFinite(v))return '-';
 const digits=v>=100?2:v>=10?3:4;
 let prefix='';
 if(currency==='USD')prefix=String.fromCharCode(36);
 else if(currency==='CNY')prefix=String.fromCharCode(165);
 else if(currency==='HKD')prefix='HK'+String.fromCharCode(36);
 return prefix+v.toFixed(digits);
}
function strategyLabelHtml(name,strategyId){
 const n=name||strategyId||'-',sid=strategyId||'';
 return '<span class="strategy-name strategy-hover" data-strategy-id="'+esc(sid)+'" data-strategy-name="'+esc(n)+'">'+esc(n)+'</span>'+
  '<span class="market-tip-icon" data-strategy-id="'+esc(sid)+'" data-strategy-name="'+esc(n)+'" aria-label="'+(lang==='zh'?'查看最新可用价格':'View latest available price')+'">i</span>';
}
function strategyMarketTip(strategyId,name){
 const m=el('market').value;
 const ctx=strategyMarketContext[m];
 const title=(name||strategyId||'-')+(strategyId&&name!==strategyId?' · '+strategyId:'');
 if(!ctx)return title+'\\n'+(lang==='zh'?'正在读取最新市场价格…':'Loading latest market prices…');
 const row=(ctx.strategies||{})[strategyId];
 if(!row)return title+'\\n'+(lang==='zh'?'当前策略暂无可用的底层资产价格映射。':'No current underlying-price mapping is available for this strategy.');
 const lines=[title];
 const assets=row.assets||[];
 if(!assets.length){
  lines.push(lang==='zh'?'当前底层：现金，无市场价格':'Current underlying: cash, no market price');
 }else{
  assets.forEach(a=>{
   const assetName=a.name||a.symbol;
   const px=fmtPrice(a.latest_price,ctx.currency);
   const chg=signedPct(a.last_trading_day_change);
   lines.push(
    lang==='zh'
     ? assetName+' '+a.symbol+' · 目标敞口 '+fmtPct(a.weight)+' · 最新可用 '+px+' · 最近交易日 '+chg
     : assetName+' '+a.symbol+' · target exposure '+fmtPct(a.weight)+' · latest available '+px+' · last trading day '+chg
   );
  });
 }
 if(Number(row.cash_weight||0)>1e-6)lines.push((lang==='zh'?'现金 ':'Cash ')+fmtPct(row.cash_weight));
 lines.push(
  (lang==='zh'?'价格源 ':'Price source ')+(ctx.provider||'-')+' · '+(ctx.price_mode||'-')+
  ' · '+(lang==='zh'?'最近交易日 ':'Last trading day ')+(ctx.last_trading_day||'-')
 );
 lines.push(
  lang==='zh'
   ? '用途：判断这个策略当前实际暴露到哪些资产、最新价格和最近交易日变化是否与目标权重相符。下一步：价格缺失、过旧或异常时先检查数据源；不要仅凭单日涨跌直接改策略权重。'
   : 'Use: verify which assets the strategy is actually exposed to and whether latest prices / last-session moves are consistent with the target weights. Next: if prices are missing, stale or anomalous, check the data source first; do not reweight from one day’s move alone.'
 );
 return lines.join('\\n');
}
function applyText(){
 const t=T[lang];
 const map={title:'title',subtitle:'subtitle',resultTitle:'result',baseReturnLabel:'baseReturn',triaidReturnLabel:'triaidReturn',gainLabel:'gain',
 liveTitle:'live',indexWindowTitle:'indexWindow',activityWindowTitle:'activityWindow',
 baseReturnSub:'baseSub',triaidReturnSub:'triaidSub',gainSub:'gainSub',overviewTitle:'overview',dateLabel:'date',coreLabel:'core',
 selectedLabel:'selected',cumLabel:'cum',curveTitle:'curve',legendBase:'legendBase',legendTriaid:'legendTriaid',dailyTitle:'daily',
 candidatePoolTitle:'candidatePool',
 usReturnMaxTitle:'usReturnMax',usrmExpectedLabel:'usrmExpected',usrmGenericLabel:'usrmGeneric',usrmSpyLabel:'usrmSpy',usrmRiskLabel:'usrmRisk',usrmStrategyTitle:'usrmStrategy',usrmAssetTitle:'usrmAsset',usrmCapitalTitle:'usrmCapital',usrmRealizedTitle:'usrmRealized',usrmControlTitle:'usrmControl',
 regimeLabel:'regime',runStateLabel:'runState',selectedNamesLabel:'selectedNames',dailyAnalysisLabel:'analysis',
 prospectiveTitle:'prospective',prospectiveDaysLabel:'prospectiveDays',prospectiveHoldLabel:'prospectiveHold',prospectiveTriaidLabel:'prospectiveTriaid',prospectiveGapLabel:'prospectiveGap',
 prospectiveStrategyTitle:'prospectiveStrategy',prospectiveDailyTitle:'prospectiveDaily',
 pthStrategy:'strategy',pthPredRank:'predRank',pthBaseWeight:'before',pthTriaidWeight:'after',pthDailyReturn:'dailyReturn',pthCumReturn:'cumReturn',pthRealRank:'realRank',pthReason:'detReason',
 recoveryWaveTitle:'recoveryWave',recoveryCashLabel:'recoveryCash',recoveryPrevDaysLabel:'recoveryPrevDays',recoveryPrevReturnLabel:'recoveryPrevReturn',recoveryPrevGapLabel:'recoveryPrevGap',
 recoveryOpinionTitle:'recoveryOpinion',recoveryReviewTitle:'recoveryReview',rwthProduct:'product',rwthAction:'action',rwthTarget:'target',rwthChange:'change',rwthDrawdown:'drawdown',rwthDirection:'direction',rwthHorizon:'horizon',rwthSpeed:'speed',rwthHitEdge:'hitEdge',rwthExpected:'expected',rwthSamples:'samples',
 capitalSleeveTitle:'capitalSleeve',capitalRealizedTitle:'capitalRealized',csthCapital:'capital',csthInvested:'invested',csthParticipation:'participation',csthDays:'days',csthCost:'cost',csthPnl:'pnl',csthReturn:'netReturn',crthCapital:'capital',crthFill:'fill',crthEquity:'equity',crthPnl:'realizedPnl',crthReturn:'realizedReturn',crthCost:'executionCost',crthRemaining:'remaining',
 rvrDate:'resultDate',rvrPortfolio:'portfolioDay',rvrEqual:'equalDay',rvrCum:'portfolioCum',rvrGap:'gapCum',strategyTitle:'strategies',
 thStrategy:'strategy',thState:'state',thExp:'exp',thRisk:'risk',thBase:'before',thTriaid:'after',thDelta:'delta',thWhy:'why',
 evolutionTitle:'evolution',evoObservedLabel:'observed',evoNegLabel:'negative',evoCandidateLabel:'next',evoNote:'evoNote',
 proposeBtn:'propose',runBtn:'run',runAllBtn:'runAll'};
 Object.entries(map).forEach(([id,key])=>el(id).textContent=t[key]);
 const tips=TIP[lang];
 const headerTips={
  thStrategy:'strategy',thState:'state',thExp:'exp',thRisk:'risk',thBase:'before',thTriaid:'after',thDelta:'delta',thWhy:'why',
  cthStrategy:'strategy',cthState:'state',cthExp:'exp',cthRisk:'risk',cthWhy:'candidateWhy'
 };
 Object.entries(headerTips).forEach(([id,key])=>{if(el(id))el(id).dataset.tip=tips[key]});
 applyTableHeaderTooltips();
 applyUiTooltips();
 if(el('selectedMarketKicker'))el('selectedMarketKicker').textContent=lang==='zh'?'当前市场':'Selected market';
 if(el('marketSectionNote'))el('marketSectionNote').textContent=lang==='zh'
  ?'以下先展示当前所选市场的结果、行情、策略池和该市场专属实验。三市场联动风险中心放在单市场内容之后。'
  :'The selected market’s results, live data, strategy pool and market-specific experiments appear first. The three-market linked risk center follows later.';
 if(el('crossMarketRiskNote'))el('crossMarketRiskNote').textContent=lang==='zh'
  ?'以下为跨市场联动风险层。它始终联合分析 US、A股、港股，不随上方单市场选择器切换。'
  :'The section below is the cross-market risk layer. It always analyzes US, CN and HK jointly and does not switch with the selected market.';
}
function statusActionGuide(status){
 const key=String(status||'').toLowerCase();
 const zh=lang==='zh';
 const mapZh={
  active:'怎么用：该策略可以进入当前正式策略群。重点看Δ权重、风险/容量和后续EVALUATED相对收益；若多次完成后验持续落后对应基线，或容量/风险约束失效，就进入降级复核。',
  shadow:'怎么用：只允许积累前瞻证据，不给正式配置权重。先看完整交易日样本、稳定性和对照结果；未达到准入门槛前，单次高收益不改变状态。',
  reduced:'怎么用：当前影响已被压低。核对触发降级的具体证据是否恢复；未恢复则继续减弱或冻结，恢复后也要重新通过准入检查。',
  frozen:'怎么用：当前不得参与正式配置。先查冻结原因；只有新的可复现证据重新通过准入链后才能恢复，不因短期反弹自动解冻。',
  candidate:'怎么用：仍是候选。逐项看 replay、holdout、shadow、前瞻、容量和audit 是否通过；缺一项都不能晋级。',
  research:'怎么用：只用于研发。先完成复现、压力测试、对照和前瞻样本，再决定是否进入Candidate。',
  retired:'怎么用：已退出当前策略体系。除非出现新的、可复现且足以重新打开准入链的证据，否则不再投入正式配置资源。',
  preview_ready:'怎么用：只检查页面与即时计算结果。不得写入正式后验、累计曲线或生命周期晋级证据。'
 };
 const mapEn={
  active:'How to use: eligible for the active strategy group. Inspect weight delta, risk/capacity and subsequent EVALUATED relative return; repeated completed underperformance or failed constraints triggers downgrade review.',
  shadow:'How to use: prospective evidence only, with no live allocation. Inspect complete trading-day samples, stability and control results; one strong result does not change status before admission gates pass.',
  reduced:'How to use: influence is already constrained. Recheck the evidence that caused downgrade; if it has not recovered, continue reducing or freeze. Recovery still requires re-admission checks.',
  frozen:'How to use: excluded from active allocation. Inspect the freeze reason; restore only after new reproducible evidence passes admission again, not because of a short-term rebound.',
  candidate:'How to use: still a candidate. Check replay, holdout, shadow, prospective, capacity and audit one by one; any missing gate blocks promotion.',
  research:'How to use: R&D only. Complete reproduction, stress tests, controls and prospective samples before Candidate admission.',
  retired:'How to use: outside the current strategy system. Do not allocate formal resources unless new reproducible evidence is strong enough to reopen admission.',
  preview_ready:'How to use: display/immediate-computation check only. Do not write it into formal posterior evidence, cumulative curves or lifecycle promotion.'
 };
 return (zh?mapZh:mapEn)[key]||(zh
  ? '状态缺少专用动作说明；先查状态定义和准入规则，不要仅凭收益数字行动。'
  : 'This state lacks dedicated action guidance; inspect its definition and admission rules before acting on return numbers.');
}
function statusTip(status){
 const key=String(status||'').toLowerCase();
 return (TIP[lang][key]||TIP[lang].state)+'\\n'+statusActionGuide(status);
}
async function json(url,opts){const r=await fetch(url,opts);if(!r.ok)throw new Error(await r.text());return r.json()}
async function jsonOrNull(url,opts){try{return await json(url,opts)}catch(e){return null}}
const uiFetchCache=new Map();
let refreshSeq=0,liveSeq=0,marketSwitchSeq=0;
async function jsonCached(url,ttlMs=12000){
 const now=Date.now(),hit=uiFetchCache.get(url);
 if(hit&&hit.data!==undefined&&now-hit.at<ttlMs)return hit.data;
 if(hit&&hit.promise)return hit.promise;
 const prior=hit&&hit.data!==undefined?hit.data:undefined;
 const priorAt=hit?.at||0;
 const promise=json(url).then(data=>{
   uiFetchCache.set(url,{data,at:Date.now(),promise:null});
   return data;
 }).catch(e=>{
   if(prior!==undefined)uiFetchCache.set(url,{data:prior,at:priorAt,promise:null});
   else uiFetchCache.delete(url);
   throw e;
 });
 uiFetchCache.set(url,{data:prior,at:priorAt,promise});
 return promise;
}
async function jsonCachedStale(url,ttlMs=12000){
 const now=Date.now(),hit=uiFetchCache.get(url);
 if(hit&&hit.data!==undefined){
   if(now-hit.at>=ttlMs&&!hit.promise)jsonCached(url,ttlMs).catch(()=>null);
   return hit.data;
 }
 return jsonCached(url,ttlMs);
}
async function jsonOrNullCached(url,ttlMs=12000){try{return await jsonCached(url,ttlMs)}catch(e){return null}}
function clearMarketCache(m){
 for(const key of [...uiFetchCache.keys()]){
   if(key.includes('market_id='+m)||key.includes('/'+m)||key==='/api/status'||key==='/api/evolution'||key.includes('/api/risk-'))uiFetchCache.delete(key);
 }
}
function warmMarketCache(m){
 const urls=[
   '/api/daily?compact=true&market_id='+m,
   '/api/strategies?market_id='+m+'&lang='+lang,
   '/api/curves?market_id='+m,
   '/api/runs?market_id='+m+'&limit=100'
 ];
 urls.forEach(url=>jsonCached(url,30000).catch(()=>null));
}
function warmAllMarkets(){
 const selected=el('market').value;
 const queue=['US','CN','HK'].filter(m=>m!==selected);
 queue.forEach((m,i)=>setTimeout(()=>warmMarketCache(m),1200+(i*1400)));
}
const MARKET_UI={
 US:{zh:'美股 / US',en:'US Equities',timezone:'America/New_York',routeZh:'Return-Max 主路线，纳入容量与模型执行成本；风险资产 SPY / QQQ / IWM，防御资产 TLT / GLD。',routeEn:'Return-Max primary route with capacity and modeled execution cost; SPY / QQQ / IWM are risk assets and TLT / GLD are defensive assets.'},
 CN:{zh:'A股 / CN',en:'China A-shares',timezone:'Asia/Shanghai',routeZh:'A股收益最大化主路线，使用 510300 / 510500 / 创业板ETF / 中证1000ETF，并以国债ETF作为防御资产。',routeEn:'CN return-max primary route using CSI 300 / CSI 500 / ChiNext / CSI 1000 ETFs with a government-bond ETF as the defensive sleeve.'},
 HK:{zh:'港股 / HK',en:'Hong Kong Equities',timezone:'Asia/Hong_Kong',routeZh:'港股独立收益最大化路线；2800 / 2828 / 3033 为风险资产，2819 为防御资产，策略权重与后验独立记录。',routeEn:'Independent HK return-max route; 2800 / 2828 / 3033 are risk assets and 2819 is the defensive sleeve, with independent weights and posterior evidence.'}
};
let marketClockState={};
let homeSummaryState={
 selectedCount:null,
 changedCount:null,
 selectedNames:[],
 evaluated:null,
 preview:false,
 latestStatus:null,
 riskScore:null,
 riskBand:null
};
function renderHomeSummary(){
 const m=el('market').value;
 const meta=MARKET_UI[m]||{};
 const clock=marketClockState[m]||{};
 const zh=lang==='zh';
 const marketName=zh?(meta.zh||m):(meta.en||m);
 const phase=phaseText(clock.session_phase||'-');
 el('homeSummaryKicker').textContent=zh?'START HERE':'START HERE';
 el('homeSummaryTitle').textContent=zh?'第一次看 TRIAID FIN？先看这里':'New to TRIAID FIN? Start here';
 el('homeSummaryPurpose').textContent=zh
  ? 'TRIAID FIN 用真实市场数据动态选择和调整策略权重，再等待真实后验结果验证这些调整是否真正提高了可实现净收益。重点不是预测某一天涨跌，而是把“选择—调整—验证—进化”做成可回溯的长期实验。'
  : 'TRIAID FIN uses real market data to select and dynamically reweight strategies, then waits for realized outcomes to test whether those changes actually improve realizable net return. The goal is not a one-day price call; it is a traceable select → adjust → validate → evolve research loop.';
 el('homeSummaryMode').textContent=zh?'研究 / Shadow · 不连接券商 · 不自动交易':'Research / Shadow · no broker connection · no auto-trading';
 el('homeSummaryMarketLabel').textContent=zh?'当前查看':'Current market';
 el('homeSummaryDecisionLabel').textContent=zh?'TRIAID 当前在做什么':'What TRIAID is doing';
 el('homeSummaryValidationLabel').textContent=zh?'最近一次真实验证':'Latest realized validation';
 el('homeSummaryRiskLabel').textContent=zh?'三市场联动风险':'Three-market risk';
 el('homeSummaryMarket').textContent=marketName+' · '+phase;
 el('homeSummaryMarket').className='home-summary-value '+(clock.is_open?'open':'closed');
 el('homeSummaryMarketDetail').textContent=zh
  ? ((clock.is_open?'绿色表示官方交易时段':'灰色表示当前非官方交易时段')+' · '+(clock.benchmark?'基准 '+clock.benchmark:'等待市场元数据'))
  : ((clock.is_open?'Green means the official session is open':'Gray means the official session is closed')+' · '+(clock.benchmark?'benchmark '+clock.benchmark:'awaiting market metadata'));

 const count=homeSummaryState.selectedCount;
 const changed=homeSummaryState.changedCount;
 const names=homeSummaryState.selectedNames||[];
 el('homeSummaryDecision').textContent=count==null
  ? (zh?'等待策略数据':'Awaiting strategy data')
  : (zh?(count+' 个策略入选 · '+changed+' 个权重调整'):(count+' selected · '+changed+' reweighted'));
 el('homeSummaryDecisionDetail').textContent=names.length
  ? (zh?'主要入选：':'Leading selections: ')+names.slice(0,3).join(zh?'、':' · ')
  : (zh?'读取当前策略群与动态权重':'Reading current strategy group and dynamic weights');

 const ev=homeSummaryState.evaluated;
 if(homeSummaryState.preview){
  el('homeSummaryValidation').textContent=zh?'当前为即时预览':'Manual preview active';
  el('homeSummaryValidation').className='home-summary-value';
  el('homeSummaryValidationDetail').textContent=zh?'预览不进入正式证据链，摘要仍等待真实后验。':'Preview results do not enter the formal evidence chain; the summary still waits for realized evidence.';
 }else if(ev&&ev.evaluation){
  const e=ev.evaluation,g=Number(e.excess_return);
  el('homeSummaryValidation').textContent=Number.isFinite(g)
   ? ((zh?'相对基线 ':'Vs baseline ')+signedPct(g))
   : (zh?'已有真实后验':'Realized outcome available');
  el('homeSummaryValidation').className='home-summary-value '+(Number.isFinite(g)?cls(g):'');
  el('homeSummaryValidationDetail').textContent=(Number.isFinite(Number(e.triaid_return))&&Number.isFinite(Number(e.baseline_return)))
   ? ((zh?'TRIAID ':'TRIAID ')+fmtPct(e.triaid_return)+' · '+(zh?'基线 ':'baseline ')+fmtPct(e.baseline_return))
   : (zh?'真实结果已登记，详细比较见下方。':'Realized evidence is recorded; see the detailed comparison below.');
 }else{
  el('homeSummaryValidation').textContent=zh?'等待真实后验':'Awaiting realized outcome';
  el('homeSummaryValidation').className='home-summary-value';
  el('homeSummaryValidationDetail').textContent=zh
   ? '只有已经发生并满足证据口径的市场结果才算验证。'
   : 'Only realized market outcomes that meet the evidence rules count as validation.';
 }

 const hasRisk=homeSummaryState.riskScore!==null&&homeSummaryState.riskScore!==undefined&&Number.isFinite(Number(homeSummaryState.riskScore));
 if(hasRisk){
  const score=Number(homeSummaryState.riskScore),band=riskBandFromScore(score);
  el('homeSummaryRisk').textContent=score.toFixed(1)+'/100 · '+riskBandText(score);
  el('homeSummaryRisk').className='home-summary-value home-summary-risk risk-number '+band+' has-tip';
  el('homeSummaryRisk').dataset.tip=riskScaleTip(score,zh?'三市场联动综合风险':'Three-market aggregate risk');
 }else{
  el('homeSummaryRisk').textContent=zh?'读取中':'Loading';
  el('homeSummaryRisk').className='home-summary-value';
  delete el('homeSummaryRisk').dataset.tip;
 }
 el('homeSummaryRiskDetail').textContent=zh
  ? 'US / A股 / 港股联合风险层 · 用于决定是否加密监控或启动Shadow收紧对照 · 不直接改权重'
  : 'US / CN / HK joint risk layer · used to decide whether to tighten monitoring or launch shadow constraints · does not directly change weights';

 const validationPhrase=homeSummaryState.preview
  ? (zh?'当前有即时预览，但它不进入正式证据链':'a manual preview is active but does not enter the formal evidence chain')
  : ev&&ev.evaluation
    ? (zh?'最近真实后验已经可比较':'the latest realized posterior is available for comparison')
    : (zh?'仍在等待满足口径的真实后验':'the system is still waiting for an eligible realized posterior');
 const riskPhrase=hasRisk
  ? (zh?('三市场风险为 '+Number(homeSummaryState.riskScore).toFixed(1)+'/100（'+riskBandText(homeSummaryState.riskScore)+'）'):('three-market risk is '+Number(homeSummaryState.riskScore).toFixed(1)+'/100 ('+riskBandText(homeSummaryState.riskScore)+')'))
  : (zh?'三市场风险正在读取':'three-market risk is loading');
 el('homeSummaryStory').textContent=zh
  ? ('现在你看到的是 '+marketName+'；TRIAID 当前从策略池中选入 '+(count==null?'若干':count)+' 个策略，'+(changed==null?'并根据证据调整权重':('其中 '+changed+' 个发生权重调整'))+'；'+validationPhrase+'；'+riskPhrase+'。')
  : ('You are viewing '+marketName+'. TRIAID currently selected '+(count==null?'a set of':count)+' strategies, '+(changed==null?'with evidence-based reweighting':changed+' of them reweighted')+'; '+validationPhrase+'; '+riskPhrase+'.');
 el('homeSummaryGuide').textContent=zh
  ? '阅读顺序：当前市场与交易状态 → 策略选择/权重调整 → 真实后验结果 → 三市场风险。下面的复杂表格主要用于追溯证据，第一次使用不需要逐项读完。'
  : 'Read in this order: current market/session → strategy selection and reweighting → realized outcome → three-market risk. The detailed tables below are mainly for evidence traceability; first-time users do not need to read every row.';
}
function phaseText(phase){
 const p=String(phase||'');
 const zh={OPEN:'交易中',PREOPEN:'盘前',BREAK:'午间休市',POSTCLOSE:'盘后',CLOSED:'休市',CALENDAR_UNAVAILABLE:'日历不可用'};
 const en={OPEN:'OPEN',PREOPEN:'PREOPEN',BREAK:'BREAK',POSTCLOSE:'POSTCLOSE',CLOSED:'CLOSED',CALENDAR_UNAVAILABLE:'CALENDAR N/A'};
 return (lang==='zh'?zh:en)[p]||p||'-';
}
function selectMarket(m){
 if(!['US','CN','HK'].includes(m))return;
 el('market').value=m;
 onMarketChange();
}
function tickMarketClocks(){
 const now=new Date();
 ['US','CN','HK'].forEach(m=>{
  const meta=MARKET_UI[m];
  const tz=marketClockState[m]?.timezone||meta.timezone;
  try{
   el('clockTime'+m).textContent=new Intl.DateTimeFormat([],{
    timeZone:tz,hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false
   }).format(now);
   el('clockDate'+m).textContent=new Intl.DateTimeFormat(lang==='zh'?'zh-CN':'en-US',{
    timeZone:tz,month:'2-digit',day:'2-digit',weekday:'short'
   }).format(now)+' · '+tz;
  }catch(e){}
 });
}
function renderMarketIdentity(){
 const m=el('market').value;
 const meta=MARKET_UI[m];
 const state=marketClockState[m]||{};
 document.querySelectorAll('[data-clock-market]').forEach(node=>{
  const key=node.dataset.clockMarket;
  node.classList.toggle('selected',key===m);
  node.classList.toggle('open',!!marketClockState[key]?.is_open);
 });
 el('selectedMarketName').textContent=lang==='zh'?meta.zh:meta.en;
 el('selectedMarketRoute').textContent=lang==='zh'?meta.routeZh:meta.routeEn;
 const phase=state.session_phase||'-';
 el('selectedMarketSession').textContent=phaseText(phase);
 el('selectedMarketSession').className='market-session-badge'+(state.is_open?' open':'');
 const bench=state.benchmark||'-',mode=state.primary_experiment_mode||'-',currency=state.currency||'-';
 el('selectedMarketMeta').textContent=(lang==='zh'
  ? '基准 '+bench+' · 主路线 '+mode+' · 计价 '+currency
  : 'Benchmark '+bench+' · primary route '+mode+' · currency '+currency);
 ['US','CN','HK'].forEach(key=>{
  const s=marketClockState[key]||{};
  el('clockPhase'+key).textContent=phaseText(s.session_phase);
 });
 renderHomeSummary();
}
async function refreshMarketClocks(){
 try{
  const payload=await jsonCached('/api/ui/market-clocks',15000);
  marketClockState=Object.fromEntries((payload.markets||[]).map(x=>[x.market_id,x]));
  renderMarketIdentity();
  tickMarketClocks();
 }catch(e){
  renderMarketIdentity();
 }
}
function localClockFromEpoch(ts){
 if(ts===null||ts===undefined)return '-';
 try{return new Date(Number(ts)*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});}catch(e){return '-'}
}
function localDateTimeFromEpoch(ts){
 if(ts===null||ts===undefined)return '-';
 try{return new Date(Number(ts)*1000).toLocaleString();}catch(e){return '-'}
}
function localClockFromIso(x){
 if(!x)return '-';
 try{return new Date(x).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});}catch(e){return '-'}
}
function setPulse(id,on,warn=false){
 const node=el(id);node.className='pulse'+(on?' on':warn?' warn':'');
}
async function refreshLiveWindows(){
 const m=el('market').value;
 const seq=++liveSeq;
 const idxPromise=jsonCached('/api/market-data/live-indicators/'+m,2000);
 const actPromise=jsonCached('/api/market-data/activity/'+m+'?limit=80',2500);

 try{
  const idx=await idxPromise;
  if(seq!==liveSeq||el('market').value!==m)return;
  const fresh=idx.available&&Number(idx.freshness_seconds||999999)<180;
  setPulse('marketPulse',fresh,idx.available&&!fresh);
  el('indexPhase').textContent=(idx.session_phase||'-')+' · '+(idx.available?localClockFromEpoch(idx.source_latest_ts):'-');
  el('indexMeta').textContent=idx.available
   ? ((lang==='zh'?'数据源 ':'Provider ')+(idx.provider||'-')+' · '+(lang==='zh'?'延迟 ':'age ')+Math.round(Number(idx.freshness_seconds||0))+'s')
   : (lang==='zh'?'暂无可用市场数据':'No market data available');
  el('indexRows').innerHTML=(idx.instruments||[]).map(x=>{
   const p=Number(x.change_pct);
   const pText=Number.isFinite(p)?signedPct(p):'-';
   const px=Number(x.close);
   return '<div class="indexitem">'+
    '<div class="small muted">'+esc(x.name||x.symbol)+'</div>'+
    '<div class="px">'+(Number.isFinite(px)?px.toFixed(px>=100?2:3):'-')+'</div>'+
    '<div class="chg '+(Number.isFinite(p)?cls(p):'')+'">'+pText+'</div></div>';
  }).join('');
 }catch(e){
  if(seq===liveSeq&&el('market').value===m){
   setPulse('marketPulse',false,true);
   el('indexMeta').textContent='Live data error: '+e.message;
  }
 }

 try{
  const act=await actPromise;
  if(seq!==liveSeq||el('market').value!==m)return;
  const events=act.events||[];
  const last=events.length?events[events.length-1]:null;
  const recent=last&&((Date.now()-new Date(last.at).getTime())<180000);
  setPulse('activityPulse',!!recent,events.length>0&&!recent);
  el('activityPhase').textContent=act.session_phase||'-';
  el('scheduleMeta').textContent=(lang==='zh'?'当前调度: ':'Schedule: ')+(act.schedule_text||'-');
  el('commandLog').innerHTML=[...events].reverse().map(e=>{
   return '<div class="cmd"><span class="cmdtime">'+esc(localClockFromIso(e.at))+'</span> '+
    '<span class="cmdkind">'+esc(e.kind||'EVENT')+'</span> '+
    '<span class="cmdmode">'+esc(e.mode||'')+'</span> '+
    esc(e.message||'')+'</div>';
  }).join('') || '<div class="cmd">'+(lang==='zh'?'暂无后台事件':'No backend events')+'</div>';
 }catch(e){
  if(seq===liveSeq&&el('market').value===m){
   setPulse('activityPulse',false,true);
   el('scheduleMeta').textContent='Activity error: '+e.message;
  }
 }

 jsonCached('/api/market-data/strategy-context/'+m,60000)
   .then(ctx=>{strategyMarketContext[m]=ctx;})
   .catch(()=>{});
}
function applyMarketScope(){
 const m=el('market').value;
 const hk=m==='HK';
 el('runBtn').disabled=false;
 el('marketScopeStatus').style.display=hk?'block':'none';
 el('marketScopeStatus').textContent=hk
  ? (lang==='zh'
     ? '港股独立路线：2800/2828/3033 为风险资产，2819 为防御资产；策略选择、TRIAID权重和后验单独记录。切到港股后应只用港股冻结基线与后验比较，不能拿美股/A股权重或结果横向替代。'
     : 'Hong Kong uses an independent route: 2800/2828/3033 are risky assets and 2819 is the defensive sleeve. Strategy selection, TRIAID weights and posterior evidence are recorded separately. Compare HK only with its own frozen HK control; do not substitute US/CN weights or outcomes.')
  : '';
}
function onMarketChange(){
 const seq=++marketSwitchSeq;
 applyMarketScope();
 renderMarketIdentity();
 requestAnimationFrame(()=>{
   if(seq!==marketSwitchSeq)return;
   refreshAll(true);
   refreshLiveWindows();
 });
}
async function runNow(){
 const m=el('market').value;
 const x=await json('/api/live/run/'+m,{method:'POST'});
 el('runStatus').textContent=T[lang].running+' '+x.run_id;pollRun(x.run_id,m);
}
async function runAll(){
 const x=await json('/api/live/run-all',{method:'POST'});
 el('runStatus').textContent=T[lang].running+' '+x.runs.map(r=>r.run_id).join(' | ');
 x.runs.forEach(r=>pollRun(r.run_id,r.market_id));
}
async function pollRun(id,market){
 for(let i=0;i<60;i++){
  await new Promise(r=>setTimeout(r,1000));
  try{
   const x=await json('/api/runs/'+id);el('runStatus').textContent=id+' · '+x.status;
   if(!['CREATED','FETCHING_DATA'].includes(x.status)){
    if(x.status==='PREVIEW_READY')previewRunIds[market]=id;
    clearMarketCache(market);
    await refreshAll();
    return;
   }
  }catch(e){}
 }
}
function drawCurve(points){
 const c=el('curve'),g=c.getContext('2d');g.clearRect(0,0,c.width,c.height);
 if(!points.length){g.fillStyle='#7b8593';g.font='13px system-ui';g.fillText(T[lang].noEval,18,32);return}
 const vals=points.flatMap(p=>[p.baseline_equity,p.triaid_equity]);
 let lo=Math.min(...vals),hi=Math.max(...vals);if(hi-lo<1e-8){hi+=.01;lo-=.01}
 const X=i=>45+(c.width-70)*i/Math.max(1,points.length-1);const Y=v=>25+(c.height-55)*(hi-v)/(hi-lo);
 g.strokeStyle='#e1e6ec';g.lineWidth=1;for(let j=0;j<4;j++){const y=25+(c.height-55)*j/3;g.beginPath();g.moveTo(45,y);g.lineTo(c.width-20,y);g.stroke()}
 [['baseline_equity','#6f7782'],['triaid_equity','#1769e0']].forEach(([key,color])=>{
  g.strokeStyle=color;g.lineWidth=3;g.beginPath();points.forEach((p,i)=>{const x=X(i),y=Y(p[key]);i?g.lineTo(x,y):g.moveTo(x,y)});g.stroke();
 });
}
function riskBandFromScore(score){
 const n=Number(score);
 if(!Number.isFinite(n))return '';
 if(n>=80)return 'critical';
 if(n>=65)return 'severe';
 if(n>=45)return 'high';
 if(n>=25)return 'elevated';
 return 'low';
}
function riskBandText(score){
 const band=riskBandFromScore(score);
 const zh={low:'低',elevated:'升高',high:'高',severe:'严重',critical:'临界'};
 const en={low:'LOW',elevated:'ELEVATED',high:'HIGH',severe:'SEVERE',critical:'CRITICAL'};
 return (lang==='zh'?zh:en)[band]||'-';
}
function riskScaleTip(score,label){
 const n=Number(score);
 if(!Number.isFinite(n))return label||'Risk';
 const lines=[
  (label|| (lang==='zh'?'风险压力':'Risk pressure'))+' '+n.toFixed(1)+'/100 · '+riskBandText(n),
  lang==='zh'
   ? '怎么用：把这个数当作当前这一项的异常/压力程度，只和同一口径的历史或同列对象比较；越高表示越值得继续追查原因。'
   : 'How to use: treat this as the abnormality/pressure level for this specific metric and compare only within the same definition or column; higher values deserve deeper investigation.',
  lang==='zh'
   ? '边界：单个指标不直接触发风控动作，必须和驱动证据、跨市场确认及真实后验一起判断。'
   : 'Boundary: one metric alone does not trigger risk action; combine it with driver evidence, cross-market confirmation and realized posterior outcomes.'
 ];
 return lines.join(String.fromCharCode(10));
}
function riskDriverSummary(d){
 if(!d||typeof d!=='object')return '';
 const name=lang==='zh'?(d.label_zh||d.id||'-'):(d.id||d.label_zh||'-');
 const bits=[];
 if(d.triggered!==undefined)bits.push((lang==='zh'?'触发 ':'triggered ')+(d.triggered?'YES':'NO'));
 if(d.historically_statistically_supported!==undefined)bits.push((lang==='zh'?'历史支持 ':'historical support ')+(d.historically_statistically_supported?'YES':'NO'));
 if(d.state)bits.push((lang==='zh'?'状态 ':'state ')+d.state);
 if(d.score!==undefined&&d.score!==null&&Number.isFinite(Number(d.score)))bits.push((lang==='zh'?'强度 ':'score ')+(Number(d.score)*100).toFixed(0)+'/100');
 if(d.percentile!==undefined&&d.percentile!==null&&Number.isFinite(Number(d.percentile)))bits.push((lang==='zh'?'历史分位 ':'hist pct ')+(Number(d.percentile)*100).toFixed(0)+'%');
 if(d.value!==undefined&&d.value!==null&&Number.isFinite(Number(d.value)))bits.push((lang==='zh'?'当前值 ':'value ')+Number(d.value).toFixed(Math.abs(Number(d.value))<1?4:2));
 if(d.alert_count!==undefined&&d.alert_count!==null)bits.push((lang==='zh'?'命中 ':'hits ')+d.alert_count+'/'+(d.min_hits??'-'));
 if(d.count!==undefined&&d.count!==null)bits.push((lang==='zh'?'数量 ':'count ')+d.count);
 if(d.usable!==undefined)bits.push((lang==='zh'?'可用 ':'usable ')+(d.usable?'YES':'NO'));
 return name+(bits.length?' · '+bits.join(' · '):'');
}
function riskSubscoreTip(key,score,report){
 const labels={
  structural:lang==='zh'?'长期结构脆弱':'Structural vulnerability',
  rates_policy:lang==='zh'?'利率/政策压力':'Rates / policy pressure',
  transmission:lang==='zh'?'跨市场传导':'Cross-market transmission',
  credit_liquidity:lang==='zh'?'信用/流动性':'Credit / liquidity',
  market_deterioration:lang==='zh'?'价格结构恶化':'Market deterioration'
 };
 const impacts={
  structural:lang==='zh'?'影响：主要抬高120日和250日中长期风险，用来判断当前高收益/高估值环境是否缺少长期安全垫。':'Impact: mainly raises 120d/250d risk and tests whether the current high-return/high-valuation regime lacks a long-run cushion.',
  rates_policy:lang==='zh'?'影响：这是利率与政策预期重新定价压力；高位时重点影响久期、估值和高杠杆资产，是当前是否需要加强Shadow防御测试的重要输入。':'Impact: captures rates/policy repricing pressure; high readings matter most for duration, valuation and levered assets and determine whether shadow defensive tests should be strengthened.',
  transmission:lang==='zh'?'影响：判断压力是否已经从单一市场扩散成三市场联动。单市场风险高但传导低，和系统性扩散是两种完全不同的状态。':'Impact: tests whether stress has spread from one market into a three-market transmission state. High single-market stress with low transmission is materially different from systemic spread.',
  credit_liquidity:lang==='zh'?'影响：判断风险是否开始进入融资、信用利差和市场流动性层。这个维度上升时，纸面可交易策略更容易出现容量和成交折损。':'Impact: tests whether stress has reached funding, credit spreads and market liquidity. Rising readings increase the chance that paper strategies lose capacity or execution quality.',
  market_deterioration:lang==='zh'?'影响：反映三市场价格本身是否已经出现回撤、负动量和波动恶化，是“宏观风险是否已经落到价格上”的确认层。':'Impact: reflects whether drawdown, negative momentum and volatility deterioration are already visible in prices; it is the confirmation layer for whether macro stress has reached markets.'
 };
 const next={
  structural:lang==='zh'?'下一步：看长期拉伸是否持续，以及多年下行确认是否开始增强；只有两者持续改善，才视为结构风险真正下降。':'Next: watch whether long-run stretch persists and whether secular-downturn confirmation strengthens; structural risk only truly eases when both improve.',
  rates_policy:lang==='zh'?'下一步：重点盯2Y美债90日变化、Fed Funds期货30日重定价、MOVE和政策重定价联合状态。若这些同步回落，风险可降级；若继续高位并叠加跨市场传导/信用压力，则升级。':'Next: watch 2Y 90d moves, 30d Fed Funds futures repricing, MOVE and the policy-repricing composite. Synchronous normalization supports de-escalation; persistence plus transmission/credit stress supports escalation.',
  transmission:lang==='zh'?'下一步：看跨市场相关性、压力市场占比和港股桥梁效应是否继续增强。若相关性回落且压力不扩散，可降级；若多市场同步恶化，则升级。':'Next: watch cross-market correlation, stressed-market share and the HK bridge. Falling correlation without spread supports de-escalation; synchronized deterioration supports escalation.',
  credit_liquidity:lang==='zh'?'下一步：看信用利差、金融条件和流动性指标是否从“未确认”转为同步恶化。没有信用/流动性确认时，不把宏观压力直接等同于系统性风险。':'Next: watch whether credit spreads, financial conditions and liquidity shift from unconfirmed to synchronized deterioration. Without credit/liquidity confirmation, do not equate macro pressure with systemic risk.',
  market_deterioration:lang==='zh'?'下一步：看US/CN/HK的回撤、63日动量和波动是否从局部变成一致恶化。价格层若改善，即使宏观压力仍高，也说明传导尚未完全落地。':'Next: watch whether US/CN/HK drawdown, 63d momentum and volatility move from local to synchronized deterioration. Price improvement despite macro stress means transmission is not fully realized.'
 };
 const detail=((report||{}).detail||{})[key]||[];
 const evidence=Array.isArray(detail)?detail.map(riskDriverSummary).filter(Boolean).slice(0,5):[];
 const lines=[
  (labels[key]||key)+' '+Number(score).toFixed(1)+'/100 · '+riskBandText(score),
  (lang==='zh'?'为什么：':'Why: ')+(evidence.length?evidence.join('；'):(lang==='zh'?'当前没有可展示的分项证据。':'No component evidence is available.')),
  impacts[key]||'',
  next[key]||'',
  lang==='zh'?'边界：这是风险证据层，不是亏损概率；不会单独触发自动交易或直接修改生产权重。':'Boundary: this is an evidence-pressure layer, not loss probability; it cannot independently trigger trading or production-weight changes.'
 ].filter(Boolean);
 return lines.join(String.fromCharCode(10));
}
function riskOverallTip(score,report){
 const drivers=(report?.main_drivers||[]).map(x=>{
  const name=lang==='zh'?(x.label_zh||x.id):x.id;
  const sev=x.severity==null?'':(' '+Math.round(Number(x.severity)*100)+'/100');
  return name+sev;
 }).slice(0,4);
 const blockers=(report?.missing_confirmations||[]).map(x=>lang==='zh'?(x.label_zh||x.id):x.id).slice(0,3);
 const de=(report?.deescalation_conditions||[]).map(x=>lang==='zh'?(x.meaning_zh||x.condition):x.condition).slice(0,3);
 const lines=[
  (lang==='zh'?'综合风险 ':'Overall risk ')+Number(score).toFixed(1)+'/100 · '+riskBandText(score),
  (lang==='zh'?'主要推高因素：':'Main drivers: ')+(drivers.length?drivers.join('；'):'-'),
  (lang==='zh'?'尚未确认：':'Still unconfirmed: ')+(blockers.length?blockers.join('；'):'-'),
  (lang==='zh'?'怎么用：看“推高因素”是否继续扩散，同时看“尚未确认项”会不会转为确认；只有两边同时恶化，系统性风险证据才真正增强。':'How to use: watch whether drivers broaden and whether currently unconfirmed dimensions become confirmed; systemic evidence strengthens only when both happen.'),
  (lang==='zh'?'解除看什么：':'De-escalation: ')+(de.length?de.join('；'):'-'),
  lang==='zh'?'边界：不自动改生产权重，也不是股灾概率。':'Boundary: does not automatically change production weights and is not crash probability.'
 ];
 return lines.join(String.fromCharCode(10));
}
function riskHorizonTip(h,entry,report){
 const n=Number(entry?.risk_pressure_index);
 const weights=entry?.weights||{};
 const ss=report?.subscores||{};
 const names={
  structural:lang==='zh'?'结构':'structural',
  rates_policy:lang==='zh'?'利率/政策':'rates/policy',
  transmission:lang==='zh'?'传导':'transmission',
  credit_liquidity:lang==='zh'?'信用/流动性':'credit/liquidity',
  market_deterioration:lang==='zh'?'价格恶化':'market deterioration'
 };
 const contributions=Object.keys(weights).map(k=>{
  const w=Number(weights[k]||0),s=Number((ss[k]||{}).score_0_100||0);
  return {k,c:w*s,w,s};
 }).sort((a,b)=>b.c-a.c).slice(0,3);
 const lines=[
  (lang==='zh'?h+'日风险 ':h+'d risk ')+n.toFixed(1)+'/100 · '+riskBandText(n),
  (lang==='zh'?'这段风险主要由：':'Main contributors: ')+contributions.map(x=>(names[x.k]||x.k)+' '+x.s.toFixed(1)+'/100 × '+Math.round(x.w*100)+'%').join('；'),
  lang==='zh'?'怎么用：不同期限权重不同，所以不要拿20日和250日直接横向比较高低；要看哪个风险源在该期限占主导，再决定追踪短期价格、政策重定价还是长期结构。':'How to use: horizon weights differ, so do not compare 20d and 250d mechanically; identify which source dominates that horizon and monitor short-term price stress, policy repricing or long-run structure accordingly.'
 ];
 return lines.join(String.fromCharCode(10));
}
function setRiskScoreNode(id,score,suffix,label,baseClass=''){
 const node=el(id),n=Number(score);
 if(!node)return;
 const band=riskBandFromScore(n);
 node.textContent=Number.isFinite(n)?n.toFixed(1)+(suffix||''):'-';
 node.className=(baseClass?baseClass+' ':'')+'risk-number '+band+' has-tip';
 node.dataset.tip=riskScaleTip(n,label);
}
function riskCellHtml(text,score,label){
 const n=Number(score),band=riskBandFromScore(n);
 if(!Number.isFinite(n))return esc(text);
 return '<span class="risk-cell-number '+band+' has-tip" data-tip="'+esc(riskScaleTip(n,label))+'">'+esc(text)+'</span>';
}
function riskStageTip(stage){
 const s=String(stage||'-');
 const zh={
  WATCH_ONLY:'观察阶段：风险证据值得关注，但不改变生产权重。用途：持续盯驱动项和后验；如果风险继续上升或跨市场同步增强，再进入Shadow收紧测试。',
  NORMAL_OBSERVATION:'常规观察：当前没有需要额外风控动作的证据。用途：继续正常验证收益路线，只有风险驱动明显变化时才升级。',
  SHADOW_TIGHTEN_RISK_CONSTRAINTS:'Shadow收紧候选：实验性测试更低风险敞口。用途：比较收紧后是否减少尾部损失、是否牺牲过多收益；通过前瞻验证前不作用生产权重。',
  SHADOW_DEFENSIVE_BIAS:'Shadow防御偏置候选：实验性提高防御配置。用途：验证防御偏置是否在真实后验中改善风险收益；未验证前不作用生产权重。',
  DATA_UNAVAILABLE:'数据不足：当前无法形成完整风险状态。用途：先修复/补齐数据，不应把缺数据解释成低风险，也不应据此调整权重。'
 };
 const en={
  WATCH_ONLY:'Watch-only: evidence deserves attention but does not change production weights. Use: monitor drivers/posterior evidence and move to shadow tightening only if risk or cross-market synchronization strengthens.',
  NORMAL_OBSERVATION:'Normal observation: no evidence currently calls for extra risk action. Use: keep validating the return route and escalate only when drivers change materially.',
  SHADOW_TIGHTEN_RISK_CONSTRAINTS:'Shadow tightening candidate: test lower risky exposure. Use: compare whether tightening reduces tail loss without excessive return sacrifice; never affect production weights before prospective validation.',
  SHADOW_DEFENSIVE_BIAS:'Shadow defensive-bias candidate: test more defensive exposure. Use: validate whether the bias improves realized risk-adjusted outcomes before any production use.',
  DATA_UNAVAILABLE:'Data unavailable: a complete risk state cannot be formed. Use: repair/fill data first; missing data is neither low risk nor a reason to change weights.'
 };
 return (lang==='zh'?zh:en)[s]||((lang==='zh'?'风控实验阶段：':'Risk-control experiment stage: ')+s);
}
function riskStageClass(stage){
 const s=String(stage||'');
 if(s.includes('DEFENSIVE'))return 'risk-stage defensive';
 if(s.includes('TIGHTEN'))return 'risk-stage tighten';
 if(s.includes('WATCH'))return 'risk-stage watch';
 return 'risk-stage';
}
function fmtRiskNumber(x,digits=2){
 const n=Number(x);return Number.isFinite(n)?n.toFixed(digits):'-';
}
function renderRiskControl(report){
 if(!report)return;
 el('riskCenterScope').textContent=lang==='zh'
  ? '独立于下方美股 / A股 / 港股单市场页面：这里始终联合分析三个市场，并把结果送入风控 shadow 实验；它不是第四个市场，也不会自动改变生产权重。'
  : 'Independent of the US/CN/HK market selector below: this center always analyzes all three markets and feeds a shadow risk-control experiment. It is not a fourth market and cannot change production weights automatically.';
 const names={US:lang==='zh'?'美股 / US':'US',CN:lang==='zh'?'A股 / CN':'CN',HK:lang==='zh'?'港股 / HK':'HK'};
 el('riskThreeMarketTitle').textContent=lang==='zh'?'三市场联动状态':'Three-market linked state';
 el('riskThreeMarketRows').innerHTML=(report.three_market_state||[]).map(x=>{
   const ddScore=x.drawdown_stress_252==null?null:Math.min(100,Math.max(0,Number(x.drawdown_stress_252)*500));
   const momPct=x.negative_momentum_percentile==null?null:Math.min(100,Math.max(0,Number(x.negative_momentum_percentile)*100));
   const volPct=x.volatility_percentile==null?null:Math.min(100,Math.max(0,Number(x.volatility_percentile)*100));
   const stage=String(x.risk_control_stage||'-');
   return '<tr><td><b>'+esc(names[x.market]||x.market)+'</b></td>'+
    '<td>'+riskCellHtml(fmtPct(x.drawdown_stress_252),ddScore,lang==='zh'?'252日回撤压力（20%回撤映射为100分）':'252d drawdown pressure (20% drawdown maps to 100)')+'</td>'+
    '<td>'+riskCellHtml(fmtPct(x.negative_momentum_63),momPct,lang==='zh'?'63日负向动量异常程度':'63d negative-momentum abnormality')+'</td>'+
    '<td>'+riskCellHtml((x.negative_momentum_percentile==null?'-':fmtPct(x.negative_momentum_percentile)),momPct,lang==='zh'?'动量异常历史分位':'Momentum abnormality percentile')+'</td>'+
    '<td>'+riskCellHtml(fmtPct(x.volatility_63),volPct,lang==='zh'?'63日波动历史分位':'63d volatility percentile')+'</td>'+
    '<td class="'+riskStageClass(stage)+' has-tip" data-tip="'+esc(riskStageTip(stage))+'">'+esc(stage)+'</td></tr>';
 }).join('');
 el('riskDynamicsTitle').textContent=lang==='zh'?'动力链 / 传导路径':'Dynamics / transmission chain';
 el('riskDynamicsRows').innerHTML=(report.dynamics_chain||[]).map(x=>{
   const state=String(x.state||'-');
   const clsState=(state.includes('ACTIVE')||state.includes('STRESSED')||state.includes('DETERIORATING'))?'chain-active':(state.includes('NOT_CONFIRMED')||state.includes('PARTIAL'))?'chain-pending':'chain-normal';
   const score=Number(x.score);
   const normalized=Number.isFinite(score)?(score<=1?score*100:score):null;
   const shown=Number.isFinite(score)?score.toFixed(score<=1?2:1):'-';
   return '<tr><td>'+esc(lang==='zh'?(x.label_zh||x.id):x.id)+'</td><td class="'+clsState+'">'+esc(state)+'</td><td>'+riskCellHtml(shown,normalized,lang==='zh'?'动力链节点风险强度':'Dynamics-node risk intensity')+'</td><td>'+esc((x.evidence||[]).join(' · ')||'-')+'</td></tr>';
 }).join('');
 el('riskMacroTitle').textContent=lang==='zh'?'利率、政策、信用与流动性':'Rates, policy, credit and liquidity';
 const macro=report.rates_policy_credit_snapshot||{};
 const macroLabels={
  US_TREASURY_2Y_LEVEL:'2Y Treasury',US_TREASURY_10Y_LEVEL:'10Y Treasury',US_TREASURY_30Y_LEVEL:'30Y Treasury',
  US_REAL_YIELD_10Y_LEVEL:'10Y Real Yield',US_TREASURY_2Y_RISE_90D:'2Y Δ90d',US_TREASURY_10Y_RISE_90D:'10Y Δ90d',
  US_REAL_YIELD_10Y_RISE_90D:'10Y Real Δ90d',FED_POLICY_RATE_LEVEL:'Fed Funds',FED_FUNDS_FUTURES_IMPLIED_RATE:'Fed Funds Futures',
  FED_FUNDS_FUTURES_REPRICING_ABS_30D:'Fed Futures |Δ30d|',MOVE_LEVEL:'MOVE',MOVE_RISE_30D:'MOVE Δ30d',MOVE_RISE_90D:'MOVE Δ90d',
  YIELD_CURVE_INVERSION:'10Y−2Y inversion stress',YIELD_CURVE_10Y3M_INVERSION:'10Y−3M inversion stress',
  FINANCIAL_CONDITIONS_NFCI:'NFCI',HY_CREDIT_SPREAD_LEVEL:'HY OAS'
 };
 el('riskMacroRows').innerHTML=Object.entries(macro).filter(([k])=>!k.endsWith('_LONG_CYCLE')).map(([k,v])=>{
   const pct=v.point_in_time_percentile==null?null:Number(v.point_in_time_percentile)*100;
   return '<tr><td>'+esc(macroLabels[k]||k)+'</td><td>'+fmtRiskNumber(v.value,4)+'</td><td>'+riskCellHtml((v.point_in_time_percentile==null?'-':fmtPct(v.point_in_time_percentile)),pct,lang==='zh'?'该风险因子的历史状态分位':'Historical state percentile for this risk factor')+'</td></tr>';
 }).join('');
 el('riskTermTitle').textContent=lang==='zh'?'Fed Funds / SOFR 期限曲线':'Fed Funds / SOFR term curves';
 const tc=report.term_curve||{}, metrics=tc.metrics||{};
 const curveNames={fed_funds:'Fed Funds',sofr_1m:'SOFR 1M',sofr_3m:'SOFR 3M'};
 el('riskTermRows').innerHTML=Object.entries(curveNames).map(([k,label])=>{
  const x=metrics[k]||{};
  return '<tr><td>'+label+'</td><td>'+esc(x.contracts??'-')+'</td><td>'+fmtRiskNumber(x.front_implied_rate,4)+'%</td><td>'+fmtRiskNumber(x.back_implied_rate,4)+'%</td><td>'+fmtRiskNumber(x.front_to_back_change,4)+'pp</td></tr>';
 }).join('');
 el('riskCurveDetailTitle').textContent=lang==='zh'?'展开期限合约明细':'Show contract-level term curve';
 const curveRows=[];
 Object.entries(curveNames).forEach(([k,label])=>(tc[k]||[]).forEach(x=>curveRows.push({label,...x})));
 el('riskCurveContractRows').innerHTML=curveRows.map(x=>'<tr><td>'+esc(x.label)+'</td><td>'+esc(x.contract_month||'-')+'</td><td>'+esc(x.symbol||'-')+'</td><td>'+fmtRiskNumber(x.price,4)+'</td><td>'+fmtRiskNumber(x.implied_rate,4)+'%</td></tr>').join('');
 el('riskHistoryTableTitle').textContent=lang==='zh'?'历史危机回溯与统计支持':'Historical crash recurrence and statistical support';
 const hv=report.historical_validation||{};
 const supported=hv.statistically_supported_composites||[];
 el('riskHistoryRows').innerHTML=supported.map(x=>'<tr><td>'+esc(x.composite||'-')+'</td><td>'+esc(x.lead_trading_days??'-')+'d</td><td>'+fmtPct(x.event_hit_rate)+'</td><td>'+fmtPct(x.control_false_positive_rate)+'</td><td>'+fmtPct(x.hit_rate_lift)+'</td><td>'+fmtRiskNumber(x.fisher_p_value,4)+'</td><td>'+fmtRiskNumber(x.bh_q_value,4)+'</td><td>'+fmtPct(x.leave_one_event_out_min_hit_rate)+'</td></tr>').join('') || '<tr><td colspan="8">-</td></tr>';
 el('riskDataQualityTitle').textContent=lang==='zh'?'数据完整性与降级状态':'Data completeness and degradation';
 const dq=report.data_quality||{}, cov=dq.risk_evidence_coverage||{}, gaps=cov.known_gaps||[];
 el('riskDataQuality').textContent=(lang==='zh'?'历史证据覆盖等级 ':'Evidence coverage ')+(cov.coverage_grade||'-')+' · '+(lang==='zh'?'期限曲线 ':'term curve ')+(cov.term_curve_usable?'OK':'NOT READY')+' · '+(lang==='zh'?'港股高频 ':'HK high-frequency ')+(dq.hk_high_frequency_degraded?'DEGRADED':'OK')+' · '+(lang==='zh'?'正式日线证据受影响 ':'daily evidence affected ')+(dq.evidence_critical_daily_data_affected?'YES':'NO');
 const liveGapEntries=Object.entries(dq.hk_high_frequency_errors||{}).map(([k,v])=>({source:k,error:(v&&v.errors)?v.errors.join(' | '):String(v)}));
 const degradedPanelEntries=Object.entries(dq.hk_high_frequency_degraded_panels||{}).map(([k,v])=>({source:k,error:(v||[]).join(' | ')}));
 const allGaps=[...gaps,...liveGapEntries,...degradedPanelEntries];
 el('riskDataGaps').innerHTML=allGaps.length?allGaps.map(x=>'<li><b>'+esc(x.source||'-')+'</b> · '+esc(x.error||'-')+'</li>').join(''):'<li>'+(lang==='zh'?'当前未记录已知缺口':'No known gap recorded')+'</li>';
 el('riskControlTitle').textContent=lang==='zh'?'三市场风控 Shadow 实验':'Three-market risk-control shadow experiment';
 const rc=report.risk_control_experiment||{};
 el('riskControlMeta').textContent=(lang==='zh'?'当前阶段 ':'Stage ')+(rc.stage||'-')+' · '+(lang==='zh'?'生产动作 ':'Production action ')+(rc.production_action||'NONE')+' · '+(lang==='zh'?'目标纪律：风险只作为可执行约束/证据层，最大化可实现净收益仍是唯一优化目标。':rc.objective_guard||'');
 el('riskControlRows').innerHTML=(report.three_market_state||[]).map(x=>{
  const q=x.shadow_candidate_constraints||{},stage=String(x.risk_control_stage||'-');
  const stageScore=stage==='SHADOW_DEFENSIVE_BIAS'?80:stage==='SHADOW_TIGHTEN_RISK_CONSTRAINTS'?65:stage==='WATCH_ONLY'?45:stage==='NORMAL_OBSERVATION'?0:null;
  return '<tr><td>'+esc(names[x.market]||x.market)+'</td><td class="'+riskStageClass(stage)+' has-tip" data-tip="'+esc(riskStageTip(stage))+'">'+esc(stage)+'</td><td>'+riskCellHtml(fmtPct(q.risky_exposure_multiplier),stageScore,lang==='zh'?'该阶段的Shadow风险资产敞口倍率候选':'Shadow risky-exposure multiplier for this stage')+'</td><td>'+riskCellHtml(fmtPct(q.defensive_exposure_floor),stageScore,lang==='zh'?'该阶段的Shadow防御敞口底线候选':'Shadow defensive-floor candidate for this stage')+'</td><td>'+(x.applied_to_production?'YES':'NO')+'</td></tr>';
 }).join('');
 const pv=report.prospective_validation||{};
 el('riskLedgerDetail').textContent=(lang==='zh'?'风控实验账本 ':'Risk-control ledger ')+(pv.ledger_id||'-')+' · '+(lang==='zh'?'已结算 ':'resolved ')+((pv.resolved_horizons||[]).join('/')||'0')+' · '+(lang==='zh'?'待结算 ':'pending ')+((pv.pending_horizons||[]).join('/')||'-');
 applyTableHeaderTooltips();
}
function renderRiskWarning(report){
 const panel=el('riskWarningPanel');
 if(!report){
  panel.style.display='none';
  homeSummaryState.riskScore=null;
  homeSummaryState.riskBand=null;
  renderHomeSummary();
  return;
 }
 panel.style.display='block';
 const o=report.overall||{}, hs=report.horizon_estimates||{}, ss=report.subscores||{};
 const score=Number(o.risk_pressure_index);
 homeSummaryState.riskScore=Number.isFinite(score)?score:null;
 homeSummaryState.riskBand=String(o.risk_band||'');
 renderHomeSummary();
 const band=String(o.risk_band||'-');
 const bandText=lang==='zh'?(o.risk_band_zh||band):band;
 const bandClass=band.toLowerCase();
 el('riskWarningTitle').textContent=lang==='zh'?'TRIAID 风险预警':'TRIAID Risk Warning';
 el('riskColorLegend').innerHTML=lang==='zh'
  ? '<span>颜色阈值：</span><span class="risk-band low">低 0–24.9</span><span class="risk-band elevated">升高 25–44.9</span><span class="risk-band high">高 45–64.9</span><span class="risk-band severe">严重 65–79.9</span><span class="risk-band critical">临界 80–100</span>'
  : '<span>Color scale:</span><span class="risk-band low">LOW 0–24.9</span><span class="risk-band elevated">ELEVATED 25–44.9</span><span class="risk-band high">HIGH 45–64.9</span><span class="risk-band severe">SEVERE 65–79.9</span><span class="risk-band critical">CRITICAL 80–100</span>';
 el('riskWarningMeta').textContent=(lang==='zh'?'状态日期 ':'As of ')+(report.as_of||'-')+' · '+(lang==='zh'?'预警ID ':'Warning ')+(report.warning_id||'-');
 setRiskScoreNode('riskOverallScore',score,'',lang==='zh'?'当前综合风险':'Current overall risk','risk-score');
 const overallTip=riskOverallTip(score,report);
 el('riskOverallScore').dataset.tip=overallTip;
 el('riskOverallBand').textContent=bandText;
 el('riskOverallBand').className='risk-band '+bandClass+' has-tip';
 el('riskOverallBand').dataset.tip=overallTip;
 setRiskScoreNode('riskOverallMini',score,' / 100',lang==='zh'?'当前综合风险':'Current overall risk','rv');
 el('riskOverallMini').dataset.tip=overallTip;
 const conf=report.confidence||{};
 el('riskConfidence').textContent=(lang==='zh'?'证据置信度 ':'Evidence confidence ')+(conf.level||'-')+' · '+(lang==='zh'?'历史危机样本 ':'historical crises ')+(conf.historical_crisis_samples_tested??'-')+' · q='+(conf.best_supported_q_value==null?'-':Number(conf.best_supported_q_value).toFixed(4))+' · '+(lang==='zh'?'前瞻 ':'prospective ')+(conf.prospective_maturity||'-');
 ['20','60','120','250'].forEach(h=>{
   const x=hs[h]||{};
   const n=Number(x.risk_pressure_index);
   setRiskScoreNode('risk'+h,n,'/100',(lang==='zh'?h+'日风险压力':h+'d risk pressure'),'rv');
   el('risk'+h).dataset.tip=riskHorizonTip(h,x,report);
   const hBand=String(x.risk_band||riskBandText(n)).toLowerCase();
   el('risk'+h+'Band').textContent=lang==='zh'?(x.risk_band_zh||x.risk_band||riskBandText(n)):(x.risk_band||riskBandText(n));
   el('risk'+h+'Band').className='small risk-band-inline '+hBand;
 });
 const subMap={structural:'riskStructural',rates_policy:'riskRates',transmission:'riskTransmission',credit_liquidity:'riskCredit',market_deterioration:'riskMarket'};
 const subLabels={
  structural:lang==='zh'?'长期结构脆弱':'Structural vulnerability',
  rates_policy:lang==='zh'?'利率/政策压力':'Rates / policy pressure',
  transmission:lang==='zh'?'跨市场传导':'Cross-market transmission',
  credit_liquidity:lang==='zh'?'信用/流动性':'Credit / liquidity',
  market_deterioration:lang==='zh'?'价格结构恶化':'Market deterioration'
 };
 Object.entries(subMap).forEach(([k,id])=>{
   const n=Number((ss[k]||{}).score_0_100);
   setRiskScoreNode(id,n,'/100',subLabels[k],'');
   el(id).dataset.tip=riskSubscoreTip(k,n,report);
 });
 el('riskOverallLabel').textContent=lang==='zh'?'当前综合风险':'Current overall risk';
 el('riskStructuralLabel').textContent=lang==='zh'?'长期结构脆弱':'Structural vulnerability';
 el('riskRatesLabel').textContent=lang==='zh'?'利率/政策压力':'Rates / policy pressure';
 el('riskTransmissionLabel').textContent=lang==='zh'?'跨市场传导':'Cross-market transmission';
 el('riskCreditLabel').textContent=lang==='zh'?'信用/流动性':'Credit / liquidity';
 el('riskMarketLabel').textContent=lang==='zh'?'价格结构恶化':'Market deterioration';
 el('riskDriversTitle').textContent=lang==='zh'?'主要风险驱动':'Main risk drivers';
 el('riskBlockersTitle').textContent=lang==='zh'?'尚未确认 / 风险阻断项':'Missing confirmations / blockers';
 el('riskDetailsTitle').textContent=lang==='zh'?'展开完整证据与验证状态':'Show full evidence and validation';
 el('riskHistoricalTitle').textContent=lang==='zh'?'历史支持':'Historical support';
 el('riskProspectiveTitle').textContent=lang==='zh'?'前瞻验证':'Prospective validation';
 el('riskEscalationTitle').textContent=lang==='zh'?'升级条件':'Escalation conditions';
 el('riskDeescalationTitle').textContent=lang==='zh'?'降级条件':'De-escalation conditions';
 const drivers=report.main_drivers||[];
 el('riskDrivers').innerHTML=drivers.length?drivers.map(x=>'<li>'+esc(lang==='zh'?(x.label_zh||x.id):x.id)+' · '+(x.severity==null?'':Math.round(Number(x.severity)*100)+'/100')+'</li>').join(''):'<li>'+(lang==='zh'?'当前没有达到主驱动阈值的项目':'No driver currently exceeds the primary threshold')+'</li>';
 const blockers=report.missing_confirmations||[];
 el('riskBlockers').innerHTML=blockers.length?blockers.map(x=>'<li>'+esc(lang==='zh'?(x.label_zh||x.id):x.id)+'</li>').join(''):'<li>'+(lang==='zh'?'无明显阻断项':'No material blocker')+'</li>';
 const hist=report.historical_support||{}, best=hist.policy_repricing_best||{};
 el('riskHistorical').textContent=(lang==='zh'?'危机簇 ':'Crash clusters ')+(hist.historical_event_count??'-')+' · '+(lang==='zh'?'正常对照 ':'controls ')+(hist.normal_control_count??'-')+' · POLICY_REPRICING_STRESS '+(best.lead_trading_days==null?'':best.lead_trading_days+'d')+' hit '+(best.event_hit_rate==null?'-':fmtPct(best.event_hit_rate))+' / false+ '+(best.control_false_positive_rate==null?'-':fmtPct(best.control_false_positive_rate))+' / q '+(best.bh_q_value==null?'-':Number(best.bh_q_value).toFixed(4));
 const pv=report.prospective_validation||{};
 el('riskProspective').textContent=(lang==='zh'?'成熟度 ':'Maturity ')+(pv.maturity||'-')+' · '+(lang==='zh'?'已结算 ':'resolved ')+((pv.resolved_horizons||[]).join('/')||'0')+' · '+(lang==='zh'?'待结算 ':'pending ')+((pv.pending_horizons||[]).join('/')||'-')+' · '+(lang==='zh'?'证据有效 ':'eligible ')+(pv.evidence_eligible===false?'NO':'YES');
 el('riskEscalation').innerHTML=(report.escalation_conditions||[]).map(x=>'<li>'+esc(lang==='zh'?(x.meaning_zh||x.condition):x.condition)+'</li>').join('');
 el('riskDeescalation').innerHTML=(report.deescalation_conditions||[]).map(x=>'<li>'+esc(lang==='zh'?(x.meaning_zh||x.condition):x.condition)+'</li>').join('');
 const src=report.source_status||{};
 el('riskSourceStatus').textContent=(lang==='zh'?'数据时点：':'Source dates: ')+'Long '+(src.long_cycle_as_of||'-')+' · Hazard '+(src.latent_hazard_as_of||'-')+' · US/CN/HK '+(src.cross_market_as_of||'-')+' · Curve '+(src.policy_curve_as_of||'-')+' · Shadow '+(src.prospective_as_of||'-')+' · '+(lang==='zh'?'期限曲线 ':'term curve ')+(src.term_curve_usable?'OK':'NOT READY');
 el('riskSemantics').textContent=lang==='zh'
  ? '读法：先看主要风险驱动，再看尚未确认项，最后看20/60/120/250日哪一段被什么分项推高。只有驱动继续扩散且阻断项转为确认，风险证据才升级；驱动回落并满足降级条件时才解除。该层只提供Shadow风控证据，不直接修改生产权重。'
  : 'Read it in order: main drivers → missing confirmations → which components dominate 20/60/120/250d horizons. Evidence escalates only when drivers broaden and blockers become confirmed; it de-escalates when drivers normalize and de-escalation conditions are met. This layer supplies shadow risk evidence and does not directly change production weights.';
 applyTableHeaderTooltips();
}
function renderComparison(evaluated){
 const t=T[lang];
 if(!evaluated){
  el('baseReturn').textContent=t.noEval;el('triaidReturn').textContent=t.noEval;el('gain').textContent=t.noEval;
  el('gain').className='value';el('gainCard').style.background='#fff';return;
 }
 const e=evaluated.evaluation, gain=Number(e.excess_return||0);
 el('baseReturn').textContent=fmtPct(e.baseline_return);el('triaidReturn').textContent=fmtPct(e.triaid_return);
 el('gain').textContent=signedPct(gain);el('gain').className='value '+cls(gain);
 el('gainCard').style.background=gain>0?'#edf8f1':gain<0?'#fff1ef':'#fff';
}
function renderUSReturnMax(report){
 const panel=el('usReturnMaxPanel');
 if(!report){panel.className='prospective-panel';return;}
 panel.className='prospective-panel show';
 const d=report.latest_decision||{};
 const review=report.previous_decision_review||null;
 const integrity=report.integrity||{};
 el('usReturnMaxStatus').textContent=(d.decision_status||'-')+' · '+(integrity.passed?'HASH PASS':'HASH FAIL');
 el('usReturnMaxMeta').textContent=(d.decision_id||'-')+' · '+(lang==='zh'?'冻结 ':'Frozen ')+(d.frozen_at||'-')+' · '+(lang==='zh'?'排序信号：多周期年化状态收益估计':'Ranking signal: multi-window annualized state-return estimate');
 el('usrmExpected').textContent=fmtPct(d.projected_annualized_expected_net_return);
 el('usrmGeneric').textContent=fmtPct(d.generic_core_projected_annualized_expected_net_return);
 el('usrmSpy').textContent=fmtPct(d.buy_hold_projected_annualized_expected_net_return);
 el('usrmRisk').textContent=fmtPct(1-Number(d.cash_residual_weight||0));
 el('usReturnMaxNote').textContent=lang==='zh'
  ? '美股主路线不使用A股反转恢复逻辑，而是在所有 ACTIVE 策略中严格选择当前多周期年化状态收益估计最高者。该指标由21/63/126/252日已实现策略净收益按固定权重年化汇总，不等于标的未来涨跌预测。数值并列时依次用更低执行成本、风险、不确定性和固定策略ID打破平局，再展开成 SPY/QQQ/IWM/TLT/GLD 的目标头寸。四档美元资金规模共享同一冻结决策，只让资金规模改变模拟成交容量和冲击成本；系统不发送券商订单。'
  : 'The US route follows the same TRIAID FIN constitution as every supported market: maximize realizable net return as the only optimization objective. Liquidity, capacity, concentration and risk are feasibility constraints, while switching and execution costs are deducted as real costs. The return signal uses realized 21/63/126/252-day strategy net returns under frozen weights and is not an underlying-price forecast. Exact net-score ties use lower execution cost and deterministic strategy ID only. Four USD capital tiers apply the same objective with capital-specific capacity and impact checks, and no broker orders are sent.';
 const rw=d.target_strategy_weights||{};
 const gw=d.generic_core_control_weights||{};
 const sids=Array.from(new Set([...Object.keys(rw),...Object.keys(gw)])).sort();
 el('usrmStrategyRows').innerHTML=sids.map(sid=>'<tr><td>'+strategyLabelHtml(strategyNameIndex.US[sid]||sid,sid)+'</td><td class="num triaid">'+fmtPct(rw[sid]||0)+'</td><td class="num">'+fmtPct(gw[sid]||0)+'</td></tr>').join('') ||
  '<tr><td colspan="3">'+(lang==='zh'?'等待冻结策略':'Awaiting frozen strategy mix')+'</td></tr>';
 const aw=d.target_asset_weights||{};
 el('usrmAssetRows').innerHTML=Object.entries(aw).filter(([_,w])=>Number(w)>1e-12).sort((a,b)=>Number(b[1])-Number(a[1])).map(([a,w])=>'<tr><td>'+esc(a)+'</td><td class="num">'+fmtPct(w)+'</td></tr>').join('') ||
  '<tr><td colspan="2">'+(lang==='zh'?'当前无风险ETF敞口':'No current risky ETF exposure')+'</td></tr>';
 const cap=d.capital_capacity||{};
 el('usrmCapitalMeta').textContent=(lang==='zh'?'冻结执行参数：':'Frozen execution parameters: ')+'ADV20 · '+fmtPct(cap.max_participation_adv)+' cap · '+(cap.base_cost_bps??'-')+'bps base · '+(cap.impact_coefficient_bps??'-')+'bps×√participation';
 el('usrmCapitalRows').innerHTML=(cap.sleeves||[]).map(x=>'<tr><td class="num">'+fmtUsd(x.starting_capital_usd)+'</td><td class="num">'+fmtUsd(x.target_invested_notional_usd)+'</td><td class="num">'+fmtPct(x.max_one_day_participation_adv)+'</td><td class="num">'+esc(x.minimum_execution_days??'-')+'</td><td class="num">'+fmtUsd(x.estimated_round_trip_cost_proxy_usd)+'</td></tr>').join('') ||
  '<tr><td colspan="5">'+(lang==='zh'?'等待资金容量决策':'Awaiting capacity decision')+'</td></tr>';
 const rs=((review&&review.capital_sleeves)||{}).sleeves||[];
 el('usrmRealizedRows').innerHTML=rs.map(x=>'<tr><td class="num">'+fmtUsd(x.starting_capital_usd)+'</td><td class="num">'+fmtPct(x.fill_ratio)+'</td><td class="num">'+fmtUsd(x.current_equity_usd)+'</td><td class="num '+cls(Number(x.current_net_pnl_usd||0))+'">'+fmtUsd(x.current_net_pnl_usd)+'</td><td class="num '+cls(Number(x.current_net_return||0))+'">'+signedPct(x.current_net_return)+'</td><td class="num">'+fmtUsd(x.total_execution_cost_usd)+'</td></tr>').join('') ||
  '<tr><td colspan="6">'+(lang==='zh'?'上一轮尚无可用的后验模拟执行结果':'No eligible posterior simulated-execution result for the prior decision yet')+'</td></tr>';
 const path=(review&&review.daily_path)||[];
 el('usrmDailyRows').innerHTML=path.map(x=>'<tr><td class="nowrap">'+esc(x.as_of||'-')+'</td><td class="num '+cls(Number(x.return_max_cumulative_return||0))+'">'+fmtPct(x.return_max_cumulative_return)+'</td><td class="num '+cls(Number(x.generic_core_cumulative_return||0))+'">'+fmtPct(x.generic_core_cumulative_return)+'</td><td class="num '+cls(Number(x.spy_buy_hold_cumulative_return||0))+'">'+fmtPct(x.spy_buy_hold_cumulative_return)+'</td></tr>').join('') ||
  '<tr><td colspan="4">'+(lang==='zh'?'等待下一完整美股交易日结果':'Awaiting the next complete US trading-day outcome')+'</td></tr>';
}
function renderProspective(report){
 const panel=el('prospectivePanel');
 if(!report){panel.className='prospective-panel';return;}
 panel.className='prospective-panel show';
 const t=T[lang];
 el('prospectiveStatus').textContent=report.status||'-';
 el('prospectiveDays').textContent=String(report.observation_days??0)+' / '+((report.horizons_trading_days||[]).join('/')||'-');
 const p=report.current_portfolio_cumulative_returns||{};
 el('prospectiveHold').textContent=fmtPct(p.HOLD_EQUAL);el('prospectiveHold').className=cls(Number(p.HOLD_EQUAL||0));
 el('prospectiveTriaid').textContent=fmtPct(p.TRIAID_STATIC_ALLOCATION);el('prospectiveTriaid').className=cls(Number(p.TRIAID_STATIC_ALLOCATION||0));
 el('prospectiveGap').textContent=signedPct(p.TRIAID_STATIC_MINUS_HOLD_EQUAL);el('prospectiveGap').className=cls(Number(p.TRIAID_STATIC_MINUS_HOLD_EQUAL||0));
 el('prospectiveMeta').textContent=(report.experiment_id||'-')+' · '+(lang==='zh'?'登记日 ':'Registered ')+(report.market_as_of||'-')+' · '+(lang==='zh'?'待完成窗口 ':'Pending horizons ')+((report.pending_horizons||[]).join('/')||'none');
 el('prospectiveNote').textContent=lang==='zh'
   ? '策略池与TRIAID综合排序在登记时冻结，禁止事后换成员或调参。累计收益来自后续真实策略收益；全现金只作为防守对照，不作为恢复排序能力的主要证据。'
   : 'Pool membership and the TRIAID composite ranking are frozen at registration with no post-result retuning. Cumulative returns use subsequent realized strategy returns; cash is a defense control, not the primary evidence of recovery-ranking skill.';
 const rows=report.strategy_determination||[];
 el('prospectiveStrategyRows').innerHTML=rows.map(x=>{
   const name=(x.name&&x.name[lang])||x.strategy_id;
   const reason=(x.selection_reason&&x.selection_reason[lang])||'';
   return '<tr>'+
    '<td>'+strategyLabelHtml(name,x.strategy_id)+'<br><span class="small muted">'+esc(x.strategy_id)+'</span></td>'+
    '<td class="num">'+(x.triaid_predicted_rank??'-')+'</td>'+
    '<td class="num base">'+fmtPct(x.baseline_weight)+'</td>'+
    '<td class="num triaid">'+fmtPct(x.triaid_weight)+'</td>'+
    '<td class="num '+cls(Number(x.latest_daily_return||0))+'">'+fmtPct(x.latest_daily_return)+'</td>'+
    '<td class="num '+cls(Number(x.realized_cumulative_return||0))+'">'+fmtPct(x.realized_cumulative_return)+'</td>'+
    '<td class="num">'+(x.realized_rank_so_far??'-')+'</td>'+
    '<td class="reason">'+esc(reason)+'</td></tr>';
 }).join('');
 const pool=rows.map(x=>x.strategy_id);
 el('prospectiveDailyHead').innerHTML='<tr><th>'+(lang==='zh'?'交易日':'Trading day')+'</th>'+
   pool.map(sid=>'<th>'+strategyLabelHtml(strategyNameIndex.CN[sid]||sid,sid)+'</th>').join('')+
   '<th>'+(lang==='zh'?'最差池':'Worst pool')+'</th><th>TRIAID</th><th>'+(lang==='zh'?'差值':'Gap')+'</th></tr>';
 const daily=report.daily_fluctuation||[];
 el('prospectiveDailyRows').innerHTML=daily.map(day=>{
   const cr=day.portfolio_cumulative_returns||{};
   return '<tr><td class="nowrap">'+esc(day.as_of||'-')+'</td>'+
    pool.map(sid=>'<td class="num '+cls(Number((day.strategy_returns||{})[sid]||0))+'">'+signedPct((day.strategy_returns||{})[sid])+'</td>').join('')+
    '<td class="num '+cls(Number(cr.HOLD_EQUAL||0))+'">'+fmtPct(cr.HOLD_EQUAL)+'</td>'+
    '<td class="num '+cls(Number(cr.TRIAID_STATIC_ALLOCATION||0))+'">'+fmtPct(cr.TRIAID_STATIC_ALLOCATION)+'</td>'+
    '<td class="num '+cls(Number(cr.TRIAID_STATIC_MINUS_HOLD_EQUAL||0))+'">'+signedPct(cr.TRIAID_STATIC_MINUS_HOLD_EQUAL)+'</td></tr>';
 }).join('') || '<tr><td colspan="'+(pool.length+4)+'">'+(lang==='zh'?'等待首个后续真实交易日结果':'Awaiting the first subsequent realized trading-day result')+'</td></tr>';
}
function renderRecoveryWave(report){
 const panel=el('recoveryWavePanel');
 if(!report){panel.className='prospective-panel';return;}
 panel.className='prospective-panel show';
 const d=report.latest_decision||{};
 const review=report.previous_decision_review||null;
 const integrity=report.integrity||{};
 const labels={
  '510300.SS':'沪深300ETF · 510300',
  '510500.SS':'中证500ETF · 510500',
  '159915.SZ':'创业板ETF · 159915',
  '512100.SS':'中证1000ETF · 512100'
 };
 el('recoveryWaveStatus').textContent=(d.decision_status||'-')+' · '+(integrity.passed?'HASH PASS':'HASH FAIL');
 el('recoveryWaveMeta').textContent=(d.decision_id||'-')+' · '+(lang==='zh'?'冻结 ':'Frozen ')+(d.frozen_at||'-')+' · '+(lang==='zh'?'源数据时间 ':'Source data time ')+localDateTimeFromEpoch(d.source_latest_ts);
 el('recoveryCash').textContent=fmtPct(d.cash_residual_weight);
 el('recoveryPrevDays').textContent=review?String(review.observation_days??0):'-';
 el('recoveryPrevReturn').textContent=review?fmtPct(review.current_portfolio_cumulative_return):'-';
 el('recoveryPrevReturn').className=review?cls(Number(review.current_portfolio_cumulative_return||0)):'';
 el('recoveryPrevGap').textContent=review?signedPct(review.current_excess_vs_equal_weight):'-';
 el('recoveryPrevGap').className=review?cls(Number(review.current_excess_vs_equal_weight||0)):'';
 const scope=d.data_scope||{};
 el('recoveryWaveNote').textContent=lang==='zh'
  ? '这是 Shadow Core 的冻结研究配置，不生成券商订单。T 时点决策只能从下一完整可交易 bar 起计算后验；当前微观层仅使用 ETF/指数基金自身真实价格与成交量，成分股级微观数据尚未接入。四档资金袖套从同一决策、全现金起步，唯一变量是资金规模；所谓填单是依据后续真实成交量与冻结参与率进行的模拟成交，不代表券商真实成交，模拟成交后从下一完整 bar 才开始计收益。历史配置与参数均冻结，不能事后改写。'
  : 'These are frozen Shadow Core research allocations and generate no broker orders. A decision at T is evaluated only from the next complete tradable bar. The four capital sleeves start from cash under the same frozen decision; capital size is the only experimental variable. Fills are simulated from subsequently observed real volume under the frozen participation rule and become return-active on the following complete bar; they are not broker fills. Frozen allocations and parameters cannot be rewritten after outcomes.';
 const opinions=d.trade_opinions||[];
 el('recoveryOpinionRows').innerHTML=opinions.map(x=>{
   const horizon=x.expected_reversal_horizon_days==null?'-':(x.expected_reversal_horizon_days+(lang==='zh'?'日':'d'));
   const hit=x.historical_recovery_edge==null?'-':signedPct(x.historical_recovery_edge);
   const exp=x.expected_forward_return==null?'-':signedPct(x.expected_forward_return);
   const rationale=lang==='zh'?x.rationale_zh:x.rationale_en;
   return '<tr>'+
    '<td><span class="strategy-name">'+esc(labels[x.symbol]||x.symbol)+'</span><br><span class="small muted">'+esc(x.symbol)+'</span></td>'+
    '<td><span class="tag">'+esc(x.action||'-')+'</span></td>'+
    '<td class="num triaid">'+fmtPct(x.target_weight)+'</td>'+
    '<td class="num '+cls(Number(x.suggested_weight_change||0))+'">'+signedPct(x.suggested_weight_change)+'</td>'+
    '<td class="num '+cls(Number(x.drawdown_252||0))+'">'+fmtPct(x.drawdown_252)+'</td>'+
    '<td>'+esc(x.state_direction||'-')+'</td>'+
    '<td class="num">'+horizon+'</td>'+
    '<td class="num '+cls(Number(x.expected_recovery_velocity_per_day||0))+'">'+(x.expected_recovery_velocity_per_day==null?'-':signedPct(x.expected_recovery_velocity_per_day))+'</td>'+
    '<td class="num '+cls(Number(x.historical_recovery_edge||0))+'">'+hit+'</td>'+
    '<td class="num '+cls(Number(x.expected_forward_return||0))+'">'+exp+'</td>'+
    '<td class="num has-tip" data-tip="'+esc((rationale||'')+'\\n'+(lang==='zh'?'怎么用：先看相似样本数，再看这些样本是否跨多个时期、方向是否一致。样本稀疏或只由单一历史情形支撑时，把 expected forward return 视为未充分支持，并保持冻结权重不变，直到新增前瞻样本或稳健性证据补足。':'Use: the sample count and rationale show whether historical analogues are strong enough to support the current research opinion. Next: with sparse samples or one-scenario support, keep observing rather than scaling from a high return proxy alone.'))+'">'+esc(x.analog_samples??'-')+'</td></tr>';
 }).join('') || '<tr><td colspan="11">'+(lang==='zh'?'暂无冻结研究配置意见':'No frozen research allocation opinion')+'</td></tr>';
 const capacity=d.capital_capacity||{};
 const capModel=capacity.model||{};
 const capRows=capacity.sleeves||[];
 el('capitalSleeveMeta').textContent=capacity.enabled
   ? ((lang==='zh'?'冻结执行参数：':'Frozen execution parameters: ')+
      'ADV20 · '+fmtPct(capModel.max_participation_adv)+' cap · '+
      (capModel.base_cost_bps??'-')+'bps base · '+
      (capModel.impact_coefficient_bps??'-')+'bps×√participation · '+
      (lang==='zh'?'不含券商个性化费率':'broker-specific fees excluded'))
   : (lang==='zh'?'当前没有启用人民币资金袖套':'CNY capital sleeves not enabled');
 el('capitalSleeveRows').innerHTML=capRows.map(x=>{
   return '<tr>'+
    '<td class="num">'+fmtMoney(x.starting_capital_cny)+'</td>'+
    '<td class="num">'+fmtMoney(x.target_invested_notional_cny)+'</td>'+
    '<td class="num">'+fmtPct(x.max_one_day_participation_adv)+'</td>'+
    '<td class="num">'+esc(x.minimum_execution_days??'-')+'</td>'+
    '<td class="num">'+fmtMoney(x.estimated_round_trip_cost_proxy_cny)+'</td>'+
    '<td class="num '+cls(Number(x.expected_wave_net_pnl_before_timing_delay_cny||0))+'">'+fmtMoney(x.expected_wave_net_pnl_before_timing_delay_cny)+'</td>'+
    '<td class="num '+cls(Number(x.expected_wave_net_return_before_timing_delay||0))+'">'+signedPct(x.expected_wave_net_return_before_timing_delay)+'</td></tr>';
 }).join('') || '<tr><td colspan="7">'+(lang==='zh'?'等待当前资金容量决策':'Awaiting capital-capacity decision')+'</td></tr>';
 const realizedSleeves=((review&&review.capital_sleeves)||{}).sleeves||[];
 el('capitalRealizedRows').innerHTML=realizedSleeves.map(x=>{
   return '<tr>'+
    '<td class="num">'+fmtMoney(x.starting_capital_cny)+'</td>'+
    '<td class="num">'+fmtPct(x.fill_ratio)+'</td>'+
    '<td class="num">'+fmtMoney(x.current_equity_cny)+'</td>'+
    '<td class="num '+cls(Number(x.current_net_pnl_cny||0))+'">'+fmtMoney(x.current_net_pnl_cny)+'</td>'+
    '<td class="num '+cls(Number(x.current_net_return||0))+'">'+signedPct(x.current_net_return)+'</td>'+
    '<td class="num">'+fmtMoney(x.total_execution_cost_cny)+'</td>'+
    '<td class="num">'+fmtMoney(x.remaining_target_notional_cny)+'</td></tr>';
 }).join('') || '<tr><td colspan="7">'+(lang==='zh'?'上一轮资金袖套尚无可用的后验模拟执行结果':'No eligible posterior simulated-execution result for the prior sleeves yet')+'</td></tr>';
 if(review){
   const prior=review.trade_opinions||[];
   el('recoveryPreviousMeta').textContent=(lang==='zh'?'上一轮 '+(review.decision_id||'-')+'：':'Prior '+(review.decision_id||'-')+': ')+
     prior.map(x=>(labels[x.symbol]||x.symbol)+' '+(x.action||'-')+' '+fmtPct(x.target_weight)+' · '+(x.expected_reversal_horizon_days==null?'-':x.expected_reversal_horizon_days+(lang==='zh'?'日':'d'))).join(' | ');
 }else{
   el('recoveryPreviousMeta').textContent=lang==='zh'?'暂无上一轮冻结决策':'No prior frozen decision';
 }
 const path=(review&&review.daily_path)||[];
 el('recoveryReviewRows').innerHTML=path.map(x=>{
   return '<tr><td class="nowrap">'+esc(x.as_of||'-')+'</td>'+
    '<td class="num '+cls(Number(x.portfolio_return||0))+'">'+signedPct(x.portfolio_return)+'</td>'+
    '<td class="num '+cls(Number(x.equal_weight_return||0))+'">'+signedPct(x.equal_weight_return)+'</td>'+
    '<td class="num '+cls(Number(x.portfolio_cumulative_return||0))+'">'+fmtPct(x.portfolio_cumulative_return)+'</td>'+
    '<td class="num '+cls(Number(x.excess_vs_equal_weight||0))+'">'+signedPct(x.excess_vs_equal_weight)+'</td></tr>';
 }).join('') || '<tr><td colspan="5">'+(lang==='zh'?'上一轮尚未产生可用的下一完整交易日结果':'The prior decision has no eligible next-complete-bar outcome yet')+'</td></tr>';
}
async function refreshAll(preferStale=false){
 const m=el('market').value;
 const seq=++refreshSeq;
 const previewId=previewRunIds[m];
 try{
  const cardsUrl='/api/strategies?market_id='+m+'&lang='+lang+(previewId?'&run_id='+encodeURIComponent(previewId):'');
  const marketGet=preferStale?jsonCachedStale:jsonCached;
  const [s,d,cards,curves,evo,runs,previewRun]=await Promise.all([
   jsonCachedStale('/api/ui/core',60000),marketGet('/api/daily?compact=true&market_id='+m,30000),marketGet(cardsUrl,30000),
   marketGet('/api/curves?market_id='+m,30000),jsonCachedStale('/api/evolution',30000),marketGet('/api/runs?market_id='+m+'&limit=100',30000),
   previewId?json('/api/runs/'+encodeURIComponent(previewId)):Promise.resolve(null)
  ]);
  if(seq!==refreshSeq||el('market').value!==m)return;
  const isCN=m==='CN';
  const isHK=m==='HK';
  const primaryMode=isCN?'CN_RETURN_MAX_CAPACITY':isHK?'HK_RETURN_MAX_CAPACITY':null;
  const evaluated=[...runs].reverse().find(x=>
   x.evaluation&&x.evaluation.status==='EVALUATED'&&(!primaryMode||x.experiment_mode===primaryMode)
  )||null;
  if(isCN){
   el('resultTitle').textContent=lang==='zh'?'A股收益最大化主路线':'CN Return-Max Primary Route';
   el('baseReturnLabel').textContent=lang==='zh'?'收益优先策略群后验收益':'Return-first portfolio posterior return';
   el('baseReturnSub').textContent=lang==='zh'?'决策时冻结的全策略竞争基线':'Full-universe return-first baseline frozen at decision time';
   el('gainLabel').textContent=lang==='zh'?'TRIAID 相对基线收益差':'TRIAID return gap vs baseline';
   el('gainSub').textContent=lang==='zh'?'TRIAID 动态权重后验收益 − 收益优先基线':'TRIAID dynamic-allocation posterior return − return-first baseline';
   el('strategyTitle').textContent=lang==='zh'?'A股收益优先策略群与动态权重':'CN Return-First Strategy Group and Dynamic Weights';
  }else{
   el('resultTitle').textContent=T[lang].result;
   el('baseReturnLabel').textContent=T[lang].baseReturn;
   el('baseReturnSub').textContent=T[lang].baseSub;
   el('gainLabel').textContent=T[lang].gain;
   el('gainSub').textContent=T[lang].gainSub;
   el('strategyTitle').textContent=m==='US'
    ? (lang==='zh'?'通用 Core 对照策略群（非 Return-Max 主路线）':'Generic Core Control Group (not the Return-Max primary route)')
    : m==='HK'
      ? (lang==='zh'?'港股收益优先策略群与 TRIAID 动态权重':'HK Return-First Strategy Group and TRIAID Dynamic Weights')
      : T[lang].strategies;
  }
  strategyNameIndex[m]=Object.fromEntries(cards.map(x=>[x.strategy_id,x.name]));
  const selected=cards.filter(x=>x.selected);
  const detailRuns=d.runs_detail||[];
  const officialLatest=primaryMode
    ? ([...detailRuns].reverse().find(x=>x.experiment_mode===primaryMode)||null)
    : (detailRuns.length?detailRuns[detailRuns.length-1]:null);
  const latest=previewRun||officialLatest;
  const lastCurve=curves.length?curves[curves.length-1]:null;
  homeSummaryState.selectedCount=selected.length;
  homeSummaryState.changedCount=selected.filter(x=>Math.abs(Number(x.triaid_weight||0)-Number(x.baseline_weight||0))>1e-8).length;
  homeSummaryState.selectedNames=selected.slice().sort((a,b)=>Number(b.triaid_weight||0)-Number(a.triaid_weight||0)).map(x=>x.name||x.strategy_id);
  homeSummaryState.evaluated=evaluated;
  homeSummaryState.preview=!!previewRun;
  homeSummaryState.latestStatus=latest?.status||null;
  renderHomeSummary();
  renderComparison(evaluated);
  el('date').textContent=(previewRun?.market?.as_of)||d.date||'-';
  el('core').textContent=(previewRun?.triaid_decision?.core_version)||s.version;
  el('selectedCount').textContent=selected.length;
  const cum=lastCurve?lastCurve.cumulative_excess_return:null;el('cumExcess').textContent=fmtPct(cum);el('cumExcess').className='value '+cls(cum||0);
  el('regime').textContent=previewRun?.market?.regime||latest?.regime||'-';
  el('runState').textContent=previewRun
    ? ((previewRun.status||'-')+' · '+(lang==='zh'?'不进入证据链':'non-evidence'))
    : (latest?.status||'-');
  el('selectedNames').innerHTML=selected.length?selected.slice(0,6).map(x=>strategyLabelHtml(x.name,x.strategy_id)).join(lang==='zh'?'、':' · ')+(selected.length>6?' …':''):'-';
  if(previewRun){
   el('dailyAnalysis').textContent=T[lang].preview;
   el('dailyAnalysis').className='';
  }else if(evaluated){
   const g=Number(evaluated.evaluation.excess_return||0);
   el('dailyAnalysis').textContent=g>1e-12?T[lang].positive:g<-1e-12?T[lang].negativeResult:T[lang].flat;
   el('dailyAnalysis').className=cls(g);
  }else if(isCN&&latest?.diagnostic_summary?.experiment_mode==='CN_RETURN_MAX_CAPACITY'){
   const p=Number(latest.diagnostic_summary.projected_excess_expected_return);
   el('dailyAnalysis').textContent=Number.isFinite(p)
    ? (lang==='zh'?'当前主路线以可实现净收益为唯一优化目标；TRIAID 相对冻结基线的状态收益差为 '+signedPct(p)+'。该值用于决策排序，不是保证的未来收益。':'The primary route uses realizable net return as the sole optimization objective; the state-return gap versus the frozen baseline is '+signedPct(p)+'. This is a decision-ranking signal, not a guaranteed future return.')
    : T[lang].pending;
   el('dailyAnalysis').className=Number.isFinite(p)?cls(p):'';
  }else{el('dailyAnalysis').textContent=T[lang].pending;el('dailyAnalysis').className='';}
  renderUSReturnMax(m==='US'?d.us_return_max:null);
  renderProspective(isCN?d.prospective_experiment:null);
  renderRecoveryWave(isCN?d.recovery_wave:null);
  drawCurve(curves);
  const selectedCards=cards.filter(x=>x.selected).sort((a,b)=>(b.baseline_weight||0)-(a.baseline_weight||0));
  const candidateCards=cards.filter(x=>!x.selected).sort((a,b)=>((b.expected_net_return??-999)-(a.expected_net_return??-999)));
  el('strategyRows').innerHTML=selectedCards.map(x=>{
   const delta=(x.triaid_weight||0)-(x.baseline_weight||0);
   const explanation=[x.summary,x.selection_reason,x.triaid_reason].filter(Boolean).join(' · ');
   return '<tr class="selected">'+
    '<td>'+strategyLabelHtml(x.name,x.strategy_id)+'<br><span class="small muted">'+esc(x.strategy_id)+'</span></td>'+
    '<td><span class="tag has-tip" data-tip="'+esc(statusTip(x.lifecycle))+'">'+esc(x.lifecycle||'-')+'</span></td>'+
    '<td class="num '+cls(x.expected_net_return||0)+'">'+fmtPct(x.expected_net_return)+'</td>'+
    '<td class="num">'+fmtPct(x.risk)+'</td>'+
    '<td class="num base">'+fmtPct(x.baseline_weight)+'</td>'+
    '<td class="num triaid">'+fmtPct(x.triaid_weight)+'</td>'+
    '<td class="num delta '+cls(delta)+'">'+signedPct(delta)+'</td>'+
    '<td class="reason">'+esc(explanation)+'</td></tr>';
  }).join('');
  el('candidateRows').innerHTML=candidateCards.map(x=>{
   return '<tr>'+
    '<td>'+strategyLabelHtml(x.name,x.strategy_id)+'<br><span class="small muted">'+esc(x.strategy_id)+'</span></td>'+
    '<td><span class="tag has-tip" data-tip="'+esc(statusTip(x.lifecycle))+'">'+esc(x.lifecycle||'-')+'</span></td>'+
    '<td class="num '+cls(x.expected_net_return||0)+'">'+fmtPct(x.expected_net_return)+'</td>'+
    '<td class="num">'+fmtPct(x.risk)+'</td>'+
    '<td class="reason">'+esc([x.summary,x.best_conditions].filter(Boolean).join(' · '))+'</td></tr>';
  }).join('');
  const diag=evo.diagnosis||{};el('evoObserved').textContent=diag.evaluated_runs??0;
  el('evoMean').textContent=diag.mean_excess_return===null||diag.mean_excess_return===undefined?T[lang].noResult:
    (lang==='zh'?'平均相对收益差 ':'Mean relative return gap ')+signedPct(diag.mean_excess_return);
  el('evoMean').className='sub '+cls(diag.mean_excess_return||0);
  el('evoNeg').textContent=diag.negative_rate===null||diag.negative_rate===undefined?'-':fmtPct(diag.negative_rate);
  const history=evo.history||[];const last=history.length?history[history.length-1]:null;
  el('evoLast').textContent=last?(last.event+' · '+(last.version||'')):T[lang].noCandidate;
  el('runStatus').textContent=previewRun
    ? ((lang==='zh'?'即时预览 · 不进入证据链 · ':'Manual preview · non-evidence · ')+previewRun.run_id)
    : (latest?((latest.market_id||m)+' · '+(latest.status||'')):'Ready');
 }catch(e){el('runStatus').textContent='UI data error: '+e.message;}
}
async function refreshRiskPanels(){
 const [riskWarning,riskControl]=await Promise.all([
  jsonOrNullCached('/api/risk-warning/latest',10000),
  jsonOrNullCached('/api/risk-control/latest',10000)
 ]);
 renderRiskWarning(riskWarning);
 renderRiskControl(riskControl);
}
async function propose(){
 const x=await json('/api/evolution/propose',{method:'POST'});
 el('runStatus').textContent=x.created?(x.candidate.version+' · CANDIDATE CREATED'):(x.reason||'NO CANDIDATE');
 refreshAll();
}
const hoverTip=el('hoverTip');
document.addEventListener('mouseover',e=>{
 const target=e.target.closest('[data-tip],[data-strategy-id]');
 if(!target)return;
 const tip=target.dataset.strategyId
  ? strategyMarketTip(target.dataset.strategyId,target.dataset.strategyName||strategyNameIndex[el('market').value][target.dataset.strategyId])
  : target.dataset.tip;
 if(!tip)return;
 hoverTip.textContent=tip;hoverTip.style.display='block';
});
document.addEventListener('mousemove',e=>{
 if(hoverTip.style.display!=='block')return;
 const pad=14;let x=e.clientX+14,y=e.clientY+16;
 const w=hoverTip.offsetWidth,h=hoverTip.offsetHeight;
 if(x+w>window.innerWidth-pad)x=e.clientX-w-14;
 if(y+h>window.innerHeight-pad)y=e.clientY-h-14;
 hoverTip.style.left=Math.max(pad,x)+'px';hoverTip.style.top=Math.max(pad,y)+'px';
});
document.addEventListener('mouseout',e=>{
 const target=e.target.closest('[data-tip],[data-strategy-id]');
 if(target&&!target.contains(e.relatedTarget))hoverTip.style.display='none';
});
const tableHeaderObserver=new MutationObserver(mutations=>{
 if(mutations.some(m=>m.type==='childList'||m.type==='characterData'))applyTableHeaderTooltips();
});
tableHeaderObserver.observe(document.body,{subtree:true,childList:true,characterData:true});
function toggleLang(){lang=lang==='zh'?'en':'zh';applyText();applyMarketScope();renderMarketIdentity();tickMarketClocks();refreshAll();refreshLiveWindows();refreshRiskPanels()}
applyText();applyMarketScope();renderMarketIdentity();refreshMarketClocks();tickMarketClocks();refreshAll();refreshLiveWindows();refreshRiskPanels();setTimeout(warmAllMarkets,1200);setInterval(tickMarketClocks,1000);setInterval(refreshMarketClocks,15000);setInterval(refreshAll,15000);setInterval(refreshLiveWindows,5000);setInterval(refreshRiskPanels,10000);
</script>
</body>
</html>
"""
