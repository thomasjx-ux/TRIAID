from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from triaid_fin.market_lab import MARKETS
from triaid_fin.market_data import get_market_data_hub, session_phase
from triaid_fin.trading_calendar import trading_day_info, official_session_phase

assert "HK" in MARKETS
assert MARKETS["HK"].currency=="HKD"
hub=get_market_data_hub()
caps=hub.capabilities("HK")["HK"]
assert caps["DAILY"]["supported"] is True
assert caps["INTRADAY"]["supported"] is True
assert caps["REALTIME"]["supported"] is True
assert caps["PREOPEN"]["supported"] is False

holiday=trading_day_info("HK","2026-10-01")
assert holiday["calendar_known"] is True
assert holiday["is_trading_day"] is False
normal=trading_day_info("HK","2026-09-23")
assert normal["calendar_known"] is True
assert normal["is_trading_day"] is True

tz=ZoneInfo("Asia/Hong_Kong")
assert official_session_phase("HK",datetime(2026,9,23,9,10,tzinfo=tz))=="PREOPEN"
assert official_session_phase("HK",datetime(2026,9,23,10,0,tzinfo=tz))=="OPEN"
assert official_session_phase("HK",datetime(2026,9,23,12,30,tzinfo=tz))=="BREAK"
assert official_session_phase("HK",datetime(2026,9,23,14,0,tzinfo=tz))=="OPEN"

print("TRIAID_HK_MARKET_STATIC_SMOKE_PASS",{
    "benchmark":MARKETS["HK"].benchmark,
    "assets":MARKETS["HK"].assets,
    "daily":caps["DAILY"]["supported"],
    "realtime":caps["REALTIME"]["supported"],
})
