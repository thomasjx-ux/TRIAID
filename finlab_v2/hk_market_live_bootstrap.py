from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
result=engine.refresh_market_data("HK","DAILY")
snapshot=engine.market_data_snapshot("HK","DAILY",False)
observed=engine.record_market_observation(snapshot)
print("TRIAID_HK_MARKET_LIVE_PASS",{
    "provider":result.get("provider"),
    "points":result.get("points"),
    "source_latest_ts":result.get("source_latest_ts"),
    "symbols":snapshot.get("symbols"),
    "session_phase":snapshot.get("session_phase"),
    "observation_recorded":observed.get("recorded"),
    "observation_reason":observed.get("reason"),
})
