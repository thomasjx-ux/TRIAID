from __future__ import annotations

import re
from pathlib import Path

ROOT=Path(__file__).resolve().parent
app=(ROOT/"app.py").read_text(encoding="utf-8")

def norm(text:str)->str:
    return re.sub(r"[\s：:]","",text or "").strip()

headers=[
    re.sub(r"<[^>]+>","",m.group(1),flags=re.S).strip()
    for m in re.finditer(r"<th(?:\s[^>]*)?>(.*?)</th>",app,re.S)
]
headers=[re.sub(r"\s+"," ",x).strip() for x in headers if x.strip()]
static=sorted({x for x in headers if "'+" not in x})
dynamic=sorted({x for x in headers if "'+" in x})

tip_start=app.find("const TABLE_HEADER_TIPS=")
tip_end=app.find("function tableHeaderTipMeta",tip_start)
if tip_start<0 or tip_end<0:
    raise SystemExit("TRIAID_UI_TABLE_CONTRACT_FAILED:TIP_DICTIONARY_NOT_FOUND")
tip_block=app[tip_start:tip_end]
tip_keys={norm(x) for x in re.findall(r"'([^']+)'\s*:",tip_block)}
missing=[x for x in static if norm(x) not in tip_keys]

allowed_dynamic=[
    "strategyLabelHtml(",
    "交易日':'Trading day",
    "最差池':'Worst pool",
    "差值':'Gap",
]
unexpected_dynamic=[
    x for x in dynamic
    if not any(token in x for token in allowed_dynamic)
]

checks={
    "all_static_headers_have_explicit_semantic_tooltip":not missing,
    "dynamic_header_patterns_are_known":not unexpected_dynamic,
    "all_table_headers_receive_hover_marker":"root.querySelectorAll('table th').forEach" in app and "tip-mark" in app,
    "dynamic_headers_reaudited":"MutationObserver" in app and "applyTableHeaderTooltips()" in app,
    "tooltip_contract_marks_explicitness":"th.dataset.tipContract=meta.explicit?'explicit':'fallback'" in app,
    "risk_scale_matches_backend_low":"if(n>=25)return 'elevated';" in app,
    "risk_scale_matches_backend_high":"if(n>=45)return 'high';" in app,
    "risk_scale_matches_backend_severe":"if(n>=65)return 'severe';" in app,
    "risk_scale_matches_backend_critical":"if(n>=80)return 'critical';" in app,
    "risk_legend_visible":'id="riskColorLegend"' in app and "低 0–24.9" in app and "临界 80–100" in app,
    "overall_risk_colored":"setRiskScoreNode('riskOverallScore'" in app and "setRiskScoreNode('riskOverallMini'" in app,
    "horizon_risk_colored":"setRiskScoreNode('risk'+h" in app,
    "subscore_risk_colored":"setRiskScoreNode(id,n,'/100',subLabels[k]" in app,
    "market_risk_cells_colored":"riskCellHtml(fmtPct(x.drawdown_stress_252)" in app and "riskCellHtml(fmtPct(x.volatility_63)" in app,
    "dynamics_risk_colored":"Dynamics-node risk intensity" in app,
    "macro_percentile_colored":"Historical state percentile for this risk factor" in app,
    "risk_stage_has_hover_explanation":"riskStageTip(stage)" in app,
    "table_hover_has_usefulness_layer":"function tooltipUseGuide(label)" in app and "'用途：" in app and "tooltipUseGuide(raw)" in app,
    "ui_hover_has_usefulness_layer":"function uiTipUseGuide(id)" in app and "uiTipUseGuide(id)" in app,
    "status_hover_has_next_action":"function statusActionGuide(status)" in app and "statusActionGuide(status)" in app,
    "strategy_hover_has_next_action":"不要仅凭单日涨跌直接改策略权重" in app,
    "market_clock_hover_is_actionable":"确认当前页面是不是处于官方交易时段" in app and "午休或休市本身不代表系统异常" in app,
    "risk_hover_is_action_oriented":"function riskActionGuide(score)" in app and "当前动作：进入专项风险复核" in app and "当前动作：进入最高级人工复核" in app,
    "risk_hover_does_not_dump_thresholds":"统一阈值：低<25" not in app[app.find("function riskScaleTip"):app.find("function setRiskScoreNode")],
    "rationale_hover_has_usefulness":"这里的样本数和理由用于判断历史相似状态" in app,
}

failed=[name for name,ok in checks.items() if not ok]
if missing:
    failed.append("missing_explicit_header_tips="+",".join(missing))
if unexpected_dynamic:
    failed.append("unexpected_dynamic_headers="+",".join(unexpected_dynamic))

if failed:
    raise SystemExit("TRIAID_UI_TABLE_CONTRACT_FAILED:"+"|".join(failed))

print(
    "TRIAID_UI_TABLE_CONTRACT_PASS",
    {
        "static_headers":len(static),
        "dynamic_header_patterns":len(dynamic),
        "checks":len(checks),
    },
)
