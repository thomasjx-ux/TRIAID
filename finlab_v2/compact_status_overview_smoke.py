from __future__ import annotations

from app import home

html=home()

cards=(
    "regimeCard","runStateCard","selectedNamesCard","dailyAnalysisCard",
    "dateCard","coreCard","selectedCountCard","cumExcessCard",
)
notes=(
    "regimeNote","runStateNote","selectedNamesNote","dailyAnalysisNote",
    "dateNote","coreNote","selectedCountNote","cumExcessNote",
)

checks={
    "single_compact_overview":html.count('id="currentStatusOverview"')==1,
    "old_second_heading_is_hidden":'<h2 id="overviewTitle" class="compat-heading">' in html,
    "eight_status_cards":all(html.count(f'id="{x}"')==1 for x in cards),
    "eight_status_notes":all(html.count(f'id="{x}"')==1 for x in notes),
    "wide_analysis_card":'id="dailyAnalysisCard"' in html and 'span-5 status-analysis-card' in html,
    "twelve_column_layout":'.status-overview-grid{display:grid;grid-template-columns:repeat(12' in html,
    "responsive_layout_present":"@media(max-width:1050px)" in html and "@media(max-width:640px)" in html,
    "no_fixed_big_card_height":".status-card{background:#fff" in html and "min-height:72px" in html,
    "human_run_state":"NO_NEW_DATA:zh?'暂无新数据'" in html,
    "human_regime":"risk_on_trend:zh?'风险偏好 · 趋势'" in html,
    "human_core_version":"TRIAID Core · " in html,
    "empty_selection_is_human":"暂无入选" in html and "当前冻结策略群为 0" in html,
    "missing_posterior_is_human":"需至少一轮真实后验后显示" in html,
    "cum_label_is_readable":"累计相对收益差" in html and "Cumulative relative return gap" in html,
    "overview_precedes_live":html.find('id="currentStatusOverview"')<html.find('id="liveTitle"'),
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit("TRIAID_COMPACT_STATUS_OVERVIEW_FAILED:"+"|".join(failed))
print("TRIAID_COMPACT_STATUS_OVERVIEW_PASS",{"checks":len(checks),"cards":len(cards)})
