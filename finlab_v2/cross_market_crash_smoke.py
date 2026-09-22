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

us=path(3000,0.35,0)
cn=path(3000,0.30,10)

us_events=CrossMarketCrashExperiment._detect_crashes(us)
cn_events=CrossMarketCrashExperiment._detect_crashes(cn)
assert us_events and cn_events
link=CrossMarketCrashExperiment._automatic_linkage(us_events,cn_events)
assert any(x["classification"]=="SYNCHRONIZED_20PCT_CRASH" for x in link)

spec={"start":"2008-01-01","end":"2012-01-01","description":"synthetic"}
episode=CrossMarketCrashExperiment._episode_report("SYN",spec,us,cn)
assert episode["US"]["available"] is True
assert episode["CN"]["available"] is True
assert episode["daily_return_linkage"]["available"] is True
assert episode["relation"] in {"BOTH_20PCT_CRASH","BOTH_STRESSED","ONE_SIDE_DOMINANT","WEAK_SHARED_STRESS"}

print("TRIAID_US_CN_CRASH_LINKAGE_SMOKE_PASS",{
    "us_events":len(us_events),
    "cn_events":len(cn_events),
    "relation":episode["relation"],
})
