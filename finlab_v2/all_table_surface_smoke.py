from __future__ import annotations

import re
from app import home

html=home()

expected_tbodies={
    "strategyRows","candidateRows",
    "usrmStrategyRows","usrmAssetRows","usrmCapitalRows","usrmRealizedRows","usrmDailyRows",
    "prospectiveStrategyRows","prospectiveDailyRows",
    "recoveryOpinionRows","capitalSleeveRows","capitalRealizedRows","recoveryReviewRows",
    "hkStrategyRows","hkAssetRows","hkCapitalRows","hkRealizedRows","hkDailyRows",
    "riskThreeMarketRows","riskDynamicsRows","riskMacroRows","riskTermRows",
    "riskCurveContractRows","riskHistoryRows","riskControlRows",
    "deiCapitalRows","deiCrossMarketRows",
}
actual_tbodies=set(re.findall(r'<tbody[^>]*id="([^"]+)"',html))
tables=re.findall(r'<table(?:\s[^>]*)?>([\s\S]*?)</table>',html)

checks={
    "table_count_is_expected":len(tables)==27,
    "tbody_contract_exact":actual_tbodies==expected_tbodies,
    "dynamic_cn_daily_header_present":'id="prospectiveDailyHead"' in html,
    "all_static_tables_have_headers":all(
        ("<th" in t) or ('id="prospectiveDailyHead"' in t)
        for t in tables
    ),
    "table_width_classes_are_runtime_driven":"table-xwide" in html and "table-wide" in html and "freeze-first" in html,
    "wide_tables_freeze_first_column":"table.freeze-first th:first-child" in html and "table.freeze-first td:first-child" in html,
    "semantic_empty_row_helper":"function tableEmptyRow" in html and 'class="table-empty"' in html,
    "selected_strategy_empty_state":"当前没有冻结入选策略" in html,
    "candidate_empty_state":"当前没有未入选候选策略" in html,
    "cn_strategy_empty_state":"当前前瞻实验还没有冻结策略排序" in html,
    "cn_daily_empty_state":"等待首个后续真实交易日结果" in html,
    "us_tables_have_empty_states":all(x in html for x in (
        "等待冻结策略","当前无风险ETF敞口","等待资金容量决策",
        "上一轮尚无可用的后验模拟执行结果","等待下一完整美股交易日结果",
    )),
    "cn_recovery_tables_have_empty_states":all(x in html for x in (
        "暂无冻结研究配置意见","等待当前资金容量决策",
        "上一轮资金袖套尚无可用的后验模拟执行结果",
        "上一轮尚未产生可用的下一完整交易日结果",
    )),
    "hk_tables_have_empty_states":all(x in html for x in (
        "等待首个正式港股冻结策略群",
        "等待港股冻结ETF敞口",
        "等待首个四档港币容量决策",
        "尚无上一轮港股容量后验",
        "尚无上一轮港股真实后验路径",
        "等待下一完整港股交易日结果",
    )),
    "risk_tables_have_empty_states":all(x in html for x in (
        "当前没有可用的三市场联动状态",
        "当前没有可展示的动力链节点",
        "当前没有可用的利率/政策/信用/流动性快照",
        "当前没有可展示的期限合约点",
        "当前没有达到统计支持门槛的联合状态",
        "当前没有可用的三市场 Shadow 风控实验状态",
    )),
    "no_naked_dash_history_row":'<td colspan="8">-</td>' not in html,
    "risk_multiplier_has_correct_unit":"fmtMultiplier(q.risky_exposure_multiplier)" in html,
    "dynamics_strength_uses_100_scale":"normalized.toFixed(1)+'/100'" in html,
    "macro_value_units_are_explicit":"function macroValueText" in html,
    "term_curve_missing_values_do_not_get_suffix":"fmtRiskWithUnit(x.front_implied_rate" in html and "fmtRiskWithUnit(x.front_to_back_change" in html,
    "lifecycle_is_humanized":"lifecycleLabel(x.lifecycle)" in html,
    "risk_stage_is_humanized":"riskStageLabel(stage)" in html,
    "chain_state_is_humanized":"chainStateLabel(state)" in html,
    "historical_lift_is_percentage_points":"fmtPp(x.hit_rate_lift)" in html,
}
failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_ALL_TABLE_SURFACE_FAILED:"+"|".join(failed))
print("TRIAID_ALL_TABLE_SURFACE_PASS",{
    "checks":len(checks),
    "tables":len(tables),
    "tbodies":len(actual_tbodies),
})
