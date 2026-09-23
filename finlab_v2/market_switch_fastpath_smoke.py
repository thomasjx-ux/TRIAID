from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parent
app=(ROOT/"app.py").read_text(encoding="utf-8")
market=(ROOT/"triaid_fin"/"market_lab.py").read_text(encoding="utf-8")

checks={
    "frontend_request_cache":"const uiFetchCache=new Map()" in app and "async function jsonCached" in app,
    "frontend_stale_while_revalidate":"async function jsonCachedStale" in app and "return hit.data;" in app,
    "frontend_market_warmup":"function warmAllMarkets()" in app and "setTimeout(warmAllMarkets,1200)" in app,
    "frontend_warmup_is_staggered":"queue.forEach((m,i)=>setTimeout(()=>warmMarketCache(m),1200+(i*1400)))" in app,
    "frontend_warmup_skips_selected":"filter(m=>m!==selected)" in app,
    "frontend_stale_response_guard":"seq!==refreshSeq||el('market').value!==m" in app,
    "live_window_stale_guard":"seq!==liveSeq||el('market').value!==m" in app,
    "strategy_context_not_in_live_promiseall":"const [idx,act,strategyCtx]=await Promise.all" not in app,
    "strategy_context_async_enrichment":"jsonCached('/api/market-data/strategy-context/'+m,60000)" in app,
    "market_change_no_duplicate_selected_warm":"function onMarketChange()" in app and "warmMarketCache(el('market').value)" not in app,
    "market_change_cache_first":"refreshAll(true)" in app and "requestAnimationFrame" in app,
    "strategy_context_not_prefetched_for_all_markets":"'/api/market-data/strategy-context/'+m" not in app[app.find("function warmMarketCache"):app.find("function warmAllMarkets")],
    "strategy_context_cache_first":"cached_daily=hub.cached_panel(key,\"DAILY\")" in market,
    "strategy_context_external_refresh_only_on_cache_miss":"if cached_daily is None:\n        daily=fetch_panel(key,\"DAILY\",force=False)" in market,
    "compact_daily_ui_path":"compact=true&market_id=" in app and "compact: bool = Query(default=False)" in app,
    "lightweight_core_endpoint":'@app.get("/api/ui/core")' in app,
    "market_switch_not_blocked_by_risk_panels":"const [s,d,cards,curves,evo,runs,previewRun]=await Promise.all" in app,
    "risk_panels_refresh_independently":"async function refreshRiskPanels()" in app and "setInterval(refreshRiskPanels,10000)" in app,
    "live_indicator_not_blocked_by_activity":"const idx=await idxPromise;" in app and "const act=await actPromise;" in app,
}

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_MARKET_SWITCH_FASTPATH_SMOKE_FAILED:"+",".join(failed))
print("TRIAID_MARKET_SWITCH_FASTPATH_SMOKE_PASS",len(checks))
