from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from triaid_fin.tencent_cn_data import TencentCNMarketDataProvider

tz=ZoneInfo("Asia/Hong_Kong")


def ts(hhmm:str)->int:
    dt=datetime.fromisoformat(f"2026-09-23T{hhmm}:00").replace(tzinfo=tz)
    return int(dt.timestamp())


rows=[]
# Isolated opening print: must not become a fake five-minute bar.
rows.append((ts("09:30"),100.0,10.0))

# Complete 09:31..09:35 bucket -> 09:35.
for i,minute in enumerate(("09:31","09:32","09:33","09:34","09:35"),start=1):
    rows.append((ts(minute),100.0+i,1.0))

# Incomplete bucket 09:36..09:39 -> must be omitted.
for i,minute in enumerate(("09:36","09:37","09:38","09:39"),start=1):
    rows.append((ts(minute),110.0+i,2.0))

# Complete lunch-edge bucket 11:56..12:00 -> 12:00.
for i,minute in enumerate(("11:56","11:57","11:58","11:59","12:00"),start=1):
    rows.append((ts(minute),120.0+i,3.0))

out=TencentCNMarketDataProvider._aggregate_one_minute_to_five(rows,tz)
by={stamp:(close,volume) for stamp,close,volume in out}

assert ts("09:30") not in by
assert ts("09:35") in by
assert by[ts("09:35")][0]==105.0
assert by[ts("09:35")][1]==5.0

assert ts("09:40") not in by

assert ts("12:00") in by
assert by[ts("12:00")][0]==125.0
assert by[ts("12:00")][1]==15.0

print(
    "TRIAID_HK_TENCENT_5M_AGGREGATION_SMOKE_PASS",
    {
        "bars":len(out),
        "first":min(by),
        "last":max(by),
        "lunch_close":by[ts("12:00")][0],
    },
)
