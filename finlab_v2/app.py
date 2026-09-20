from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from triaid_fin.contracts import OutcomeRequest, RunRequest
from triaid_fin.engine import EvolutionLabEngine

app = FastAPI(title="TRIAID FIN Evolution Lab V2", version="0.4.1")
engine = EvolutionLabEngine()


@app.get("/health")
def health() -> dict:
    return {"ok": True, **engine.status()}


@app.get("/api/status")
def status() -> dict:
    return engine.status()


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


@app.get("/api/strategies")
def strategies(
    lang: str = Query(default="zh", pattern="^(zh|en)$"),
    market_id: str | None = Query(default=None),
) -> list[dict]:
    cards = engine.strategy_population.strategy_cards(lang)
    latest_run = engine.latest_run(market_id) if market_id else None
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
:root{font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:#16181d;background:#f6f7f9}
*{box-sizing:border-box}body{margin:0}.wrap{max-width:1240px;margin:auto;padding:24px}
.top{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}
h1{margin:0;font-size:28px}h2{margin-top:30px;font-size:20px}h3{margin:0 0 8px;font-size:16px}
.muted{color:#68707c}.toolbar{display:flex;gap:8px;flex-wrap:wrap}
button,select{border:1px solid #cfd3da;background:#fff;border-radius:9px;padding:9px 13px;cursor:pointer}
button.primary{background:#16181d;color:#fff;border-color:#16181d}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{background:#fff;border:1px solid #e1e4e8;border-radius:12px;padding:15px}
.kpi{font-size:24px;margin-top:6px}.small{font-size:12px}.good{color:#0b7a3e}.bad{color:#b42318}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e1e4e8}
th,td{text-align:left;padding:9px;border-bottom:1px solid #eceef1;font-size:13px;vertical-align:top}
th{background:#fafafa;position:sticky;top:0}.tablewrap{overflow:auto;max-height:650px;border-radius:12px}
.tag{display:inline-block;padding:2px 7px;border-radius:999px;background:#eef0f3;font-size:11px}
.selected{background:#eaf7ef}.weights{white-space:nowrap}.reason{min-width:260px}
canvas{width:100%;height:280px;background:#fff;border:1px solid #e1e4e8;border-radius:12px}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px;margin:0}
.statusline{padding:10px 12px;border-radius:9px;background:#fff;border:1px solid #e1e4e8;margin-top:12px}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1 id="title">TRIAID FIN 进化实验台 V2</h1>
      <div class="muted" id="subtitle">真实市场 → 动态策略群 → TRIAID Core → 后验评价 → 诊断 → Core 进化</div>
    </div>
    <div class="toolbar">
      <select id="market" onchange="refreshAll()"><option value="US">US</option><option value="CN">A股 / CN</option></select>
      <button class="primary" onclick="runNow()" id="runBtn">立即执行</button>
      <button onclick="runAll()" id="runAllBtn">执行两个市场</button>
      <button onclick="toggleLang()">中文 / English</button>
    </div>
  </div>
  <div class="statusline" id="runStatus">Ready</div>

  <h2 id="overviewTitle">系统与今日状态</h2>
  <div class="grid">
    <div class="card"><div class="muted" id="archLabel">架构版本</div><div class="kpi" id="arch">-</div></div>
    <div class="card"><div class="muted" id="coreLabel">当前 Core</div><div class="kpi" id="core">-</div></div>
    <div class="card"><div class="muted" id="dateLabel">最新数据日</div><div class="kpi" id="date">-</div></div>
    <div class="card"><div class="muted" id="excessLabel">累计 TRIAID 超额</div><div class="kpi" id="excess">-</div></div>
  </div>

  <h2 id="curveTitle">连续回顾曲线</h2>
  <canvas id="curve" width="1180" height="280"></canvas>

  <h2 id="dailyTitle">每日总结与详细分析</h2>
  <div class="card"><pre id="daily">-</pre></div>

  <h2 id="strategyTitle">策略群与每个策略解释</h2>
  <div class="tablewrap">
  <table>
    <thead><tr>
      <th id="thStrategy">策略</th><th id="thState">状态</th><th id="thExp">预期净回报</th>
      <th id="thRisk">风险</th><th id="thWeight">群组 → TRIAID</th><th id="thWhy">是什么 / 为什么选 / TRIAID为什么调</th>
    </tr></thead>
    <tbody id="strategyRows"></tbody>
  </table>
  </div>

  <h2 id="evolutionTitle">Core 进化</h2>
  <div class="grid">
    <div class="card"><h3 id="diagLabel">诊断</h3><pre id="evolution">-</pre></div>
    <div class="card"><h3 id="gateLabel">进化门</h3><div id="gateText" class="muted"></div><br><button onclick="propose()" id="proposeBtn">生成 Candidate Core</button></div>
  </div>
</div>
<script>
let lang='zh';
const T={
 zh:{title:'TRIAID FIN 进化实验台 V2',subtitle:'真实市场 → 动态策略群 → TRIAID Core → 后验评价 → 诊断 → Core 进化',
 overview:'系统与今日状态',arch:'架构版本',core:'当前 Core',date:'最新数据日',excess:'累计 TRIAID 超额',
 curve:'连续回顾曲线',daily:'每日总结与详细分析',strategies:'策略群与每个策略解释',
 strategy:'策略',state:'状态',exp:'预期净回报',risk:'风险',weight:'群组 → TRIAID',why:'是什么 / 为什么选 / TRIAID为什么调',
 evolution:'Core 进化',diag:'诊断',gate:'进化门',gateText:'Candidate 只有 Replay、Holdout、Shadow、Audit 四项全部通过才能晋升。',
 run:'立即执行',runAll:'执行两个市场',ready:'Ready',running:'已创建运行，后台正在读取真实市场数据。'},
 en:{title:'TRIAID FIN Evolution Lab V2',subtitle:'Real market → Dynamic strategy population → TRIAID Core → Outcome evaluation → Diagnosis → Core evolution',
 overview:'System and Daily State',arch:'Architecture',core:'Active Core',date:'Latest Market Date',excess:'Cumulative TRIAID Excess',
 curve:'Continuous Review Curves',daily:'Daily Summary and Detailed Analysis',strategies:'Strategy Group and Explanations',
 strategy:'Strategy',state:'State',exp:'Expected Net Return',risk:'Risk',weight:'Group → TRIAID',why:'What it is / Why selected / Why TRIAID changed it',
 evolution:'Core Evolution',diag:'Diagnosis',gate:'Evolution Gate',gateText:'A Candidate can be promoted only after Replay, Holdout, Shadow and Audit all pass.',
 run:'Run Now',runAll:'Run Both Markets',ready:'Ready',running:'Run created. Real market data is being processed in the background.'}
};
function fmtPct(x){return x===null||x===undefined?'-':(100*x).toFixed(2)+'%'}
function esc(x){return String(x??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
const el=id=>document.getElementById(id);
function applyText(){
 const t=T[lang];
 el('title').textContent=t.title;el('subtitle').textContent=t.subtitle;el('overviewTitle').textContent=t.overview;
 el('archLabel').textContent=t.arch;el('coreLabel').textContent=t.core;el('dateLabel').textContent=t.date;el('excessLabel').textContent=t.excess;
 el('curveTitle').textContent=t.curve;el('dailyTitle').textContent=t.daily;el('strategyTitle').textContent=t.strategies;
 el('thStrategy').textContent=t.strategy;el('thState').textContent=t.state;el('thExp').textContent=t.exp;el('thRisk').textContent=t.risk;el('thWeight').textContent=t.weight;el('thWhy').textContent=t.why;
 el('evolutionTitle').textContent=t.evolution;el('diagLabel').textContent=t.diag;el('gateLabel').textContent=t.gate;el('gateText').textContent=t.gateText;
 el('runBtn').textContent=t.run;el('runAllBtn').textContent=t.runAll;
}
async function json(url,opts){const r=await fetch(url,opts);if(!r.ok)throw new Error(await r.text());return r.json()}
async function runNow(){
 const m=el('market').value;const x=await json('/api/live/run/'+m,{method:'POST'});el('runStatus').textContent=T[lang].running+' '+x.run_id;pollRun(x.run_id);
}
async function runAll(){
 const x=await json('/api/live/run-all',{method:'POST'});el('runStatus').textContent=T[lang].running+' '+x.runs.map(r=>r.run_id).join(' | ');
 x.runs.forEach(r=>pollRun(r.run_id));
}
async function pollRun(id){
 for(let i=0;i<30;i++){await new Promise(r=>setTimeout(r,1000));try{const x=await json('/api/runs/'+id);el('runStatus').textContent=id+' · '+x.status;if(!['CREATED','FETCHING_DATA'].includes(x.status)){refreshAll();return}}catch(e){}}
}
function drawCurve(points){
 const c=document.getElementById('curve'),g=c.getContext('2d');g.clearRect(0,0,c.width,c.height);
 g.font='12px system-ui';g.fillStyle='#68707c';g.fillText('Baseline / TRIAID',12,18);
 if(!points.length){g.fillText(lang==='zh'?'等待连续验证数据':'Waiting for continuous verified outcomes',12,45);return}
 const vals=points.flatMap(p=>[p.baseline_equity,p.triaid_equity]);let lo=Math.min(...vals),hi=Math.max(...vals);if(hi-lo<1e-8){hi+=.01;lo-=.01}
 const X=i=>30+(c.width-55)*i/Math.max(1,points.length-1);const Y=v=>25+(c.height-50)*(hi-v)/(hi-lo);
 g.strokeStyle='#d8dce2';g.beginPath();g.moveTo(30,c.height-25);g.lineTo(c.width-20,c.height-25);g.stroke();
 [['baseline_equity','#69707d'],['triaid_equity','#111827']].forEach(([key,color])=>{g.strokeStyle=color;g.lineWidth=2;g.beginPath();points.forEach((p,i)=>{const x=X(i),y=Y(p[key]);i?g.lineTo(x,y):g.moveTo(x,y)});g.stroke()});
}
async function refreshAll(){
 const m=el('market').value;
 try{
 const [s,d,cards,curves,evo]=await Promise.all([
  json('/api/status'),json('/api/daily?market_id='+m),json('/api/strategies?market_id='+m+'&lang='+lang),json('/api/curves?market_id='+m),json('/api/evolution')
 ]);
 el('arch').textContent=s.architecture_version;el('core').textContent=s.active_core.version;el('date').textContent=d.date||'-';
 const last=curves.length?curves[curves.length-1]:null;const ex=last?last.cumulative_excess_return:null;
 el('excess').textContent=fmtPct(ex);el('excess').className='kpi '+(ex>0?'good':ex<0?'bad':'');
 el('daily').textContent=JSON.stringify(d,null,2);el('evolution').textContent=JSON.stringify({active_version:evo.active_version,diagnosis:evo.diagnosis,history:(evo.history||[]).slice(-5)},null,2);
 drawCurve(curves);
 el('strategyRows').innerHTML=cards.map(x=>{
  const cls=x.selected?'selected':'';
  const explain=[x.summary,x.best_conditions,x.selection_reason,x.triaid_reason].filter(Boolean).join('\\n');
  return '<tr class="'+cls+'"><td><b>'+esc(x.name)+'</b><br><span class="small muted">'+esc(x.strategy_id)+'</span></td><td><span class="tag">'+esc(x.lifecycle||'-')+'</span></td><td>'+fmtPct(x.expected_net_return)+'</td><td>'+fmtPct(x.risk)+'</td><td class="weights">'+fmtPct(x.baseline_weight)+' → '+fmtPct(x.triaid_weight)+'</td><td class="reason">'+esc(explain).replace(/\\n/g,'<br>')+'</td></tr>'
 }).join('');
 }catch(e){el('runStatus').textContent='UI data error: '+e.message;}
}
async function propose(){
 const x=await json('/api/evolution/propose',{method:'POST'});el('runStatus').textContent=JSON.stringify(x);refreshAll();
}
function toggleLang(){lang=lang==='zh'?'en':'zh';applyText();refreshAll()}
applyText();refreshAll();setInterval(refreshAll,15000);
</script>
</body>
</html>
"""
