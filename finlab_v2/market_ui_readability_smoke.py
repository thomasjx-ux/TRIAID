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
overview_pos=app.find('id="overviewTitle"')

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
    "compact_status_stays_with_market_context":overview_pos>hero_pos>0 and risk_pos>overview_pos and 'id="currentStatusOverview"' in app and 'class="compat-heading"' in app,
    "risk_follows_market_specific_route":risk_pos>max(us_pos,cn_pos,recovery_pos),
    "all_table_headers_auto_tooltipped":"root.querySelectorAll('table th').forEach" in app,
    "dynamic_headers_rechecked":"MutationObserver" in app and "applyTableHeaderTooltips()" in app,
    "risk_header_specific_tips":"'252日回撤压力'" in app and "'动量异常分位'" in app and "'风险敞口倍率候选'" in app,
    "market_clock_hover_explanations":app.count('data-clock-market=')>=3 and "data-tip=" in app,
    "three_market_label_correct":"执行两个市场" not in app and "预览三个市场" in app,
    "readability_row_hover":"tbody tr:hover td" in app,
    "status_cards_are_compact":".status-overview-grid{display:grid;grid-template-columns:repeat(12" in app and "min-height:72px" in app,
    "empty_statuses_are_humanized":"暂无入选" in app and "暂无新数据" in app and "待后验" in app,
    "market_switch_updates_identity":"function onMarketChange()" in app and "applyMarketScope();" in app and "renderMarketIdentity();" in app,
    "market_switch_preserves_fast_render":"refreshAll(true)" in app and "requestAnimationFrame" in app,
    "ambiguous_waiting_status_removed":"||(zh?'等待状态':'Awaiting status')" not in app and "||(zh?'等待状态':'Awaiting regime')" not in app,
    "session_and_strategy_regime_separated":"前半段是官方交易时段，后半段是最近一次正式决策使用的策略环境" in app,
    "intraday_without_frozen_run_is_explicit":"当日监控 · 等待收盘冻结" in app and "正式日线决策要等完整收盘数据后冻结；不是系统停止" in app,
    "intraday_candidates_not_mislabeled_frozen":"显示当前盘中候选策略群" in app and "当前候选" in app,
    "posterior_copy_requires_real_frozen_run":"当前仅盘中监控，正式决策尚未冻结。" in app and "尚无可验证的正式决策或后验结果。" in app,
    "initial_clock_and_brief_render_without_double_full":"refreshHomeBrief()" in app and "setTimeout(()=>{if(!document.hidden)refreshAll(true)},1400)" in app and "if(!hadClock)refreshAll(true)" not in app,
}

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_MARKET_UI_READABILITY_SMOKE_FAILED:"+",".join(failed))
print("TRIAID_MARKET_UI_READABILITY_SMOKE_PASS",len(checks))
