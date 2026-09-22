from __future__ import annotations
from triaid_fin.engine import EvolutionLabEngine
engine=EvolutionLabEngine()
report=engine.cross_market_crash_run(force=True)
print("TRIAID_US_CN_CRASH_LINKAGE_LIVE_PASS",{
    "experiment_id":report.get("experiment_id"),
    "as_of":report.get("as_of"),
    "data_completeness":report.get("data_completeness"),
    "paired_event_rows":((report.get("detected_crashes") or {}).get("paired_event_rows")),
    "episodes":{
        k:{
            "relation":v.get("relation"),
            "US_dd":((v.get("US") or {}).get("max_drawdown")),
            "CN_dd":((v.get("CN") or {}).get("max_drawdown")),
            "cn_minus_us_trough_days":v.get("cn_trough_minus_us_trough_calendar_days"),
            "same_day_corr":((v.get("daily_return_linkage") or {}).get("same_day_correlation")),
            "strongest_lag":((v.get("daily_return_linkage") or {}).get("strongest_lag_trading_days")),
            "strongest_corr":((v.get("daily_return_linkage") or {}).get("strongest_absolute_correlation")),
        }
        for k,v in (report.get("canonical_episode_studies") or {}).items()
    },
})
