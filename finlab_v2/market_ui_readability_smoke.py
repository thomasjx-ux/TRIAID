from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parent
app=(ROOT/"app.py").read_text(encoding="utf-8")

summary_pos=app.find('<section class="home-summary" id="homeSummary"')
clock_pos=app.find('<div class="market-clock-strip" id="marketClockStrip"')
hero_pos=app.find('<section class="market-hero" id="marketHero">')
risk_pos=app.find('<section class="riskpanel" id="riskWarningPanel">')
us_pos=app.find('<div class="prospective-panel" id="usReturnMaxPanel"')
cn_pos=app.find('<div class="prospective-panel" id="prospectivePanel"')
recovery_pos=app.find('<div class="prospective-panel" id="recoveryWavePanel"')
overview_pos=app.find('<h2 id="overviewTitle">')

checks={
    "first_screen_summary_present":summary_pos>0 and 'id="homeSummaryTitle"' in app and 'id="homeSummaryPurpose"' in app,
    "summary_precedes_market_detail":summary_pos<clock_pos<hero_pos,
    "summary_explains_goal":"选择—调整—验证—进化" in app and "可实现净收益" in app,
    "summary_marks_research_only":'研究 / Shadow · 不连接券商 · 不自动交易' in app,
    "summary_has_four_questions":all(x in app for x in ("homeSummaryMarket","homeSummaryDecision","homeSummaryValidation","homeSummaryRisk")),
    "summary_updates_on_market_switch":"renderHomeSummary();" in app and "homeSummaryState.selectedCount=selected.length" in app,
    "summary_uses_real_posterior":"homeSummaryState.evaluated=evaluated" in app and "Only realized market outcomes" in app,
    "summary_uses_risk_semantics":"homeSummaryState.riskScore" in app and "riskScaleTip(score" in app,
    "three_market_clock_cards":all(f'data-clock-market="{m}"' in app for m in ("US","CN","HK")),
    "official_clock_endpoint":'@app.get("/api/ui/market-clocks")' in app,
    "official_session_phase_used":"official_session_phase(market_id,local_now)" in app,
    "trading_calendar_day_used":"trading_day_info(market_id,local_now)" in app,
    "green_only_for_open":"is_open" in app and "phase==\"OPEN\"" in app,
    "market_identity_panel":'id="marketHero"' in app and 'id="selectedMarketName"' in app,
    "selected_market_first":risk_pos>0 and us_pos>0 and cn_pos>0 and recovery_pos>0 and risk_pos>max(us_pos,cn_pos,recovery_pos),
    "risk_before_generic_status":overview_pos>risk_pos>0,
    "all_table_headers_auto_tooltipped":"root.querySelectorAll('table th').forEach" in app,
    "dynamic_headers_rechecked":"MutationObserver" in app and "applyTableHeaderTooltips()" in app,
    "risk_header_specific_tips":"'252日回撤压力'" in app and "'动量异常分位'" in app and "'风险敞口倍率候选'" in app,
    "market_clock_hover_explanations":app.count('data-clock-market=')>=3 and "data-tip=" in app,
    "three_market_label_correct":"执行两个市场" not in app and "预览三个市场" in app,
    "readability_row_hover":"tbody tr:hover td" in app,
    "market_switch_updates_identity":"function onMarketChange()" in app and "applyMarketScope();" in app and "renderMarketIdentity();" in app,
    "market_switch_preserves_fast_render":"refreshAll(true)" in app and "requestAnimationFrame" in app,
}

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_MARKET_UI_READABILITY_SMOKE_FAILED:"+",".join(failed))
print("TRIAID_MARKET_UI_READABILITY_SMOKE_PASS",len(checks))
