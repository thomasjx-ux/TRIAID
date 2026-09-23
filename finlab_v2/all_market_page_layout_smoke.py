from __future__ import annotations

from pathlib import Path
from app import home

ROOT=Path(__file__).resolve().parent
source=(ROOT/"app.py").read_text(encoding="utf-8")
html=home()

required_markets=("US","CN","HK")
checks={
    "market_ui_has_all_three":all(f"{m}:{{" in source for m in required_markets),
    "all_three_have_route_modes":all(x in source for x in ("US_RETURN_MAX_CAPACITY","CN_RETURN_MAX_CAPACITY","HK_RETURN_MAX_CAPACITY")),
    "all_three_have_scope_copy":all(x in source for x in (
        "美股页面分两层",
        "A股页面以 CN_RETURN_MAX_CAPACITY 为主路线",
        "港股使用独立 HK_RETURN_MAX_CAPACITY 路线",
    )),
    "all_three_have_market_specific_stage_titles":all(x in source for x in (
        "美股通用 Core 对照决策",
        "A股收益优先决策",
        "港股收益优先决策",
        "美股 Return-Max 主路线与可实现性",
        "A股前瞻、恢复波段与容量",
        "港股独立路线与可实现性",
    )),
    "scope_banner_is_not_hk_only":"style.display='block'" in source and "meta.scopeZh" in source,
    "flow_labels_are_market_driven":"meta.decisionZh" in source and "meta.validationZh" in source and "meta.routeStageZh" in source,
    "shared_route_overview_present":'id="marketRouteOverview"' in html and "renderMarketRouteOverview" in source,
    "us_route_panel_present":'id="usReturnMaxPanel"' in html,
    "cn_route_panels_present":'id="prospectivePanel"' in html and 'id="recoveryWavePanel"' in html,
    "hk_route_panel_present":'id="hkRoutePanel"' in html and "renderHKRoutePanel" in source,
    "hk_route_panel_only_for_hk":"if(m!=='HK')" in source,
    "hk_assets_are_explicit":all(x in source for x in ("2800.HK","2828.HK","3033.HK","2819.HK")),
    "hk_capacity_gap_is_explicit":"Dedicated HK capacity posterior ledger not yet formed" in source and "不借用US/CN结果" in source,
    "market_switch_relabels_flow":"applyMarketScope();" in source and "renderMarketIdentity();" in source and "applyFlowLabels();" in source,
    "all_market_pages_keep_six_stage_chain":all(f'id="{x}"' in html for x in (
        "stageMarket","stageDecision","stageValidation","stageRoute","stageRisk","stageEvolution"
    )),
    "all_market_pages_keep_system_logs_collapsed":'<details class="ops-details" id="systemOpsPanel">' in html,
}
failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_ALL_MARKET_LAYOUT_FAILED:"+"|".join(failed))
print("TRIAID_ALL_MARKET_LAYOUT_PASS",{"checks":len(checks),"markets":list(required_markets)})
