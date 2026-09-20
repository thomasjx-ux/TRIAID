from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from triaid_fin.contracts import OutcomeRequest, RunRequest
from triaid_fin.engine import EvolutionLabEngine

app = FastAPI(title="TRIAID FIN Evolution Lab V2", version="0.1.0")
engine = EvolutionLabEngine()


@app.get("/health")
def health() -> dict:
    return {"ok": True, **engine.status()}


@app.get("/api/status")
def status() -> dict:
    return engine.status()


@app.post("/api/run", status_code=202)
def create_run(request: RunRequest, background_tasks: BackgroundTasks) -> dict:
    run = engine.create_run(request)
    background_tasks.add_task(engine.execute, run.run_id, request)
    return {"run_id": run.run_id, "status": run.status}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        return engine.get_run(run_id).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run_id not found") from exc


@app.post("/api/runs/{run_id}/outcome")
def submit_outcome(run_id: str, outcome: OutcomeRequest) -> dict:
    try:
        return engine.submit_outcome(run_id, outcome).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run_id not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/daily")
def daily() -> dict:
    return engine.daily_summary()


@app.get("/api/curves")
def curves() -> list[dict]:
    return engine.curves()


@app.get("/api/strategies")
def strategies(lang: str = Query(default="zh", pattern="^(zh|en)$")) -> list[dict]:
    return engine.strategy_population.strategy_cards(lang)


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
body{font-family:system-ui,-apple-system,sans-serif;max-width:1080px;margin:40px auto;padding:0 20px;line-height:1.5}
.row{display:flex;gap:12px;flex-wrap:wrap}.card{border:1px solid #ddd;border-radius:12px;padding:16px;min-width:220px;flex:1}
button{padding:8px 12px}.muted{opacity:.65}pre{white-space:pre-wrap}
</style>
</head>
<body>
<div class="row"><h1 id="title">TRIAID FIN 进化实验台 V2</h1><button onclick="toggleLang()">中文 / English</button></div>
<p id="subtitle">最小模块化架构：策略群 → TRIAID Core → 结果评价 → 审计 → 连续回顾</p>
<div class="row">
  <div class="card"><div id="archLabel">架构状态</div><pre id="status">loading...</pre></div>
  <div class="card"><div id="dailyLabel">今日总结</div><pre id="daily">loading...</pre></div>
</div>
<h2 id="strategyTitle">策略说明</h2>
<div id="strategies" class="row"></div>
<script>
let lang='zh';
const text={
 zh:{title:'TRIAID FIN 进化实验台 V2',subtitle:'最小模块化架构：策略群 → TRIAID Core → 结果评价 → 审计 → 连续回顾',arch:'架构状态',daily:'今日总结',strategies:'策略说明',empty:'策略 Registry 尚未迁入。'},
 en:{title:'TRIAID FIN Evolution Lab V2',subtitle:'Minimal modular architecture: Strategy Population → TRIAID Core → Evaluation → Audit → Continuous Review',arch:'Architecture Status',daily:'Daily Summary',strategies:'Strategy Explanations',empty:'The audited strategy registry has not been migrated yet.'}
};
async function refresh(){
 const s=await fetch('/api/status').then(r=>r.json());
 const d=await fetch('/api/daily').then(r=>r.json());
 const cards=await fetch('/api/strategies?lang='+lang).then(r=>r.json());
 document.getElementById('status').textContent=JSON.stringify(s,null,2);
 document.getElementById('daily').textContent=JSON.stringify(d,null,2);
 document.getElementById('strategies').innerHTML=cards.length?cards.map(x=>'<div class="card"><h3>'+x.name+'</h3><p>'+x.summary+'</p><p class="muted">'+x.logic+'</p></div>').join(''):'<div class="card">'+text[lang].empty+'</div>';
}
function applyText(){const t=text[lang];title.textContent=t.title;subtitle.textContent=t.subtitle;archLabel.textContent=t.arch;dailyLabel.textContent=t.daily;strategyTitle.textContent=t.strategies;}
function toggleLang(){lang=lang==='zh'?'en':'zh';applyText();refresh();}
applyText();refresh();setInterval(refresh,5000);
</script>
</body>
</html>
"""
