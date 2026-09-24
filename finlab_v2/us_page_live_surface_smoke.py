from __future__ import annotations

from pathlib import Path
from app import home

ROOT=Path(__file__).resolve().parent
source=(ROOT/"app.py").read_text(encoding="utf-8")
html=home()

checks={
    "intraday_panel_present":'id="usrmIntradayPanel"' in html,
    "intraday_regime_present":'id="usrmIntradayRegime"' in html,
    "intraday_decision_count_present":'id="usrmIntradayDecisionCount"' in html,
    "intraday_weight_change_present":'id="usrmIntradayWeightChange"' in html,
    "intraday_update_time_present":'id="usrmIntradayUpdated"' in html,
    "intraday_renderer_present":"function renderUSIntradayState" in source,
    "intraday_reads_scheduler_events":"/api/decision-scheduler/events?market_id=US&limit=120" in source,
    "intraday_reads_scheduler_status":"/api/decision-scheduler/status" in source,
    "intraday_does_not_claim_formal_posterior":"盘中层只读，不进入正式后验" in source,
    "realized_table_has_wrapper":'id="usrmRealizedWrap"' in html,
    "daily_table_has_wrapper":'id="usrmDailyWrap"' in html,
    "realized_empty_explained":'id="usrmRealizedEmpty"' in html and "盘中价格不会被冒充为已实现收益" in html,
    "daily_empty_explained":'id="usrmDailyEmpty"' in html and "盘中状态单独显示在上方" in html,
    "unavailable_realized_table_collapses":"usrmRealizedWrap').style.display=hasRealized?'block':'none'" in source,
    "unavailable_daily_table_collapses":"usrmDailyWrap').style.display=hasDaily?'block':'none'" in source,
    "formal_return_max_still_present":'id="usrmStrategyRows"' in html and 'id="usrmCapitalRows"' in html,
}
failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_US_PAGE_LIVE_SURFACE_FAILED:"+"|".join(failed))
print("TRIAID_US_PAGE_LIVE_SURFACE_PASS",{"checks":len(checks)})
