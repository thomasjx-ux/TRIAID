from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
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

@asynccontextmanager
async def lifespan(app:FastAPI):
    tasks=[]
    if calendar_sync.enabled:
        tasks.append(asyncio.create_task(calendar_sync.run()))
    if market_automation.enabled:
        tasks.append(asyncio.create_task(market_automation.run()))
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

app=FastAPI(title="TRIAID FIN Evolution Lab V2",version="0.11.0",lifespan=lifespan)
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
        "storage_backend":storage.get("backend"),
        "storage_durability":storage.get("durability"),
        "storage_volume_mounted":volume.get("expected_mount_is_mounted"),
        "storage_persistence_confirmed":probe.get("confirmed_across_deployments"),
        "decision_automation_enabled":decision_scheduler.enabled,
        "broker_execution_enabled":False,
        "official_trading_calendar_version":TRADING_CALENDAR_VERSION,
        "calendar_sync_enabled":calendar_sync.enabled,
        "calendar_sync_version":calendar_sync.version,
    }


@app.get("/api/status")
def status()->dict:
    return engine.status()


@app.get("/api/storage/status")
def storage_status()->dict:
    return engine.store.status()


@app.post("/api/live/run/{market_id}", status_code=202)
def live_run(market_id: str, background_tasks: BackgroundTasks) -> dict:
    market_id = market_id.upper()
    if market_id not in {"US", "CN"}:
        raise HTTPException(status_code=400, detail="market_id must be US or CN")
    run = engine.create_pending_live_run(market_id)
    background_tasks.add_task(engine.execute_live, run.run_id, market_id)
    return {"run_id": run.run_id, "status": run.status, "market_id": market_id}


@app.post("/api/live/run-all", status_code=202)
def live_run_all(background_tasks: BackgroundTasks) -> dict:
    runs = []
    for market_id in ("US", "CN"):
        run = engine.create_pending_live_run(market_id)
        background_tasks.add_task(engine.execute_live, run.run_id, market_id)
        runs.append({"run_id": run.run_id, "status": run.status, "market_id": market_id})
    return {"runs": runs}


@app.post("/api/run", status_code=202)
def create_run(request: RunRequest, background_tasks: BackgroundTasks) -> dict:
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
def submit_outcome(run_id: str, outcome: OutcomeRequest) -> dict:
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
) -> list[dict]:
    cards = engine.strategy_population.strategy_cards(lang, market_id)
    latest_run = engine.latest_decision_run(market_id) if market_id else None
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
def evolution_propose() -> dict:
    return engine.propose_core_candidate()


@app.post("/api/evolution/promote/{version}")
def evolution_promote(version: str, validation: dict[str, Any] = Body(...)) -> dict:
    try:
        return engine.promote_core(version, validation)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="core version not found") from exc





@app.get("/api/strategy-evolution")
def strategy_evolution_status(market_id: str | None = None) -> dict:
    return engine.strategy_evolution_status(market_id)


@app.post("/api/strategy-evolution/propose/{market_id}")
def strategy_evolution_propose(market_id: str) -> dict:
    market_id=market_id.upper()
    if market_id not in {"US","CN"}:
        raise HTTPException(status_code=400, detail="market_id must be US or CN")
    return engine.propose_strategy_candidate(market_id)


@app.post("/api/strategy-evolution/promote/{market_id}/{version}")
def strategy_evolution_promote(
    market_id: str,
    version: str,
    validation: dict[str, Any] = Body(...),
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
.has-tip{cursor:help;text-decoration-line:underline;text-decoration-style:dotted;text-decoration-color:#aeb7c4;text-underline-offset:4px}
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
      <div class="label" id="baseReturnLabel">策略群基线收益</div>
      <div class="value base" id="baseReturn">等待后验</div>
      <div class="sub" id="baseReturnSub">介入前</div>
    </div>
    <div class="card">
      <div class="label" id="triaidReturnLabel">TRIAID 后收益</div>
      <div class="value triaid" id="triaidReturn">等待后验</div>
      <div class="sub" id="triaidReturnSub">介入后</div>
    </div>
    <div class="card gain" id="gainCard">
      <div class="label" id="gainLabel">TRIAID 增益</div>
      <div class="value" id="gain">等待后验</div>
      <div class="sub" id="gainSub">TRIAID 后收益 − 策略群基线</div>
    </div>
  </div>

  <h2 id="liveTitle">实时运行指示</h2>
  <div class="livegrid">
    <div class="livepanel">
      <div class="livehead">
        <div class="livehead-left"><span id="marketPulse" class="pulse"></span><span id="indexWindowTitle">实时指数窗口</span></div>
        <span class="small muted" id="indexPhase">-</span>
      </div>
      <div class="live-meta" id="indexMeta">等待实时市场数据</div>
      <div class="indexgrid" id="indexRows"></div>
    </div>
    <div class="livepanel">
      <div class="livehead">
        <div class="livehead-left"><span id="activityPulse" class="pulse"></span><span id="activityWindowTitle">后台指令流水</span></div>
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
        <th id="thExp" class="has-tip">预期净回报</th>
        <th id="thRisk" class="has-tip">风险</th>
        <th id="thBase" class="has-tip">介入前</th>
        <th id="thTriaid" class="has-tip">TRIAID 后</th>
        <th id="thDelta" class="has-tip">增减</th>
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
          <th id="cthExp" class="has-tip">预期净回报</th>
          <th id="cthRisk" class="has-tip">风险</th>
          <th id="cthWhy" class="has-tip">说明</th>
        </tr></thead>
        <tbody id="candidateRows"></tbody>
      </table>
    </div>
  </details>

  <h2 id="dailyTitle">今日摘要</h2>
  <div class="summary">
    <div class="item"><span class="label" id="regimeLabel">市场状态</span><b id="regime">-</b></div>
    <div class="item"><span class="label" id="runStateLabel">运行状态</span><b id="runState">-</b></div>
    <div class="item"><span class="label" id="selectedNamesLabel">当前入选</span><b id="selectedNames">-</b></div>
    <div class="item"><span class="label" id="dailyAnalysisLabel">今日结论</span><b id="dailyAnalysis">-</b></div>
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
      <div class="item"><span class="label" id="usrmExpectedLabel">Return-Max 年化预期</span><b id="usrmExpected">-</b></div>
      <div class="item"><span class="label" id="usrmGenericLabel">通用 Core 年化预期</span><b id="usrmGeneric">-</b></div>
      <div class="item"><span class="label" id="usrmSpyLabel">SPY Buy & Hold 年化预期</span><b id="usrmSpy">-</b></div>
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
    <h3 id="usrmAssetTitle">底层可执行 ETF 敞口</h3>
    <div class="tablewrap" style="max-height:280px">
      <table>
        <thead><tr><th>ETF</th><th>目标权重</th></tr></thead>
        <tbody id="usrmAssetRows"></tbody>
      </table>
    </div>
    <h3 id="usrmCapitalTitle">四美元资金规模容量实验</h3>
    <div class="prospective-meta" id="usrmCapitalMeta">-</div>
    <div class="tablewrap" style="max-height:330px">
      <table>
        <thead><tr><th>起始资金</th><th>目标投入</th><th>单日ADV占比</th><th>最少成交天数</th><th>预计往返成本</th></tr></thead>
        <tbody id="usrmCapitalRows"></tbody>
      </table>
    </div>
    <h3 id="usrmRealizedTitle">上一轮真实收益与容量回顾</h3>
    <div class="tablewrap" style="max-height:360px">
      <table>
        <thead><tr><th>起始资金</th><th>成交比例</th><th>当前净值</th><th>净利润</th><th>净收益率</th><th>执行成本</th></tr></thead>
        <tbody id="usrmRealizedRows"></tbody>
      </table>
    </div>
    <h3 id="usrmControlTitle">上一轮理论对照路径</h3>
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
    <h3 id="prospectiveStrategyTitle">策略确定与恢复排序</h3>
    <div class="tablewrap" style="max-height:430px">
      <table>
        <thead><tr>
          <th id="pthStrategy">策略</th>
          <th id="pthPredRank">TRIAID预测名次</th>
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
        <b id="recoveryWaveTitle">TRIAID 二阶恢复波段决策</b>
        <div class="prospective-meta" id="recoveryWaveMeta">-</div>
      </div>
      <span class="tag" id="recoveryWaveStatus">-</span>
    </div>
    <div class="summary prospective-kpis">
      <div class="item"><span class="label" id="recoveryCashLabel">现金剩余</span><b id="recoveryCash">-</b></div>
      <div class="item"><span class="label" id="recoveryPrevDaysLabel">上一轮已观察</span><b id="recoveryPrevDays">-</b></div>
      <div class="item"><span class="label" id="recoveryPrevReturnLabel">上一轮实际收益</span><b id="recoveryPrevReturn">-</b></div>
      <div class="item"><span class="label" id="recoveryPrevGapLabel">相对等权对照</span><b id="recoveryPrevGap">-</b></div>
    </div>
    <div class="prospective-note" id="recoveryWaveNote">-</div>
    <h3 id="recoveryOpinionTitle">当前冻结交易意见</h3>
    <div class="tablewrap" style="max-height:430px">
      <table>
        <thead><tr>
          <th id="rwthProduct">产品</th>
          <th id="rwthAction">意见</th>
          <th id="rwthTarget">目标权重</th>
          <th id="rwthChange">本轮调整</th>
          <th id="rwthDrawdown">当前回撤</th>
          <th id="rwthDirection">状态方向</th>
          <th id="rwthHorizon">预计反转</th>
          <th id="rwthSpeed">预计恢复速度/日</th>
          <th id="rwthHitEdge">恢复率优势</th>
          <th id="rwthExpected">历史相似状态预期</th>
          <th id="rwthSamples">样本</th>
        </tr></thead>
        <tbody id="recoveryOpinionRows"></tbody>
      </table>
    </div>
    <h3 id="capitalSleeveTitle">四资金规模容量实验</h3>
    <div class="prospective-meta" id="capitalSleeveMeta">-</div>
    <div class="tablewrap" style="max-height:330px">
      <table>
        <thead><tr>
          <th id="csthCapital">起始资金</th>
          <th id="csthInvested">目标投入</th>
          <th id="csthParticipation">单日ADV占比</th>
          <th id="csthDays">最少成交天数</th>
          <th id="csthCost">预计往返成本</th>
          <th id="csthPnl">预期波段净利润</th>
          <th id="csthReturn">预期净收益率</th>
        </tr></thead>
        <tbody id="capitalSleeveRows"></tbody>
      </table>
    </div>
    <h3 id="capitalRealizedTitle">上一轮四资金袖套真实执行回顾</h3>
    <div class="tablewrap" style="max-height:330px">
      <table>
        <thead><tr>
          <th id="crthCapital">起始资金</th>
          <th id="crthFill">已成交比例</th>
          <th id="crthEquity">当前净值</th>
          <th id="crthPnl">净利润</th>
          <th id="crthReturn">净收益率</th>
          <th id="crthCost">累计执行成本</th>
          <th id="crthRemaining">未成交目标</th>
        </tr></thead>
        <tbody id="capitalRealizedRows"></tbody>
      </table>
    </div>
    <h3 id="recoveryReviewTitle">上一轮决策真实回顾</h3>
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
    <div class="card"><div class="label" id="coreLabel">当前 Core</div><div class="value triaid" id="core">-</div></div>
    <div class="card"><div class="label" id="selectedLabel">当前策略数</div><div class="value" id="selectedCount">-</div></div>
    <div class="card"><div class="label" id="cumLabel">累计 TRIAID 超额</div><div class="value" id="cumExcess">-</div></div>
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
      <div class="label" id="evoObservedLabel">已验证决策</div>
      <div class="value" id="evoObserved">0</div>
      <div class="sub" id="evoMean">尚无可评价结果</div>
    </div>
    <div class="card">
      <div class="label" id="evoNegLabel">负贡献比例</div>
      <div class="value" id="evoNeg">-</div>
      <div class="sub" id="evoNote">Core 会根据持续验证结果形成候选改进</div>
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
const el=id=>document.getElementById(id);
const T={
 zh:{
  title:'TRIAID FIN 进化实验台 V2',subtitle:'真实市场 → 动态策略群 → TRIAID Core → 后验验证 → 持续进化',
  result:'TRIAID 结果比较',baseReturn:'策略群基线收益',triaidReturn:'TRIAID 后收益',gain:'TRIAID 增益',
  live:'实时运行指示',indexWindow:'实时指数窗口',activityWindow:'后台指令流水',
  baseSub:'介入前',triaidSub:'介入后',gainSub:'TRIAID 后收益 − 策略群基线',
  overview:'当前状态',date:'最新数据日',core:'当前 Core',selected:'当前策略数',cum:'累计 TRIAID 超额',
  curve:'连续回顾',legendBase:'策略群基线',legendTriaid:'TRIAID',
  daily:'今日摘要',regime:'市场状态',runState:'运行状态',selectedNames:'当前入选',analysis:'今日结论',
  usReturnMax:'美股 Return-Max 路线',usrmExpected:'Return-Max 年化预期',usrmGeneric:'通用 Core 年化预期',usrmSpy:'SPY Buy & Hold 年化预期',usrmRisk:'目标风险仓位',usrmStrategy:'当前冻结策略权重',usrmAsset:'底层可执行 ETF 敞口',usrmCapital:'四美元资金规模容量实验',usrmRealized:'上一轮真实收益与容量回顾',usrmControl:'上一轮理论对照路径',
  prospective:'A股前瞻对照实验',prospectiveDays:'已观察交易日',prospectiveHold:'最差池累计收益',prospectiveTriaid:'TRIAID冻结配置累计收益',prospectiveGap:'TRIAID相对最差池',
  prospectiveStrategy:'策略确定与恢复排序',prospectiveDaily:'每日波动轨迹',predRank:'TRIAID预测名次',dailyReturn:'最近一日',cumReturn:'累计收益',realRank:'当前实际名次',detReason:'确定依据',
  recoveryWave:'TRIAID 二阶恢复波段决策',recoveryCash:'现金剩余',recoveryPrevDays:'上一轮已观察',recoveryPrevReturn:'上一轮实际收益',recoveryPrevGap:'相对等权对照',
  recoveryOpinion:'当前冻结交易意见',recoveryReview:'上一轮决策真实回顾',product:'产品',action:'意见',target:'目标权重',change:'本轮调整',drawdown:'当前回撤',direction:'状态方向',horizon:'预计反转',speed:'预计恢复速度/日',hitEdge:'恢复率优势',expected:'历史相似状态预期',samples:'样本',resultDate:'结果日',portfolioDay:'冻结组合当日',equalDay:'等权对照当日',portfolioCum:'冻结组合累计',gapCum:'累计差值',
  capitalSleeve:'四资金规模容量实验',capitalRealized:'上一轮四资金袖套真实执行回顾',capital:'起始资金',invested:'目标投入',participation:'单日ADV占比',days:'最少成交天数',cost:'预计往返成本',pnl:'预期波段净利润',netReturn:'预期净收益率',fill:'已成交比例',equity:'当前净值',realizedPnl:'净利润',realizedReturn:'净收益率',executionCost:'累计执行成本',remaining:'未成交目标',
  strategies:'当前策略群与 TRIAID 调整',candidatePool:'查看未入选候选策略池',strategy:'策略',state:'状态',exp:'预期净回报',risk:'风险',
  before:'介入前',after:'TRIAID 后',delta:'增减',why:'策略说明与选择原因',
  evolution:'Core 进化状态',observed:'已验证决策',negative:'负贡献比例',next:'下一步',
  noEval:'等待下一交易日后验',noResult:'尚无可评价结果',evoNote:'Core 会根据持续验证结果形成候选改进',
  noCandidate:'尚无 Candidate',propose:'生成 Candidate Core',run:'立即执行',runAll:'执行两个市场',
  running:'已创建运行，后台正在读取真实市场数据。',pending:'当前决策已生成，等待下一交易日结果。',
  positive:'TRIAID 本次产生正增益',negativeResult:'TRIAID 本次产生负增益，需要回看干预归因',flat:'TRIAID 本次与基线基本一致'
 },
 en:{
  title:'TRIAID FIN Evolution Lab V2',subtitle:'Real market → Dynamic strategy population → TRIAID Core → Outcome validation → Continuous evolution',
  result:'TRIAID Result Comparison',baseReturn:'Strategy-group baseline',triaidReturn:'After TRIAID',gain:'TRIAID uplift',
  live:'Live Runtime Indicators',indexWindow:'Live Market Index Window',activityWindow:'Backend Command Stream',
  baseSub:'Before intervention',triaidSub:'After intervention',gainSub:'After TRIAID − strategy-group baseline',
  overview:'Current State',date:'Latest market date',core:'Active Core',selected:'Selected strategies',cum:'Cumulative TRIAID excess',
  curve:'Continuous Review',legendBase:'Strategy-group baseline',legendTriaid:'TRIAID',
  daily:'Daily Summary',regime:'Market regime',runState:'Run status',selectedNames:'Selected now',analysis:'Daily conclusion',
  usReturnMax:'US Return-Max Route',usrmExpected:'Return-Max expected annualized',usrmGeneric:'Generic Core expected annualized',usrmSpy:'SPY Buy & Hold expected annualized',usrmRisk:'Target risk exposure',usrmStrategy:'Current Frozen Strategy Weights',usrmAsset:'Executable ETF Exposure',usrmCapital:'Four-USD-Capital Capacity Experiment',usrmRealized:'Prior Realized Return and Capacity Review',usrmControl:'Prior Theoretical Control Path',
  prospective:'CN Prospective Control Experiment',prospectiveDays:'Observed trading days',prospectiveHold:'Worst-pool cumulative return',prospectiveTriaid:'Frozen TRIAID cumulative return',prospectiveGap:'TRIAID vs worst pool',
  prospectiveStrategy:'Strategy Determination and Recovery Ranking',prospectiveDaily:'Daily Fluctuation Path',predRank:'TRIAID predicted rank',dailyReturn:'Latest day',cumReturn:'Cumulative return',realRank:'Current realized rank',detReason:'Determination basis',
  recoveryWave:'TRIAID Second-Order Recovery Wave Decision',recoveryCash:'Cash residual',recoveryPrevDays:'Prior decision observed days',recoveryPrevReturn:'Prior realized return',recoveryPrevGap:'Vs equal-weight control',
  recoveryOpinion:'Current Frozen Trade Opinion',recoveryReview:'Prior Decision Realized Review',product:'Product',action:'Opinion',target:'Target weight',change:'This decision change',drawdown:'Current drawdown',direction:'State direction',horizon:'Expected reversal',speed:'Expected recovery/day',hitEdge:'Recovery-rate edge',expected:'Historical-analog expectation',samples:'Samples',resultDate:'Outcome date',portfolioDay:'Frozen portfolio day',equalDay:'Equal-weight day',portfolioCum:'Frozen portfolio cumulative',gapCum:'Cumulative gap',
  capitalSleeve:'Four-Capital Capacity Experiment',capitalRealized:'Prior Four-Sleeve Realized Execution Review',capital:'Starting capital',invested:'Target invested',participation:'One-day ADV share',days:'Minimum execution days',cost:'Estimated round-trip cost',pnl:'Expected wave net P&L',netReturn:'Expected net return',fill:'Fill ratio',equity:'Current equity',realizedPnl:'Net P&L',realizedReturn:'Net return',executionCost:'Cumulative execution cost',remaining:'Unfilled target',
  strategies:'Current Strategy Group and TRIAID Adjustments',candidatePool:'View unselected candidate pool',strategy:'Strategy',state:'State',exp:'Expected net return',risk:'Risk',
  before:'Before',after:'After TRIAID',delta:'Change',why:'Strategy explanation and selection reason',
  evolution:'Core Evolution State',observed:'Verified decisions',negative:'Negative-contribution rate',next:'Next step',
  noEval:'Awaiting next-period outcome',noResult:'No evaluated outcome yet',evoNote:'Core forms candidate improvements from continuously verified results',
  noCandidate:'No Candidate yet',propose:'Generate Candidate Core',run:'Run Now',runAll:'Run Both Markets',
  running:'Run created. Real market data is being processed in the background.',pending:'Current decision is ready and awaiting the next market outcome.',
  positive:'TRIAID produced positive uplift in the latest evaluated run',negativeResult:'TRIAID produced negative uplift; intervention attribution should be reviewed',flat:'TRIAID is approximately in line with baseline'
 }
};
const TIP={
 zh:{
  strategy:'策略名称和策略编号。当前策略群主表只显示真正获得配置权重的策略。',
  state:'策略生命周期：ACTIVE=正式参与配置；SHADOW=只做真实前瞻验证、不获得生产权重；REDUCED=降级观察；FROZEN=暂停；CANDIDATE=候选阶段。',
  exp:'基于当前时点可见的 21/63/126/252 日真实净收益轨迹估计的年化预期净收益。它是预测值，不是已经实现的收益。',
  risk:'策略近期收益波动的年化值。数值越大，代表收益越不稳定；这里不是“亏损概率”。',
  before:'Strategy Population 完成选群以后、TRIAID Core 尚未介入时的基础资金权重。',
  after:'TRIAID Core 根据当前市场状态、策略风险与不确定性调整后的最终权重。',
  delta:'TRIAID 后权重 − 介入前权重。正数表示 TRIAID 增配，负数表示减配。',
  why:'说明这个策略做什么、为什么今天进入当前策略群，以及 TRIAID 为什么增配或减配。',
  candidateWhy:'说明候选策略的核心逻辑和适用市场。未入选策略不会获得当前正式配置权重。',
  active:'ACTIVE：已通过当前准入条件，可以正式参与策略群并获得生产权重。',
  shadow:'SHADOW：只记录真实未来表现进行前瞻验证，暂时不获得任何正式配置权重。',
  reduced:'REDUCED：策略被降级观察，仍可能保留少量权重，但正在接受进一步验证。',
  frozen:'FROZEN：策略已冻结，暂停进入正式策略群。',
  candidate:'CANDIDATE：候选阶段，尚未满足进入正式策略群的证据要求。',
  research:'RESEARCH：研究阶段，只用于开发和验证。',
  retired:'RETIRED：已退出当前策略体系，除非出现新的证据，否则不再参与选群。'
 },
 en:{
  strategy:'Strategy name and ID. The main group table shows only strategies that actually receive allocation weight.',
  state:'Strategy lifecycle: ACTIVE=eligible for live allocation; SHADOW=prospective observation only with no live weight; REDUCED=degraded monitoring; FROZEN=paused; CANDIDATE=pre-admission.',
  exp:'Annualized expected net return estimated only from information visible at the current time across 21/63/126/252-day realized net-return histories. It is a forecast, not realized profit.',
  risk:'Annualized volatility of recent strategy returns. Higher means less stable returns; it is not the probability of losing money.',
  before:'Baseline capital weight assigned by Strategy Population before TRIAID Core intervenes.',
  after:'Final weight after TRIAID Core adjusts for current market state, risk and uncertainty.',
  delta:'After-TRIAID weight minus baseline weight. Positive means TRIAID adds allocation; negative means it reduces allocation.',
  why:'Explains what the strategy does, why it entered the current group, and why TRIAID increased or reduced it.',
  candidateWhy:'Explains the candidate strategy logic and suitable conditions. Unselected strategies receive no current live allocation.',
  active:'ACTIVE: eligible for the live strategy group and production allocation.',
  shadow:'SHADOW: prospectively tracked on real future data but receives no production allocation.',
  reduced:'REDUCED: downgraded for further observation and may retain only limited allocation.',
  frozen:'FROZEN: paused and excluded from the live strategy group.',
  candidate:'CANDIDATE: not yet supported by enough evidence for live admission.',
  research:'RESEARCH: development and validation only.',
  retired:'RETIRED: removed from the current strategy system unless new evidence justifies reconsideration.'
 }
};

const TABLE_HEADER_TIPS={
 zh:{
  '策略':'当前策略名称或策略编号。鼠标移到具体策略状态上还可查看生命周期说明。',
  '状态':'当前策略或任务的状态。ACTIVE、SHADOW、FROZEN 等状态代表不同的准入和运行阶段。',
  '预期净回报':'基于当前时点可见信息估计的预期净收益，属于前瞻预测，不是已经实现的收益。',
  '风险':'当前策略收益波动和不确定性的风险度量。数值越高代表收益路径越不稳定。',
  '介入前':'TRIAID Core 介入前，由基础策略群或对照方案给出的配置权重。',
  'TRIAID后':'TRIAID 根据当前状态、风险和不确定性重新评估后的配置权重。',
  '增减':'TRIAID 后权重减去介入前权重。正数表示增配，负数表示减配。',
  '策略说明与选择原因':'策略的核心逻辑、适用状态、进入当前策略群的原因以及 TRIAID 调整原因。',
  '说明':'当前行策略或结果的补充解释，包括适用条件、状态和选择依据。',
  'Return-Max权重':'美股 Return-Max 路线当前冻结的策略权重，以预期净收益最大化为主目标。',
  '通用Core权重':'通用 TRIAID Core 在同一时点给出的对照策略权重，用于与 Return-Max 路线比较。',
  'ETF':'最终可执行的 ETF 标的。策略层权重会展开到这些底层资产。',
  '目标权重':'当前冻结决策建议配置到该产品或资产的目标比例，不代表已经自动成交。',
  '起始资金':'本资金袖套用于容量和成本评估的初始账户规模。',
  '目标投入':'按当前目标权重计划投入风险资产的名义资金规模。',
  '单日ADV占比':'计划成交额占该资产近似日均成交额 ADV 的比例，用于限制市场冲击。',
  '最少成交天数':'在当前 ADV 参与率限制下，完成目标仓位至少需要的交易日数量。',
  '预计往返成本':'按当前流动性和冲击模型估计的买入加卖出总执行成本。',
  '成交比例':'当前资金袖套已完成的目标成交比例。',
  '已成交比例':'当前资金袖套已完成的目标成交比例。',
  '当前净值':'当前资金袖套在计入持仓收益和执行成本后的账户价值。',
  '净利润':'当前净值减去起始资金后的净损益。',
  '净收益率':'相对起始资金的净收益率，已计入模型中的执行成本。',
  '执行成本':'本轮已经计入的交易执行成本，包括基础成本和流动性冲击代理。',
  '累计执行成本':'截至当前累计产生的模型化执行成本。',
  '未成交目标':'受容量或参与率约束尚未完成的目标名义仓位。',
  '结果日':'该行真实后验结果对应的完整交易日。',
  'Return-Max累计':'从冻结决策开始，Return-Max 组合截至该日的累计净收益。',
  '通用Core累计':'从同一起点开始，通用 Core 对照组合截至该日的累计净收益。',
  'SPY累计':'同期 SPY 买入持有对照的累计收益。',
  'TRIAID预测名次':'冻结时由 TRIAID 根据当时可见信息给出的策略相对排序。',
  '基线权重':'不使用 TRIAID 状态调整时的冻结对照权重。',
  'TRIAID权重':'登记时冻结的 TRIAID 配置权重，后续结果不能倒推修改。',
  '最近一日':'最新一个完整交易日该策略的真实单日收益。',
  '累计收益':'从实验登记或冻结起点到当前的累计真实收益。',
  '当前实际名次':'根据已经发生的真实累计收益计算出的当前实际排序。',
  '确定依据':'冻结时选择、排序或配置该策略所使用的证据和规则。',
  '产品':'当前恢复波段决策对应的 ETF 或可交易产品。',
  '意见':'本轮冻结的研究意见，例如增配、减配、持有或观察，不会自动发送券商订单。',
  '本轮调整':'目标权重相对上一轮目标权重的变化幅度。',
  '当前回撤':'当前价格相对近期高点的回落幅度，用于描述恢复空间和压力。',
  '状态方向':'当前状态更偏向修复、承压、转强、转弱或震荡的方向判断。',
  '预计反转':'根据冻结时状态估计的潜在反转或恢复时间窗口。',
  '预计恢复速度/日':'若进入恢复阶段，模型估计的平均每日恢复速度。',
  '恢复率优势':'历史相似状态中，该产品相对对照的恢复成功率或恢复效率优势。',
  '历史相似状态预期':'基于历史相似状态得到的前瞻收益参考，不等于保证实现的收益。',
  '样本':'形成当前历史相似状态统计所使用的有效样本数量。',
  '预期波段净利润':'按当前目标仓位、预期收益和执行成本估计的波段净利润。',
  '预期净收益率':'扣除模型化执行成本后的预期波段净收益率。',
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
  'Expectednetreturn':'Forward expected net return estimated only from information available at the decision time; it is not realized profit.',
  'Risk':'Risk measure based on recent return variability and uncertainty. Higher values indicate a less stable return path.',
  'BeforeTRIAID':'Baseline allocation before TRIAID Core intervention.',
  'AfterTRIAID':'Allocation after TRIAID evaluates current state, risk and uncertainty.',
  'Change':'After-TRIAID weight minus baseline weight. Positive adds allocation and negative reduces it.',
  'Explanation':'Supporting explanation for the row, including logic, conditions and selection rationale.',
  'Return-Maxweight':'Frozen strategy weight from the US Return-Max route, whose primary objective is expected net return maximization.',
  'GenericCoreweight':'Contemporaneous generic TRIAID Core allocation used as the control for the Return-Max route.',
  'ETF':'Executable underlying ETF exposure produced from the strategy-level allocation.',
  'Targetweight':'Frozen target allocation for this product or asset; it does not imply that a broker order has been sent.',
  'Startingcapital':'Initial account size used for capacity and transaction-cost evaluation.',
  'Targetinvested':'Target notional amount allocated to risky assets under the frozen decision.',
  'One-dayADVshare':'Planned trade size as a share of approximate average daily volume, used to limit market impact.',
  'Minimumexecutiondays':'Minimum trading days required to complete the target position under the ADV participation cap.',
  'Estimatedround-tripcost':'Estimated total buy-plus-sell execution cost under the current liquidity and impact model.',
  'Fillratio':'Share of the target position completed so far.',
  'Currentequity':'Current account value after realized market moves and modeled execution costs.',
  'NetP&L':'Current equity minus starting capital.',
  'Netreturn':'Net return relative to starting capital after modeled execution costs.',
  'Executioncost':'Modeled trading cost already incurred, including base cost and liquidity-impact proxy.',
  'Cumulativeexecutioncost':'Cumulative modeled execution cost to date.',
  'Remainingtarget':'Target notional position not yet completed because of capacity or participation constraints.',
  'Resultdate':'Complete trading day represented by this realized outcome row.',
  'Return-Maxcumulative':'Cumulative net return of the frozen Return-Max portfolio since the decision date.',
  'GenericCorecumulative':'Cumulative net return of the generic Core control from the same starting point.',
  'SPYcumulative':'Cumulative SPY buy-and-hold return over the same period.',
  'TRIAIDpredictedrank':'Relative ranking frozen by TRIAID using only information available at registration.',
  'Baselineweight':'Frozen control allocation without TRIAID state adjustment.',
  'TRIAIDweight':'TRIAID allocation frozen at registration and not retuned using future outcomes.',
  'Latestday':'Realized return for the latest complete trading day.',
  'Cumulativereturn':'Realized cumulative return since experiment registration or decision freeze.',
  'Currentrealizedrank':'Current ranking computed from realized cumulative returns observed so far.',
  'Rationale':'Evidence and rule used to select, rank or allocate the strategy at freeze time.',
  'Product':'ETF or executable product covered by the current recovery-wave decision.',
  'Action':'Frozen research opinion such as add, reduce, hold or observe; no broker order is generated.',
  'Currentdrawdown':'Current decline from a recent high, used as a proxy for pressure and recovery room.',
  'Statedirection':'Current directional state such as recovering, weakening, strengthening, stressed or mixed.',
  'Expectedreversal':'Estimated reversal or recovery horizon from the frozen state.',
  'Expectedrecoveryspeed/day':'Estimated average daily recovery speed if a recovery phase develops.',
  'Recoveryedge':'Historical recovery-rate or recovery-efficiency advantage relative to the control.',
  'Historicalanalogueexpectation':'Forward-return reference derived from historical analogue states; it is not a guaranteed outcome.',
  'Samples':'Number of valid historical analogue samples supporting the current statistic.',
  'ExpectedswingnetP&L':'Estimated swing net profit after target sizing and modeled execution costs.',
  'Expectednetreturn':'Expected swing net return after modeled execution costs.',
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
  th.classList.add('has-tip');
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
 return (currency==='USD'?'
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
   : (lang==='zh'?'暂无实时数据':'No realtime data');
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
 el('runStatus').textContent=T[lang].running+' '+x.run_id;pollRun(x.run_id);
}
async function runAll(){
 const x=await json('/api/live/run-all',{method:'POST'});
 el('runStatus').textContent=T[lang].running+' '+x.runs.map(r=>r.run_id).join(' | ');
 x.runs.forEach(r=>pollRun(r.run_id));
}
async function pollRun(id){
 for(let i=0;i<30;i++){
  await new Promise(r=>setTimeout(r,1000));
  try{
   const x=await json('/api/runs/'+id);el('runStatus').textContent=id+' · '+x.status;
   if(!['CREATED','FETCHING_DATA'].includes(x.status)){refreshAll();return}
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
 el('usReturnMaxMeta').textContent=(d.decision_id||'-')+' · '+(lang==='zh'?'冻结 ':'Frozen ')+(d.frozen_at||'-')+' · '+(d.selection_source||'-');
 el('usrmExpected').textContent=fmtPct(d.projected_annualized_expected_net_return);
 el('usrmGeneric').textContent=fmtPct(d.generic_core_projected_annualized_expected_net_return);
 el('usrmSpy').textContent=fmtPct(d.buy_hold_projected_annualized_expected_net_return);
 el('usrmRisk').textContent=fmtPct(1-Number(d.cash_residual_weight||0));
 el('usReturnMaxNote').textContent=lang==='zh'
  ? '美股主路线不使用A股反转恢复逻辑，而是在所有 ACTIVE 策略中严格选择当前预期净收益最高者；收益并列时依次用更低执行成本、风险、不确定性和固定策略ID打破平局，再展开成 SPY/QQQ/IWM/TLT/GLD 的可执行头寸。四个美元账户共享同一冻结决策，只让资金规模改变成交容量和冲击成本。'
  : 'The US primary route does not reuse the CN recovery thesis. It strictly selects the ACTIVE strategy with the highest current expected net return; exact return ties are broken by lower execution cost, risk, uncertainty, then deterministic strategy ID, before expansion into executable SPY/QQQ/IWM/TLT/GLD exposures. All four USD sleeves share the same frozen decision; only capital size changes capacity and impact.';
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
  '<tr><td colspan="6">'+(lang==='zh'?'上一轮尚无真实执行结果':'No realized execution result for the prior decision yet')+'</td></tr>';
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
   ? '策略池与预测顺序在登记时冻结，禁止事后换人或调参。累计收益来自后续真实策略收益；全现金只作为防守对照，不作为恢复预测能力的主要证据。'
   : 'Pool membership and predicted ordering are frozen at registration with no post-result retuning. Cumulative returns use subsequent realized strategy returns; cash is a defense control, not the primary evidence of recovery-selection skill.';
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
 el('recoveryWaveMeta').textContent=(d.decision_id||'-')+' · '+(lang==='zh'?'冻结 ':'Frozen ')+(d.frozen_at||'-')+' · '+(lang==='zh'?'源时间 ':'Source ')+(d.source_latest_ts||'-');
 el('recoveryCash').textContent=fmtPct(d.cash_residual_weight);
 el('recoveryPrevDays').textContent=review?String(review.observation_days??0):'-';
 el('recoveryPrevReturn').textContent=review?fmtPct(review.current_portfolio_cumulative_return):'-';
 el('recoveryPrevReturn').className=review?cls(Number(review.current_portfolio_cumulative_return||0)):'';
 el('recoveryPrevGap').textContent=review?signedPct(review.current_excess_vs_equal_weight):'-';
 el('recoveryPrevGap').className=review?cls(Number(review.current_excess_vs_equal_weight||0)):'';
 const scope=d.data_scope||{};
 el('recoveryWaveNote').textContent=lang==='zh'
  ? '这是 Shadow Core 的冻结研究交易意见，不生成券商订单。T 时点决策只能从下一完整可交易 bar 起计算结果；当前微观层仅使用 ETF/指数基金自身真实价格与成交量，成分股级微观数据尚未接入。四资金袖套从同一决策、全现金起步，唯一变量是资金规模；未来填单只使用后续真实成交量，成交后从下一完整 bar 才开始计收益。历史建议与参数均冻结，不能事后改写。'
  : 'These are frozen Shadow Core research opinions and generate no broker orders. A decision at T is evaluated only from the next complete tradable bar. The four capital sleeves start from cash under the same frozen decision; capital size is the only experimental variable. Future fills use only subsequent observed volume and become return-active on the following complete bar. Frozen advice and parameters cannot be rewritten after outcomes.';
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
 }).join('') || '<tr><td colspan="11">'+(lang==='zh'?'暂无冻结交易意见':'No frozen trade opinion')+'</td></tr>';
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
 }).join('') || '<tr><td colspan="7">'+(lang==='zh'?'上一轮资金袖套尚无可用真实执行结果':'No eligible realized execution result for the prior sleeves yet')+'</td></tr>';
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
 try{
  const [s,d,cards,curves,evo,runs]=await Promise.all([
   json('/api/status'),json('/api/daily?market_id='+m),json('/api/strategies?market_id='+m+'&lang='+lang),
   json('/api/curves?market_id='+m),json('/api/evolution'),json('/api/runs?market_id='+m+'&limit=100')
  ]);
  const isCNStress=m==='CN';
  const evaluated=[...runs].reverse().find(x=>
   x.evaluation&&x.evaluation.status==='EVALUATED'&&(!isCNStress||x.experiment_mode==='CN_WORST_POOL_RESCUE')
  )||null;
  if(isCNStress){
   el('resultTitle').textContent=lang==='zh'?'A股逆向压力实验结果':'CN Adversarial Stress Experiment';
   el('baseReturnLabel').textContent=lang==='zh'?'最差策略池基线收益':'Worst-pool baseline return';
   el('baseReturnSub').textContent=lang==='zh'?'故意构造的不利起点':'Intentionally adverse baseline';
   el('gainLabel').textContent=lang==='zh'?'TRIAID 挽回损失':'TRIAID loss rescue';
   el('gainSub').textContent=lang==='zh'?'TRIAID 后 − 最差策略池基线':'After TRIAID − worst-pool baseline';
   el('strategyTitle').textContent=lang==='zh'?'A股最差策略池与 TRIAID 减损调整':'CN Worst Strategy Pool and TRIAID Loss Reduction';
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
  const latest=d.runs_detail&&d.runs_detail.length?d.runs_detail[d.runs_detail.length-1]:null;
  const lastCurve=curves.length?curves[curves.length-1]:null;
  renderComparison(evaluated);
  el('date').textContent=d.date||'-';el('core').textContent=s.active_core.version;el('selectedCount').textContent=selected.length;
  const cum=lastCurve?lastCurve.cumulative_excess_return:null;el('cumExcess').textContent=fmtPct(cum);el('cumExcess').className='value '+cls(cum||0);
  el('regime').textContent=latest?.regime||'-';el('runState').textContent=latest?.status||'-';
  el('selectedNames').innerHTML=selected.length?selected.slice(0,6).map(x=>strategyLabelHtml(x.name,x.strategy_id)).join(lang==='zh'?'、':' · ')+(selected.length>6?' …':''):'-';
  if(evaluated){
   const g=Number(evaluated.evaluation.excess_return||0);
   el('dailyAnalysis').textContent=g>1e-12?T[lang].positive:g<-1e-12?T[lang].negativeResult:T[lang].flat;
   el('dailyAnalysis').className=cls(g);
  }else if(isCNStress&&latest?.diagnostic_summary?.experiment_mode==='CN_WORST_POOL_RESCUE'){
   const p=Number(latest.diagnostic_summary.projected_excess_expected_return);
   el('dailyAnalysis').textContent=Number.isFinite(p)
    ? (lang==='zh'?'当前信息下预计减损 '+signedPct(p)+'，实际挽回以后验为准':'Projected loss reduction '+signedPct(p)+' on current information; realized rescue awaits outcome')
    : T[lang].pending;
   el('dailyAnalysis').className=Number.isFinite(p)?cls(p):'';
  }else{el('dailyAnalysis').textContent=T[lang].pending;el('dailyAnalysis').className='';}
  renderUSReturnMax(!isCNStress?d.us_return_max:null);
  renderProspective(isCNStress?d.prospective_experiment:null);
  renderRecoveryWave(isCNStress?d.recovery_wave:null);
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
    (lang==='zh'?'平均 TRIAID 增益 ':'Mean TRIAID uplift ')+signedPct(diag.mean_excess_return);
  el('evoMean').className='sub '+cls(diag.mean_excess_return||0);
  el('evoNeg').textContent=diag.negative_rate===null||diag.negative_rate===undefined?'-':fmtPct(diag.negative_rate);
  const history=evo.history||[];const last=history.length?history[history.length-1]:null;
  el('evoLast').textContent=last?(last.event+' · '+(last.version||'')):T[lang].noCandidate;
  el('runStatus').textContent=latest?((latest.market_id||m)+' · '+(latest.status||'')):'Ready';
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
:currency==='CNY'?'¥':'')+v.toFixed(digits);
}
function strategyLabelHtml(name,strategyId){
 const n=name||strategyId||'-',sid=strategyId||'';
 return '<span class="strategy-name strategy-hover" data-strategy-id="'+esc(sid)+'" data-strategy-name="'+esc(n)+'">'+esc(n)+'</span>'+
  '<span class="market-tip-icon" data-strategy-id="'+esc(sid)+'" data-strategy-name="'+esc(n)+'" aria-label="'+(lang==='zh'?'查看最新价格':'View latest price')+'">i</span>';
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
     ? assetName+' '+a.symbol+' · 持仓 '+fmtPct(a.weight)+' · 最新 '+px+' · 最近交易日 '+chg
     : assetName+' '+a.symbol+' · weight '+fmtPct(a.weight)+' · latest '+px+' · last trading day '+chg
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
  const [idx,act]=await Promise.all([
   json('/api/market-data/live-indicators/'+m),
   json('/api/market-data/activity/'+m+'?limit=80')
  ]);
  const fresh=idx.available&&Number(idx.freshness_seconds||999999)<180;
  setPulse('marketPulse',fresh,idx.available&&!fresh);
  el('indexPhase').textContent=(idx.session_phase||'-')+' · '+(idx.available?localClockFromEpoch(idx.source_latest_ts):'-');
  el('indexMeta').textContent=idx.available
   ? ((lang==='zh'?'数据源 ':'Provider ')+(idx.provider||'-')+' · '+(lang==='zh'?'延迟 ':'age ')+Math.round(Number(idx.freshness_seconds||0))+'s')
   : (lang==='zh'?'暂无实时数据':'No realtime data');
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
 el('runStatus').textContent=T[lang].running+' '+x.run_id;pollRun(x.run_id);
}
async function runAll(){
 const x=await json('/api/live/run-all',{method:'POST'});
 el('runStatus').textContent=T[lang].running+' '+x.runs.map(r=>r.run_id).join(' | ');
 x.runs.forEach(r=>pollRun(r.run_id));
}
async function pollRun(id){
 for(let i=0;i<30;i++){
  await new Promise(r=>setTimeout(r,1000));
  try{
   const x=await json('/api/runs/'+id);el('runStatus').textContent=id+' · '+x.status;
   if(!['CREATED','FETCHING_DATA'].includes(x.status)){refreshAll();return}
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
 el('usReturnMaxMeta').textContent=(d.decision_id||'-')+' · '+(lang==='zh'?'冻结 ':'Frozen ')+(d.frozen_at||'-')+' · '+(d.selection_source||'-');
 el('usrmExpected').textContent=fmtPct(d.projected_annualized_expected_net_return);
 el('usrmGeneric').textContent=fmtPct(d.generic_core_projected_annualized_expected_net_return);
 el('usrmSpy').textContent=fmtPct(d.buy_hold_projected_annualized_expected_net_return);
 el('usrmRisk').textContent=fmtPct(1-Number(d.cash_residual_weight||0));
 el('usReturnMaxNote').textContent=lang==='zh'
  ? '美股主路线不使用A股反转恢复逻辑，而是在所有 ACTIVE 策略中严格选择当前预期净收益最高者；收益并列时依次用更低执行成本、风险、不确定性和固定策略ID打破平局，再展开成 SPY/QQQ/IWM/TLT/GLD 的可执行头寸。四个美元账户共享同一冻结决策，只让资金规模改变成交容量和冲击成本。'
  : 'The US primary route does not reuse the CN recovery thesis. It strictly selects the ACTIVE strategy with the highest current expected net return; exact return ties are broken by lower execution cost, risk, uncertainty, then deterministic strategy ID, before expansion into executable SPY/QQQ/IWM/TLT/GLD exposures. All four USD sleeves share the same frozen decision; only capital size changes capacity and impact.';
 const rw=d.target_strategy_weights||{};
 const gw=d.generic_core_control_weights||{};
 const sids=Array.from(new Set([...Object.keys(rw),...Object.keys(gw)])).sort();
 el('usrmStrategyRows').innerHTML=sids.map(sid=>'<tr><td>'+esc(sid)+'</td><td class="num triaid">'+fmtPct(rw[sid]||0)+'</td><td class="num">'+fmtPct(gw[sid]||0)+'</td></tr>').join('') ||
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
  '<tr><td colspan="6">'+(lang==='zh'?'上一轮尚无真实执行结果':'No realized execution result for the prior decision yet')+'</td></tr>';
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
   ? '策略池与预测顺序在登记时冻结，禁止事后换人或调参。累计收益来自后续真实策略收益；全现金只作为防守对照，不作为恢复预测能力的主要证据。'
   : 'Pool membership and predicted ordering are frozen at registration with no post-result retuning. Cumulative returns use subsequent realized strategy returns; cash is a defense control, not the primary evidence of recovery-selection skill.';
 const rows=report.strategy_determination||[];
 el('prospectiveStrategyRows').innerHTML=rows.map(x=>{
   const name=(x.name&&x.name[lang])||x.strategy_id;
   const reason=(x.selection_reason&&x.selection_reason[lang])||'';
   return '<tr>'+
    '<td><span class="strategy-name">'+esc(name)+'</span><br><span class="small muted">'+esc(x.strategy_id)+'</span></td>'+
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
   pool.map(sid=>'<th>'+esc(sid)+'</th>').join('')+
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
 el('recoveryWaveMeta').textContent=(d.decision_id||'-')+' · '+(lang==='zh'?'冻结 ':'Frozen ')+(d.frozen_at||'-')+' · '+(lang==='zh'?'源时间 ':'Source ')+(d.source_latest_ts||'-');
 el('recoveryCash').textContent=fmtPct(d.cash_residual_weight);
 el('recoveryPrevDays').textContent=review?String(review.observation_days??0):'-';
 el('recoveryPrevReturn').textContent=review?fmtPct(review.current_portfolio_cumulative_return):'-';
 el('recoveryPrevReturn').className=review?cls(Number(review.current_portfolio_cumulative_return||0)):'';
 el('recoveryPrevGap').textContent=review?signedPct(review.current_excess_vs_equal_weight):'-';
 el('recoveryPrevGap').className=review?cls(Number(review.current_excess_vs_equal_weight||0)):'';
 const scope=d.data_scope||{};
 el('recoveryWaveNote').textContent=lang==='zh'
  ? '这是 Shadow Core 的冻结研究交易意见，不生成券商订单。T 时点决策只能从下一完整可交易 bar 起计算结果；当前微观层仅使用 ETF/指数基金自身真实价格与成交量，成分股级微观数据尚未接入。四资金袖套从同一决策、全现金起步，唯一变量是资金规模；未来填单只使用后续真实成交量，成交后从下一完整 bar 才开始计收益。历史建议与参数均冻结，不能事后改写。'
  : 'These are frozen Shadow Core research opinions and generate no broker orders. A decision at T is evaluated only from the next complete tradable bar. The four capital sleeves start from cash under the same frozen decision; capital size is the only experimental variable. Future fills use only subsequent observed volume and become return-active on the following complete bar. Frozen advice and parameters cannot be rewritten after outcomes.';
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
 }).join('') || '<tr><td colspan="11">'+(lang==='zh'?'暂无冻结交易意见':'No frozen trade opinion')+'</td></tr>';
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
 }).join('') || '<tr><td colspan="7">'+(lang==='zh'?'上一轮资金袖套尚无可用真实执行结果':'No eligible realized execution result for the prior sleeves yet')+'</td></tr>';
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
 try{
  const [s,d,cards,curves,evo,runs]=await Promise.all([
   json('/api/status'),json('/api/daily?market_id='+m),json('/api/strategies?market_id='+m+'&lang='+lang),
   json('/api/curves?market_id='+m),json('/api/evolution'),json('/api/runs?market_id='+m+'&limit=100')
  ]);
  const isCNStress=m==='CN';
  const evaluated=[...runs].reverse().find(x=>
   x.evaluation&&x.evaluation.status==='EVALUATED'&&(!isCNStress||x.experiment_mode==='CN_WORST_POOL_RESCUE')
  )||null;
  if(isCNStress){
   el('resultTitle').textContent=lang==='zh'?'A股逆向压力实验结果':'CN Adversarial Stress Experiment';
   el('baseReturnLabel').textContent=lang==='zh'?'最差策略池基线收益':'Worst-pool baseline return';
   el('baseReturnSub').textContent=lang==='zh'?'故意构造的不利起点':'Intentionally adverse baseline';
   el('gainLabel').textContent=lang==='zh'?'TRIAID 挽回损失':'TRIAID loss rescue';
   el('gainSub').textContent=lang==='zh'?'TRIAID 后 − 最差策略池基线':'After TRIAID − worst-pool baseline';
   el('strategyTitle').textContent=lang==='zh'?'A股最差策略池与 TRIAID 减损调整':'CN Worst Strategy Pool and TRIAID Loss Reduction';
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
  const selected=cards.filter(x=>x.selected);
  const latest=d.runs_detail&&d.runs_detail.length?d.runs_detail[d.runs_detail.length-1]:null;
  const lastCurve=curves.length?curves[curves.length-1]:null;
  renderComparison(evaluated);
  el('date').textContent=d.date||'-';el('core').textContent=s.active_core.version;el('selectedCount').textContent=selected.length;
  const cum=lastCurve?lastCurve.cumulative_excess_return:null;el('cumExcess').textContent=fmtPct(cum);el('cumExcess').className='value '+cls(cum||0);
  el('regime').textContent=latest?.regime||'-';el('runState').textContent=latest?.status||'-';
  el('selectedNames').textContent=selected.length?selected.map(x=>x.name).slice(0,6).join('、')+(selected.length>6?' …':''):'-';
  if(evaluated){
   const g=Number(evaluated.evaluation.excess_return||0);
   el('dailyAnalysis').textContent=g>1e-12?T[lang].positive:g<-1e-12?T[lang].negativeResult:T[lang].flat;
   el('dailyAnalysis').className=cls(g);
  }else if(isCNStress&&latest?.diagnostic_summary?.experiment_mode==='CN_WORST_POOL_RESCUE'){
   const p=Number(latest.diagnostic_summary.projected_excess_expected_return);
   el('dailyAnalysis').textContent=Number.isFinite(p)
    ? (lang==='zh'?'当前信息下预计减损 '+signedPct(p)+'，实际挽回以后验为准':'Projected loss reduction '+signedPct(p)+' on current information; realized rescue awaits outcome')
    : T[lang].pending;
   el('dailyAnalysis').className=Number.isFinite(p)?cls(p):'';
  }else{el('dailyAnalysis').textContent=T[lang].pending;el('dailyAnalysis').className='';}
  renderUSReturnMax(!isCNStress?d.us_return_max:null);
  renderProspective(isCNStress?d.prospective_experiment:null);
  renderRecoveryWave(isCNStress?d.recovery_wave:null);
  drawCurve(curves);
  const selectedCards=cards.filter(x=>x.selected).sort((a,b)=>(b.baseline_weight||0)-(a.baseline_weight||0));
  const candidateCards=cards.filter(x=>!x.selected).sort((a,b)=>((b.expected_net_return??-999)-(a.expected_net_return??-999)));
  el('strategyRows').innerHTML=selectedCards.map(x=>{
   const delta=(x.triaid_weight||0)-(x.baseline_weight||0);
   const explanation=[x.summary,x.selection_reason,x.triaid_reason].filter(Boolean).join(' · ');
   return '<tr class="selected">'+
    '<td><span class="strategy-name">'+esc(x.name)+'</span><br><span class="small muted">'+esc(x.strategy_id)+'</span></td>'+
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
    '<td><span class="strategy-name">'+esc(x.name)+'</span><br><span class="small muted">'+esc(x.strategy_id)+'</span></td>'+
    '<td><span class="tag has-tip" data-tip="'+esc(statusTip(x.lifecycle))+'">'+esc(x.lifecycle||'-')+'</span></td>'+
    '<td class="num '+cls(x.expected_net_return||0)+'">'+fmtPct(x.expected_net_return)+'</td>'+
    '<td class="num">'+fmtPct(x.risk)+'</td>'+
    '<td class="reason">'+esc([x.summary,x.best_conditions].filter(Boolean).join(' · '))+'</td></tr>';
  }).join('');
  const diag=evo.diagnosis||{};el('evoObserved').textContent=diag.evaluated_runs??0;
  el('evoMean').textContent=diag.mean_excess_return===null||diag.mean_excess_return===undefined?T[lang].noResult:
    (lang==='zh'?'平均 TRIAID 增益 ':'Mean TRIAID uplift ')+signedPct(diag.mean_excess_return);
  el('evoMean').className='sub '+cls(diag.mean_excess_return||0);
  el('evoNeg').textContent=diag.negative_rate===null||diag.negative_rate===undefined?'-':fmtPct(diag.negative_rate);
  const history=evo.history||[];const last=history.length?history[history.length-1]:null;
  el('evoLast').textContent=last?(last.event+' · '+(last.version||'')):T[lang].noCandidate;
  el('runStatus').textContent=latest?((latest.market_id||m)+' · '+(latest.status||'')):'Ready';
 }catch(e){el('runStatus').textContent='UI data error: '+e.message;}
}
async function propose(){
 const x=await json('/api/evolution/propose',{method:'POST'});
 el('runStatus').textContent=x.created?(x.candidate.version+' · CANDIDATE CREATED'):(x.reason||'NO CANDIDATE');
 refreshAll();
}
const hoverTip=el('hoverTip');
document.addEventListener('mouseover',e=>{
 const target=e.target.closest('[data-tip]');
 if(!target||!target.dataset.tip)return;
 hoverTip.textContent=target.dataset.tip;hoverTip.style.display='block';
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
 const target=e.target.closest('[data-tip]');
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
