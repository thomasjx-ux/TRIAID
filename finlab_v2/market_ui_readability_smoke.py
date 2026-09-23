from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parent
app=(ROOT/"app.py").read_text(encoding="utf-8")

risk_pos=app.find('<section class="riskpanel" id="riskWarningPanel">')
us_pos=app.find('<div class="prospective-panel" id="usReturnMaxPanel"')
cn_pos=app.find('<div class="prospective-panel" id="prospectivePanel"')
recovery_pos=app.find('<div class="prospective-panel" id="recoveryWavePanel"')
overview_pos=app.find('<h2 id="overviewTitle">')

checks={
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
    "market_switch_updates_identity":"function onMarketChange(){applyMarketScope();renderMarketIdentity();" in app,
}

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_MARKET_UI_READABILITY_SMOKE_FAILED:"+",".join(failed))
print("TRIAID_MARKET_UI_READABILITY_SMOKE_PASS",len(checks))
