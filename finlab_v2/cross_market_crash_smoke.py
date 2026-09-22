from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from triaid_fin.cross_market_crash import CrossMarketCrashExperiment

start=datetime(2000,1,3,tzinfo=timezone.utc)
n=7000
ts=[int((start+timedelta(days=i)).timestamp()) for i in range(n)]

def path(crash_start:int,crash_depth:float,lag:int=0):
    out=[]
    for i in range(n):
        base=100.0*math.exp(0.00015*i)
        j=i-(crash_start+lag)
        if j<0:
            factor=1.0
        elif j<60:
            factor=1.0-crash_depth*(j/60.0)
        elif j<180:
            factor=(1.0-crash_depth)+crash_depth*((j-60)/120.0)
        else:
            factor=1.0
        out.append(base*factor)
    return {"ts":ts,"close":out}

series={
    "US":path(3000,0.35,0),
    "CN":path(3000,0.30,10),
    "HK":path(3000,0.40,5),
}
events={m:CrossMarketCrashExperiment._detect_crashes(s) for m,s in series.items()}
assert all(events[m] for m in ("US","CN","HK"))
link=CrossMarketCrashExperiment._pairwise_automatic_linkage(events)
assert set(link)=={"CN_HK","CN_US","HK_US"}
assert all(any(x["classification"]=="SYNCHRONIZED_20PCT_CRASH" for x in rows) for rows in link.values())

spec={"start":"2008-01-01","end":"2012-01-01","description":"synthetic"}
episode=CrossMarketCrashExperiment._episode_report("SYN",spec,series)
assert all(episode["markets"][m]["available"] for m in ("US","CN","HK"))
assert all(v["available"] for v in episode["pairwise_daily_return_linkage"].values())
assert episode["relation"]=="ALL_THREE_20PCT_CRASH"
assert len(episode["trough_order"])==3

print("TRIAID_US_CN_HK_CRASH_LINKAGE_SMOKE_PASS",{
    "event_counts":{m:len(v) for m,v in events.items()},
    "relation":episode["relation"],
    "trough_order":episode["trough_order"],
})
