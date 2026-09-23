from __future__ import annotations

import re
from pathlib import Path

ROOT=Path(__file__).resolve().parent
app=(ROOT/"app.py").read_text(encoding="utf-8")

def block(start:str,end:str)->str:
    s=app.find(start)
    e=app.find(end,s)
    if s<0 or e<0:
        raise SystemExit(f"TRIAID_UI_COPY_QUALITY_FAILED:BLOCK_NOT_FOUND:{start}")
    return app[s:e]

ui_tips=block("const UI_TIPS=","function uiTipUseGuide")
zh=ui_tips[ui_tips.find(" zh:{"):ui_tips.find(" en:{")]
ui_ids=set(re.findall(r"^\s{2}([A-Za-z0-9_]+):",zh,flags=re.M))

ui_guide=block("function uiTipUseGuide","function applyUiTooltips")
classified=set()
for raw in re.findall(r"new Set\(\[([^\]]*)\]\)",ui_guide):
    classified.update(re.findall(r"'([^']+)'",raw))

table_guide=block("function tooltipUseGuide","function tableHeaderTipMeta")
status_guide=block("function statusActionGuide","function statusTip")
risk_dynamic=block("function riskDriverSummary","function setRiskScoreNode")

banned_vague=[
    "帮助判断",
    "继续观察",
    "具体口径以",
    "本列展示",
    "综合判断",
]
vague_hits=[x for x in banned_vague if x in ui_guide or x in table_guide or x in status_guide]

checks={
    "all_ui_help_has_specific_category":ui_ids==classified,
    "no_unclassified_ui_help":not (ui_ids-classified),
    "no_dead_generic_risk_action_template":"function riskActionGuide" not in app,
    "no_vague_help_phrases":not vague_hits,
    "ui_help_uses_operational_checks":all(x in ui_guide for x in (
        "基线权重→TRIAID权重",
        "ADV占用",
        "replay、holdout、shadow、前瞻与audit",
        "同一冻结时点",
        "去掉极端单日后优势消失",
    )),
    "table_help_uses_operational_checks":all(x in table_guide for x in (
        "基线权重、TRIAID权重和Δ权重",
        "样本数 → 命中/误报 → Lift → p/q",
        "ADV占用、成交天数、fill ratio",
        "冻结时理由",
    )),
    "risk_help_is_evidence_driven":all(x in risk_dynamic for x in (
        "为什么：",
        "影响：",
        "下一步：",
        "解除看什么：",
        "main_drivers",
        "missing_confirmations",
        "deescalation_conditions",
    )),
    "risk_footer_tells_reading_order":"先看主要风险驱动，再看尚未确认项，最后看20/60/120/250日" in app,
    "hk_scope_help_is_comparison_specific":"不能拿美股/A股权重或结果横向替代" in app,
}

failed=[k for k,v in checks.items() if not v]
if ui_ids-classified:
    failed.append("unclassified_ui_ids="+",".join(sorted(ui_ids-classified)))
if classified-ui_ids:
    failed.append("unknown_classified_ui_ids="+",".join(sorted(classified-ui_ids)))
if vague_hits:
    failed.append("vague_phrases="+",".join(vague_hits))

if failed:
    raise SystemExit("TRIAID_UI_COPY_QUALITY_FAILED:"+"|".join(failed))

print("TRIAID_UI_COPY_QUALITY_PASS",{
    "ui_tip_ids":len(ui_ids),
    "classified":len(classified),
    "checks":len(checks),
})
