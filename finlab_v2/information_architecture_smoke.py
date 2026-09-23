from __future__ import annotations

import re
from html.parser import HTMLParser
from app import home

html=home()

def pos(id_:str)->int:
    p=html.find(f'id="{id_}"')
    if p<0:
        raise SystemExit(f"TRIAID_INFORMATION_ARCHITECTURE_FAILED:MISSING:{id_}")
    return p

ids=[
    "homeSummary","marketClockStrip","marketHero",
    "stageMarket","stageDecision","stageValidation","stageRoute","stageRisk","stageEvolution",
    "dailyTitle","overviewTitle","liveTitle","strategyTitle","resultTitle","curveTitle",
    "riskWarningPanel","riskDrivers","riskBlockers","riskDeepEvidence","evolutionTitle","systemOpsPanel",
    "activityWindowTitle",
]
for x in ids:
    marker=f'id="{x}"'
    count=html.count(marker)
    if count!=1:
        raise SystemExit(f"TRIAID_INFORMATION_ARCHITECTURE_FAILED:ID_COUNT:{x}:{count}")

checks={
    "executive_summary_before_market_controls":pos("homeSummary")<pos("marketClockStrip")<pos("marketHero"),
    "six_stage_chain":pos("stageMarket")<pos("stageDecision")<pos("stageValidation")<pos("stageRoute")<pos("stageRisk")<pos("stageEvolution"),
    "market_context_before_decision":pos("dailyTitle")<pos("overviewTitle")<pos("liveTitle")<pos("strategyTitle"),
    "decision_before_realized_validation":pos("strategyTitle")<pos("resultTitle")<pos("curveTitle"),
    "route_after_validation":pos("curveTitle")<pos("stageRoute"),
    "risk_after_single_market_route":pos("stageRoute")<pos("riskWarningPanel"),
    "risk_conclusion_before_deep_evidence":pos("riskDrivers")<pos("riskDeepEvidence") and pos("riskBlockers")<pos("riskDeepEvidence"),
    "evolution_after_risk":pos("riskWarningPanel")<pos("evolutionTitle"),
    "ops_after_business_chain":pos("evolutionTitle")<pos("systemOpsPanel")<pos("activityWindowTitle"),
    "system_ops_collapsed_by_default":'<details class="ops-details" id="systemOpsPanel">' in html,
    "deep_risk_collapsed_by_default":'<details class="evidence-details" id="riskDeepEvidence">' in html,
    "flow_copy_present":"当前市场与状态 → TRIAID决策 → 真实结果验证 → 市场专属路线与可实现性 → 三市场风险 → Core进化" in html,
    "flow_labels_are_bilingual":"function applyFlowLabels()" in html and "US Generic-Core Control Decision" in html and "CN Return-First Decision" in html and "HK Return-First Decision" in html and "Core evolution" in html,
    "all_markets_have_route_summary":'id="marketRouteOverview"' in html and 'id="hkRoutePanel"' in html,
}

failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("TRIAID_INFORMATION_ARCHITECTURE_FAILED:"+"|".join(failed))

print("TRIAID_INFORMATION_ARCHITECTURE_PASS",{"checks":len(checks),"stages":6})
