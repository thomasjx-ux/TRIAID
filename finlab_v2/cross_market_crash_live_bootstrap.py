from __future__ import annotations
from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
report=engine.cross_market_crash_run(force=True)
print("TRIAID_US_CN_HK_CRASH_LINKAGE_LIVE_PASS",{
    "experiment_id":report.get("experiment_id"),
    "as_of":report.get("as_of"),
    "data_completeness":report.get("data_completeness"),
    "synchronized_pair_rows":((report.get("detected_crashes") or {}).get("synchronized_pair_rows")),
    "episodes":{
        k:{
            "relation":v.get("relation"),
            "drawdowns":{
                m:((row or {}).get("max_drawdown"))
                for m,row in (v.get("markets") or {}).items()
            },
            "trough_order":v.get("trough_order"),
            "trough_span_days":v.get("trough_span_calendar_days"),
            "pairwise":{
                pair:{
                    "same_day_corr":p.get("same_day_correlation"),
                    "strongest_lag":p.get("strongest_lag_trading_days"),
                    "strongest_corr":p.get("strongest_absolute_correlation"),
                }
                for pair,p in (v.get("pairwise_daily_return_linkage") or {}).items()
            },
        }
        for k,v in (report.get("canonical_episode_studies") or {}).items()
    },
})
