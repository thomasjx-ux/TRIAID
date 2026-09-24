from __future__ import annotations

import triaid_fin.market_runtime as runtime
from triaid_fin.market_runtime import MarketDataAutomation


class FakeEngine:
    def market_observations(self,market,mode,limit):
        return [
            {
                "market_id":market,
                "mode":"REALTIME",
                "session_phase":"OPEN",
                "provider":"fake",
                "source_latest_ts":100,
                "latest":{
                    "SPY":{"close":100.0,"volume":10},
                },
            },
            {
                "market_id":market,
                "mode":"REALTIME",
                "session_phase":"OPEN",
                "provider":"fake",
                "source_latest_ts":200,
                "latest":{
                    "SPY":{"close":101.0,"volume":11},
                },
            },
        ]


automation=object.__new__(MarketDataAutomation)
automation.services=FakeEngine()

original=runtime.session_phase
try:
    runtime.session_phase=lambda market:"CLOSED"
    payload=automation.live_indicators("US")
finally:
    runtime.session_phase=original

checks={
    "current_phase_comes_from_current_calendar":payload["session_phase"]=="CLOSED",
    "observation_phase_is_preserved_separately":payload["observation_session_phase"]=="OPEN",
    "historical_observation_remains_available":payload["available"] is True,
    "price_is_preserved":payload["instruments"][0]["close"]==101.0,
    "change_is_computed":round(payload["instruments"][0]["change_pct"],6)==0.01,
}
failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_LIVE_PHASE_SEMANTICS_FAILED:"+"|".join(failed))
print("TRIAID_LIVE_PHASE_SEMANTICS_PASS",{"checks":len(checks)})
