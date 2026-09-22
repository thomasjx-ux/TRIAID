from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from triaid_fin.contracts import OutcomeRequest, RunRequest
from triaid_fin.engine import EvolutionLabEngine
from triaid_fin.decision_api import build_decision_router
from triaid_fin.decision_scheduler import DecisionScheduler
from triaid_fin.market_api import build_market_data_router
from triaid_fin.market_runtime import MarketDataAutomation
from triaid_fin.trading_calendar import VERSION as TRADING_CALENDAR_VERSION
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
            },ensure_ascii=False,sort_keys=True),
        )
    except Exception as exc:
        print("TRIAID_LATENT_HAZARD_BACKGROUND_FAILED",f"{type(exc).__name__}:{exc}")

@asynccontextmanager
async def lifespan(app:FastAPI):
    tasks=[]
    startup_maintenance_enabled=os.getenv(
        "TRIAID_STARTUP_MAINTENANCE","0"
    ).lower() in {"1","true","on","yes"}
    if startup_maintenance_enabled:
        receipt=engine.recover_stale_runs()
        primary_references={}
        for market_id in ("US","CN"):
            try:
                primary_references[market_id]=engine.ensure_primary_reference(market_id)
            except Exception as exc:
                primary_references[market_id]={
                    "market_id":market_id,
                    "created":False,
                    "reason":"PRIMARY_REFERENCE_BOOTSTRAP_ERROR",
                    "error":f"{type(exc).__name__}:{exc}",
                }
        receipt["primary_references"]=primary_references
        app.state.startup_maintenance_receipt=receipt
    else:
        app.state.startup_maintenance_receipt={
            "event":"STALE_RUN_RECOVERY",
            "applied":False,
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
    source_revision=(
        os.getenv("TRIAID_DEPLOY_REVISION","").strip()
        or os.getenv("TRIAID_DEPLOY_REV","").strip()
        or None
    )
    runtime_revision=os.getenv("TRIAID_V2_REV","").strip() or None
    return {
        "source_revision":source_revision,
        "runtime_revision":runtime_revision,
    }


app=FastAPI(
    title="TRIAID FIN Evolution Lab V2",
    version=engine.architecture_version.split("@",1)[-1],
    lifespan=lifespan,
)
app.include_router(build_market_data_router(engine,market_automation,calendar_sync))
app.include_router(build_decision_router(decision_scheduler))


@app.get("/health")
def health()->dict:
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


@app.get("/api/status")
def status()->dict:
    return {
        **engine.status(),
        "deployment":deployment_identity(),
    }


@app.get("/api/storage/status")
def storage_status()->dict:
    return engine.store.status()


@app.post("/api/live/run/{market_id}", status_code=202)
def live_run(market_id: str, background_tasks: BackgroundTasks) -> dict:
    market_id = market_id.upper()
    if market_id not in {"US", "CN"}:
        raise HTTPException(status_code=400, detail="market_id must be US or CN")
    run = engine.create_pending_live_run(market_id,"MANUAL_PREVIEW")
    background_tasks.add_task(engine.execute_live, run.run_id, market_id, "MANUAL_PREVIEW")
    return {
        "run_id": run.run_id,
        "status": run.status,
        "market_id": market_id,
        "run_scope":"MANUAL_PREVIEW",
        "evidence_eligible":False,
    }


@app.post("/api/live/run-all", status_code=202)
def live_run_all(background_tasks: BackgroundTasks) -> dict:
    runs = []
    for market_id in ("US", "CN"):
        run = engine.create_pending_live_run(market_id,"MANUAL_PREVIEW")
        background_tasks.add_task(engine.execute_live, run.run_id, market_id, "MANUAL_PREVIEW")
        runs.append({
            "run_id": run.run_id,
            "status": run.status,
            "market_id": market_id,
            "run_scope":"MANUAL_PREVIEW",
            "evidence_eligible":False,
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
def daily(market_id: str | None = None) -> dict:
    return engine.daily_summary(market_id)


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
        latest_run=engine.latest_decision_run(market_id)
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
    market_id=market_id.upper()
    if market_id not in {"US","CN"}:
        raise HTTPException(status_code=400, detail="market_id must be US or CN")
    return engine.propose_strategy_candidate(market_id)


@app.post("/api/strategy-evolution/promote/{market_id}/{version}")
def strategy_evolution_promote(
    market_id: str,
    version: str,
    validation: dict[str, Any] = Body(default={}),
    _admin:None=Depends(require_admin_token),
) -> dict:
    market_id=market_id.upper()
    if market_id not in {"US","CN"}:
        raise HTTPException(status_code=400, detail="market_id must be US or CN")
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
#hoverTip{position:fixed;display:none;z-index:9999;max-width:430px;padding:9px 11px;border-radius:8px;background:#172033;color:#fff;font-size:12px;line-height:1.5;white-space:pre-line;box-shadow:0 8px 24px rgba(0,0,0,.18);pointer-events:none}
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
      <select id="market" onchange="onMarketChange()"><option value="US">US</option><option value="CN">A股 / CN</option></select>
      <button class="primary" onclick="runNow()" id="runBtn">立即执行</button>
      <button onclick="runAll()" id="runAllBtn">执行两个市场</button>
      <button onclick="toggleLang()">中文 / English</button>
    </div>
  </div>
  <div class="statusline" id="runStatus">Ready</div>

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
let strategyNameIndex={US:{},CN:{}};
let previewRunIds={US:null,CN:null};
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
  noCandidate:'尚无 Candidate',propose:'生成 Candidate Core',run:'立即运行（预览）',runAll:'预览两个市场',
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
  noCandidate:'No Candidate yet',propose:'Generate Candidate Core',run:'Run Preview',runAll:'Preview Both Markets',
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

function applyUiTooltips(){
 Object.entries(UI_TIPS[lang]||{}).forEach(([id,tip])=>{
  const node=el(id); if(!node)return;
  node.classList.add('has-tip','tip-mark');
  node.dataset.tip=tip;
  node.setAttribute('aria-label',(node.textContent||'').trim()+' — '+tip);
 });
}

function normalizeHeaderLabel(text){
 return String(text||'').replace(/\\s+/g,'').replace(/[：:]/g,'').trim();
}
function tableHeaderTip(label){
 const raw=String(label||'').trim();
 const key=normalizeHeaderLabel(raw);
 const local=TABLE_HEADER_TIPS[lang]||{};
 if(local[key])return local[key];
 const fallbackOther=lang==='zh'?(TABLE_HEADER_TIPS.en||{}):(TABLE_HEADER_TIPS.zh||{});
 if(fallbackOther[key])return fallbackOther[key];
 if(!raw)return lang==='zh'?'本列表头说明。':'Table-column explanation.';
 return lang==='zh'
  ? '本列展示“'+raw+'”对应的数据。具体口径以当前表格的冻结决策、真实结果和页面说明为准。'
  : 'This column shows data for “'+raw+'”. The exact definition follows the frozen decision, realized outcomes and the surrounding table context.';
}
function applyTableHeaderTooltips(root=document){
 root.querySelectorAll('table th').forEach(th=>{
  const label=(th.dataset.headerLabel||th.textContent||'').trim();
  if(!label)return;
  th.classList.add('has-tip','tip-mark');
  th.dataset.tip=tableHeaderTip(label);
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
 return (currency==='USD'?'$':currency==='CNY'?'¥':'')+v.toFixed(digits);
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
}
function statusTip(status){
 const key=String(status||'').toLowerCase();
 return TIP[lang][key]||TIP[lang].state;
}
async function json(url,opts){const r=await fetch(url,opts);if(!r.ok)throw new Error(await r.text());return r.json()}
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
 try{
  const [idx,act,strategyCtx]=await Promise.all([
   json('/api/market-data/live-indicators/'+m),
   json('/api/market-data/activity/'+m+'?limit=80'),
   json('/api/market-data/strategy-context/'+m)
  ]);
  strategyMarketContext[m]=strategyCtx;
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
  setPulse('marketPulse',false,true);setPulse('activityPulse',false,true);
  el('indexMeta').textContent='Live data error: '+e.message;
  el('scheduleMeta').textContent='Activity error: '+e.message;
 }
}
function onMarketChange(){refreshAll();refreshLiveWindows()}
async function runNow(){
 const m=el('market').value;const x=await json('/api/live/run/'+m,{method:'POST'});
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
    '<td class="num has-tip" data-tip="'+esc(rationale||'')+'">'+esc(x.analog_samples??'-')+'</td></tr>';
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
async function refreshAll(){
 const m=el('market').value;
 const previewId=previewRunIds[m];
 try{
  const cardsUrl='/api/strategies?market_id='+m+'&lang='+lang+(previewId?'&run_id='+encodeURIComponent(previewId):'');
  const [s,d,cards,curves,evo,runs,previewRun]=await Promise.all([
   json('/api/status'),json('/api/daily?market_id='+m),json(cardsUrl),
   json('/api/curves?market_id='+m),json('/api/evolution'),json('/api/runs?market_id='+m+'&limit=100'),
   previewId?json('/api/runs/'+encodeURIComponent(previewId)):Promise.resolve(null)
  ]);
  const isCN=m==='CN';
  const evaluated=[...runs].reverse().find(x=>
   x.evaluation&&x.evaluation.status==='EVALUATED'&&(!isCN||x.experiment_mode==='CN_RETURN_MAX_CAPACITY')
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
    : T[lang].strategies;
  }
  strategyNameIndex[m]=Object.fromEntries(cards.map(x=>[x.strategy_id,x.name]));
  const selected=cards.filter(x=>x.selected);
  const detailRuns=d.runs_detail||[];
  const officialLatest=isCN
    ? ([...detailRuns].reverse().find(x=>x.experiment_mode==='CN_RETURN_MAX_CAPACITY')||null)
    : (detailRuns.length?detailRuns[detailRuns.length-1]:null);
  const latest=previewRun||officialLatest;
  const lastCurve=curves.length?curves[curves.length-1]:null;
  renderComparison(evaluated);
  el('date').textContent=(previewRun?.market?.as_of)||d.date||'-';
  el('core').textContent=(previewRun?.triaid_decision?.core_version)||s.active_core.version;
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
  renderUSReturnMax(!isCN?d.us_return_max:null);
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
function toggleLang(){lang=lang==='zh'?'en':'zh';applyText();refreshAll();refreshLiveWindows()}
applyText();refreshAll();refreshLiveWindows();setInterval(refreshAll,15000);setInterval(refreshLiveWindows,5000);
</script>
</body>
</html>
"""
