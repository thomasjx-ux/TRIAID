import asyncio

import triaid_fin.market_runtime as market_runtime
from triaid_fin.market_runtime import MarketDataAutomation


class FakeStore:
    def __init__(self):
        self.data={}

    def load_json(self,name,default=None):
        return self.data.get(name,default)

    def save_json(self,name,value):
        self.data[name]=value
        return None

    def append_jsonl(self,*args,**kwargs):
        return None


class FakeEngine:
    def __init__(self):
        self.store=FakeStore()

    def market_data_capabilities(self,market_id):
        return {
            market_id:{
                "DAILY":{"supported":False},
                "INTRADAY":{"supported":False},
                "REALTIME":{"supported":False},
                "PREOPEN":{"supported":False},
            }
        }

    def market_data_status(self):
        return {}


async def main():
    automation=MarketDataAutomation(FakeEngine())
    original_phase=market_runtime.session_phase
    original_sleep=market_runtime.asyncio.sleep

    def fake_phase(market_id):
        if market_id=="US":
            raise NameError("simulated_phase_failure")
        return "CLOSED"

    async def stop_after_cycle(_seconds):
        raise asyncio.CancelledError()

    market_runtime.session_phase=fake_phase
    market_runtime.asyncio.sleep=stop_after_cycle
    try:
        try:
            await automation.run()
        except asyncio.CancelledError:
            pass
    finally:
        market_runtime.session_phase=original_phase
        market_runtime.asyncio.sleep=original_sleep

    assert "US:AUTOMATION_LOOP" in automation.errors
    assert "CN" in automation.last_market_cycle_utc
    assert automation.last_loop_heartbeat_utc is not None
    assert automation.errors["US:AUTOMATION_LOOP"].startswith("NameError:")
    print("TRIAID_MARKET_AUTOMATION_SELF_HEALING_SMOKE_PASS")
    print({
        "us_error":automation.errors["US:AUTOMATION_LOOP"],
        "cn_cycle":automation.last_market_cycle_utc["CN"],
        "heartbeat":automation.last_loop_heartbeat_utc,
        "policy":automation.status()["self_healing"]["policy"],
    })


asyncio.run(main())
