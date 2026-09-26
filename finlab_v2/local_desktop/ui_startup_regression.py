"""Protect a fresh desktop install from displaying a 503 payload as raw JSON.

The API's scientific 503/BLOCKED state must remain intact. The original page
is required to render other valid panels and a short human-readable status.
"""
from __future__ import annotations
import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

FIN=Path(__file__).resolve().parent.parent
source=(FIN/"app.py").read_text(encoding="utf-8")
tree=ast.parse(source)
page=None
for node in tree.body:
    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name=="home":
        for statement in node.body:
            if isinstance(statement,ast.Return) and isinstance(statement.value,ast.Constant):
                if isinstance(statement.value.value,str):
                    page=statement.value.value
        break
assert page and "<script>" in page and "</script>" in page
js=page.split("<script>",1)[1].split("</script>",1)[0]
start=js.index("async function json(")
end=js.index("async function jsonOrNull(",start)
helper=js[start:end]
assert "marketPageBlockMessage" in helper
assert "payload?.projection_scope==='FULL'" in helper
assert "r.status===503" in helper
assert "String(e?.message||e)" in js
assert "UI data error: '+e.message" not in js
assert "WAITING_FOR_FIRST_FROZEN_DECISION" in js
assert "const cards=['READY','WAITING']" in js
assert "projectionIntegrity.status==='BLOCKED'" in js

node=shutil.which("node")
if node:
    executable=String=helper + r"""
    const assert=require('node:assert/strict');
    let reply=null;
    const fetch=async ()=>reply;
    const lang='zh';
    const marketClockState={US:{session_phase:'CLOSED'}};
    const MARKET_UI={US:{zh:'美股 / US'}};
    const blocked={
      projection_scope:'FULL',
      integrity:{status:'BLOCKED',passed:false,
                 errors:['strategies:WAITING:WAITING_FOR_FIRST_FROZEN_DECISION']},
      sections:{strategies:{state:'WAITING',
                           reason:'WAITING_FOR_FIRST_FROZEN_DECISION',
                           data:[{strategy_id:'P00_BUY_HOLD',expected_net_return:null,risk:null}]}}
    };
    const response=(status,obj)=>({
      ok:status>=200&&status<300,status,
      headers:{get:()=> 'application/json'},json:async()=>obj
    });
    (async()=>{
      reply=response(503,blocked);
      const page=await json('/api/ui/market-page/US?lang=zh');
      assert.strictEqual(page,blocked,'Scientific gate must remain structured');
      const notice=marketPageBlockMessage(page,'US');
      assert(notice.includes('尚无')&&notice.includes('休市'),notice);
      assert(notice.length<260);
      const huge='X'.repeat(250000);
      reply=response(503,{detail:huge});
      await assert.rejects(
        json('/api/ui/risk-center'),
        error=>error.message.startsWith('HTTP 503:')
             &&error.message.length<160
             &&!error.message.includes(huge),
      );
      reply=response(500,{detail:'failure'});
      await assert.rejects(json('/api/status'),/HTTP 500/);
      reply=response(200,{ok:true});
      assert.equal((await json('/api/status')).ok,true);
      console.log('TRIAID_FIRST_BOOT_BROWSER_HTTP_503_PASS');
    })().catch(err=>{console.error(err);process.exit(1)});
    """
    test=subprocess.run(
        [node,"-e",executable],capture_output=True,text=True,
        timeout=25,check=False,cwd=str(FIN),
    )
    if test.returncode:
        raise SystemExit("TRIAID_FIRST_BOOT_BROWSER_FAILURE:"+test.stdout+test.stderr)
    print(test.stdout.strip())
elif os.getenv("CI"):
    raise SystemExit("TRIAID_FIRST_BOOT_BROWSER_FAILURE:CI_NODE_REQUIRED")
else:
    print("TRIAID_FIRST_BOOT_BROWSER_DYNAMIC_SKIPPED:NO_NODE")

print("TRIAID_FIRST_BOOT_UI_REGRESSION_PASS")
