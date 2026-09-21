from __future__ import annotations

import asyncio
import tempfile

import triaid_fin.market_runtime as market_runtime
from triaid_fin.market_runtime import MarketDataAutomation
from triaid_fin.store import RunStore


class FakeEngine:
    def __init__(self, root: str) -> None:
        self.store = RunStore(root)
        self.calls: list[tuple[str, str]] = []

    def market_data_capabilities(self, market_id: str) -> dict:
        return {
            market_id: {
                "DAILY": {"supported": True},
                "INTRADAY": {"supported": True},
                "PREOPEN": {"supported": True},
                "REALTIME": {"supported": True},
            }
        }

    def refresh_market_data(self, market_id: str, mode: str) -> dict:
        self.calls.append((market_id, mode))
        return {"source_latest_ts": 1_800_000_000, "points": 5}

    def market_data_snapshot(self, market_id: str, mode: str, refresh: bool) -> dict:
        return {
            "market_id": market_id,
            "mode": mode,
            "source_latest_ts": 1_800_000_000,
            "provider": "fake",
            "latest": {},
        }

    def record_market_observation(self, snapshot: dict) -> dict:
        return {"recorded": True, "reason": "RECORDED"}

    def market_data_status(self) -> dict:
        return {"provider": "fake", "ok": True}


def main() -> None:
    original_session_phase = market_runtime.session_phase
    market_runtime.session_phase = lambda market_id: "OPEN"
    try:
        with tempfile.TemporaryDirectory(prefix="triaid-serverless-scheduler-") as root:
            first_engine = FakeEngine(root)
            first = MarketDataAutomation(first_engine)
            first_result = asyncio.run(first.tick_once(("US",)))
            assert first_result["ok"] is True
            assert len(first_result["refreshed"]) == 2
            assert set(first_engine.calls) == {("US", "INTRADAY"), ("US", "REALTIME")}
            assert first.status()["external_tick_ready"] is True

            second_engine = FakeEngine(root)
            second = MarketDataAutomation(second_engine)
            second_result = asyncio.run(second.tick_once(("US",)))
            assert second_result["ok"] is True
            assert second_engine.calls == []
            assert len(second_result["skipped"]) == 2
            assert all(row["reason"] == "INTERVAL_NOT_DUE" for row in second_result["skipped"])

            state = second_engine.store.load_json("market_automation_state.json")
            for key in list(state.get("last_refresh_epoch") or {}):
                state["last_refresh_epoch"][key] = 0.0
            second_engine.store.save_json("market_automation_state.json", state)

            third_engine = FakeEngine(root)
            third = MarketDataAutomation(third_engine)
            third_result = asyncio.run(third.tick_once(("US",)))
            assert third_result["ok"] is True
            assert len(third_result["refreshed"]) == 2
            assert set(third_engine.calls) == {("US", "INTRADAY"), ("US", "REALTIME")}

        print("SERVERLESS_SCHEDULER_SMOKE_PASS")
    finally:
        market_runtime.session_phase = original_session_phase


if __name__ == "__main__":
    main()
