from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from triaid_fin.contracts import OutcomeRequest, RunRequest
from triaid_fin.engine import EvolutionLabEngine

app = FastAPI(title="TRIAID FIN Evolution Lab V2", version="0.5.1")
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
    cards = engine.strategy_population.strategy_cards(lang, market_id)
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
canvas{width:100%;height:270px;background:#fff;border:1px solid var(--line);border-radius:12px}
.legend{display:flex;gap:18px;font-size:12px;margin:8px 0}.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px}
.tablewrap{overflow:auto;max-height:690px;border:1px solid var(--line);border-radius:12px;background:#fff}
table{width:100%;border-collapse:collapse;background:#fff}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #edf0f3;font-size:13px;vertical-align:top}
th{background:#f8fafc;position:sticky;top:0;z-index:1}.selected{background:#f6fbff}.tag{display:inline-block;padding:2px 7px;border-radius:999px;background:#eef1f5;font-size:11px}
.num{white-space:nowrap;font-variant-numeric:tabular-nums}.reason{min-width:320px;line-height:1.45}.strategy-name{font-weight:650}
.delta{font-weight:700}.corebox{display:flex;gap:12px;flex-wrap:wrap}.corebox .card{flex:1;min-width:240px}
.small{font-size:12px}.nowrap{white-space:nowrap}
@media(max-width:760px){.compare{grid-template-columns:1fr}.wrap{padding:15px}th,td{font-size:12px}.reason{min-width:240px}}
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
      <select id="market" onchange="refreshAll()"><option value="US">US</option><option value="CN">A股 / CN</option></select>
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

  <h2 id="strategyTitle">策略群与 TRIAID 调整</h2>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th id="thStrategy">策略</th>
        <th id="thState">状态</th>
        <th id="thExp">预期净回报</th>
        <th id="thRisk">风险</th>
        <th id="thBase">介入前</th>
        <th id="thTriaid">TRIAID 后</th>
        <th id="thDelta">增减</th>
        <th id="thWhy">策略说明与选择原因</th>
      </tr></thead>
      <tbody id="strategyRows"></tbody>
    </table>
  </div>
  <details style="margin-top:12px">
    <summary id="candidatePoolTitle" style="cursor:pointer;color:#748091">查看未入选候选策略池</summary>
    <div class="tablewrap" style="margin-top:10px;max-height:420px">
      <table>
        <thead><tr>
          <th>策略</th><th>状态</th><th>预期净回报</th><th>风险</th><th>说明</th>
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

<script>
let lang='zh';
const el=id=>document.getElementById(id);
const T={
 zh:{
  title:'TRIAID FIN 进化实验台 V2',subtitle:'真实市场 → 动态策略群 → TRIAID Core → 后验验证 → 持续进化',
  result:'TRIAID 结果比较',baseReturn:'策略群基线收益',triaidReturn:'TRIAID 后收益',gain:'TRIAID 增益',
  baseSub:'介入前',triaidSub:'介入后',gainSub:'TRIAID 后收益 − 策略群基线',
  overview:'当前状态',date:'最新数据日',core:'当前 Core',selected:'当前策略数',cum:'累计 TRIAID 超额',
  curve:'连续回顾',legendBase:'策略群基线',legendTriaid:'TRIAID',
  daily:'今日摘要',regime:'市场状态',runState:'运行状态',selectedNames:'当前入选',analysis:'今日结论',
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
  baseSub:'Before intervention',triaidSub:'After intervention',gainSub:'After TRIAID − strategy-group baseline',
  overview:'Current State',date:'Latest market date',core:'Active Core',selected:'Selected strategies',cum:'Cumulative TRIAID excess',
  curve:'Continuous Review',legendBase:'Strategy-group baseline',legendTriaid:'TRIAID',
  daily:'Daily Summary',regime:'Market regime',runState:'Run status',selectedNames:'Selected now',analysis:'Daily conclusion',
  strategies:'Current Strategy Group and TRIAID Adjustments',candidatePool:'View unselected candidate pool',strategy:'Strategy',state:'State',exp:'Expected net return',risk:'Risk',
  before:'Before',after:'After TRIAID',delta:'Change',why:'Strategy explanation and selection reason',
  evolution:'Core Evolution State',observed:'Verified decisions',negative:'Negative-contribution rate',next:'Next step',
  noEval:'Awaiting next-period outcome',noResult:'No evaluated outcome yet',evoNote:'Core forms candidate improvements from continuously verified results',
  noCandidate:'No Candidate yet',propose:'Generate Candidate Core',run:'Run Now',runAll:'Run Both Markets',
  running:'Run created. Real market data is being processed in the background.',pending:'Current decision is ready and awaiting the next market outcome.',
  positive:'TRIAID produced positive uplift in the latest evaluated run',negativeResult:'TRIAID produced negative uplift; intervention attribution should be reviewed',flat:'TRIAID is approximately in line with baseline'
 }
};
function fmtPct(x){return x===null||x===undefined?'-':(100*x).toFixed(2)+'%'}
function signedPct(x){if(x===null||x===undefined)return '-';const v=100*x;return (v>0?'+':'')+v.toFixed(2)+'%'}
function cls(x){return x>1e-12?'good':x<-1e-12?'bad':''}
function esc(x){return String(x??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function applyText(){
 const t=T[lang];
 const map={title:'title',subtitle:'subtitle',resultTitle:'result',baseReturnLabel:'baseReturn',triaidReturnLabel:'triaidReturn',gainLabel:'gain',
 baseReturnSub:'baseSub',triaidReturnSub:'triaidSub',gainSub:'gainSub',overviewTitle:'overview',dateLabel:'date',coreLabel:'core',
 selectedLabel:'selected',cumLabel:'cum',curveTitle:'curve',legendBase:'legendBase',legendTriaid:'legendTriaid',dailyTitle:'daily',
 candidatePoolTitle:'candidatePool',
 regimeLabel:'regime',runStateLabel:'runState',selectedNamesLabel:'selectedNames',dailyAnalysisLabel:'analysis',strategyTitle:'strategies',
 thStrategy:'strategy',thState:'state',thExp:'exp',thRisk:'risk',thBase:'before',thTriaid:'after',thDelta:'delta',thWhy:'why',
 evolutionTitle:'evolution',evoObservedLabel:'observed',evoNegLabel:'negative',evoCandidateLabel:'next',evoNote:'evoNote',
 proposeBtn:'propose',runBtn:'run',runAllBtn:'runAll'};
 Object.entries(map).forEach(([id,key])=>el(id).textContent=t[key]);
}
async function json(url,opts){const r=await fetch(url,opts);if(!r.ok)throw new Error(await r.text());return r.json()}
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
async function refreshAll(){
 const m=el('market').value;
 try{
  const [s,d,cards,curves,evo,runs]=await Promise.all([
   json('/api/status'),json('/api/daily?market_id='+m),json('/api/strategies?market_id='+m+'&lang='+lang),
   json('/api/curves?market_id='+m),json('/api/evolution'),json('/api/runs?market_id='+m+'&limit=100')
  ]);
  const evaluated=[...runs].reverse().find(x=>x.evaluation&&x.evaluation.status==='EVALUATED')||null;
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
  }else{el('dailyAnalysis').textContent=T[lang].pending;el('dailyAnalysis').className='';}
  drawCurve(curves);
  const selectedCards=cards.filter(x=>x.selected).sort((a,b)=>(b.baseline_weight||0)-(a.baseline_weight||0));
  const candidateCards=cards.filter(x=>!x.selected).sort((a,b)=>((b.expected_net_return??-999)-(a.expected_net_return??-999)));
  el('strategyRows').innerHTML=selectedCards.map(x=>{
   const delta=(x.triaid_weight||0)-(x.baseline_weight||0);
   const explanation=[x.summary,x.selection_reason,x.triaid_reason].filter(Boolean).join(' · ');
   return '<tr class="selected">'+
    '<td><span class="strategy-name">'+esc(x.name)+'</span><br><span class="small muted">'+esc(x.strategy_id)+'</span></td>'+
    '<td><span class="tag">'+esc(x.lifecycle||'-')+'</span></td>'+
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
    '<td><span class="tag">'+esc(x.lifecycle||'-')+'</span></td>'+
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
function toggleLang(){lang=lang==='zh'?'en':'zh';applyText();refreshAll()}
applyText();refreshAll();setInterval(refreshAll,15000);
</script>
</body>
</html>
"""
