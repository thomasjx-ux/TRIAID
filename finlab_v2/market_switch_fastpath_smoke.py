from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parent
app=(ROOT/"app.py").read_text(encoding="utf-8")
market=(ROOT/"triaid_fin"/"market_lab.py").read_text(encoding="utf-8")
projection=(ROOT/"triaid_fin"/"ui_projection.py").read_text(encoding="utf-8")

refresh_start=app.find("async function refreshAll(preferStale=false,forceServer=false)")
refresh_block=app[refresh_start:refresh_start+18000] if refresh_start>=0 else ""

live_start=app.find("async function refreshLiveWindows()")
live_end=app.find("function applyMarketScope",live_start)
live_block=app[live_start:live_end] if live_start>=0 and live_end>live_start else ""

checks={
    "frontend_request_cache":"const uiFetchCache=new Map()" in app and "async function jsonCached" in app,
    "frontend_stale_while_revalidate":"async function jsonCachedStale" in app and "return hit.data;" in app,
    "frontend_market_warmup_only_on_intent":"function warmMarketCache(m)" in app and "setTimeout(warmAllMarkets,1200)" not in app and "addEventListener('mouseenter'" in app,
    "frontend_warmup_skips_selected":"filter(m=>m!==selected)" in app and "if(document.hidden||m===el('market').value)return;" in app,
    "frontend_no_initial_heavy_fanout":"setTimeout(warmAllMarkets" not in app,
    "frontend_stale_response_guard":"seq!==refreshSeq||el('market').value!==m" in app,
    "live_window_stale_guard":"seq!==liveSeq||el('market').value!==m" in app,
    "strategy_context_async_enrichment":"jsonCached('/api/market-data/strategy-context/'+m,180000)" in app,
    "market_change_no_duplicate_selected_warm":"function onMarketChange()" in app and "warmMarketCache(el('market').value)" not in app,
    "market_change_cache_first":"refreshAll(true)" in app and "requestAnimationFrame" in app,
    "strategy_context_not_prefetched_for_all_markets":"'/api/market-data/strategy-context/'+m" not in app[app.find("function warmMarketCache"):app.find("function warmAllMarkets")],
    "strategy_context_cache_first":'cached_daily=hub.cached_panel(key,"DAILY")' in market,
    "strategy_context_external_refresh_only_on_cache_miss":'if cached_daily is None:\n        daily=fetch_panel(key,"DAILY",force=False)' in market,
    "unified_projection_endpoint":'@app.get("/api/ui/market-page/{market_id}")' in app,
    "unified_live_projection_endpoint":'@app.get("/api/ui/market-page/{market_id}/live")' in app,
    "market_switch_uses_one_projection_fetch":"const page=await marketGet(projectionUrl,30000);" in refresh_block and "projectionUrl=" in refresh_block,
    "market_switch_no_legacy_data_fanout":all(token not in refresh_block for token in (
        "/api/daily?compact=true&market_id=",
        "/api/strategies?market_id=",
        "/api/curves?market_id=",
        "/api/runs?market_id=",
    )),
    "warmup_uses_full_projection_only":"jsonCached('/api/ui/market-page/'+m+'?lang='+lang,30000)" in app,
    "live_refresh_uses_single_projection_fetch":"await jsonCached('/api/ui/market-page/'+m+'/live',2000)" in live_block,
    "live_refresh_no_scheduler_fanout":"/api/decision-scheduler/events" not in live_block and "/api/decision-scheduler/status" not in live_block,
    "projection_has_explicit_section_states":"NO_UNEXPLAINED_EMPTY_SURFACES" in projection and "WAITING_FOR_NEXT_COMPLETE_TRADING_DAY_OUTCOME" in projection,
    "projection_validates_ready_numeric_contracts":"NUMERIC_FIELDS_INCOMPLETE" in projection,
    "risk_panels_refresh_independently":"async function refreshRiskPanels()" in app and "setInterval(()=>{if(!document.hidden)refreshRiskPanels()},30000)" in app,
    "validation_uses_single_ui_projection":"async function refreshValidationSummary" in app and "/api/ui/validation-summary?market_id=" in app,
    "validation_no_domain_outcome_fanout":"/api/experiments/outcomes/" not in app[app.find("async function refreshValidationSummary"):app.find("function phaseText")],
    "validation_stale_response_guard":"seq!==refreshSeq||el('market').value!==m" in app[app.find("async function refreshValidationSummary"):app.find("function phaseText")],
    "first_paint_fast_brief":"refreshHomeBrief()" in app and "/api/ui/home-brief" in app,
    "full_refresh_low_frequency":"setInterval(refreshFullIfDue,15000)" in app and "setInterval(refreshAll,15000)" not in app,
    "live_refresh_phase_aware":"setInterval(refreshLiveIfDue,5000)" in app and "phase==='OPEN'?5000" in app,
    "hidden_tab_avoids_heavy_work":"document.addEventListener('visibilitychange'" in app and "if(document.hidden)return;" in app,
    "duplicate_full_refresh_guard":"if(fullRequestPending[m]&&!forceServer)return;" in app,
    "duplicate_live_refresh_guard":"if(liveRequestPending[m])return;" in app,
}

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_MARKET_SWITCH_FASTPATH_SMOKE_FAILED:"+",".join(failed))
print("TRIAID_MARKET_SWITCH_FASTPATH_SMOKE_PASS",len(checks))
