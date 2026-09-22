from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from triaid_fin.latent_hazard import LatentHazardExperiment

# Synthetic helper validation: three markets share a late crash, HK amplifies it.
start=datetime(1990,1,1,tzinfo=timezone.utc)
n=9000
dates=[(start+timedelta(days=i)).date() for i in range(n)]
base=[100.0*math.exp(0.0002*i) for i in range(n)]

def shocked(depth:float,offset:int=0):
    out=[]
    for i,x in enumerate(base):
        j=i-(7000+offset)
        if j<0:
            factor=1.0
        elif j<80:
            factor=1.0-depth*j/80.0
        elif j<200:
            factor=(1.0-depth)+depth*(j-80)/120.0
        else:
            factor=1.0
        out.append(x*factor)
    return out

prices={"US":shocked(0.30,0),"CN":shocked(0.28,8),"HK":shocked(0.42,3)}
i=7040
features=LatentHazardExperiment._features(dates,prices,i,{})
assert features["HK_DRAWDOWN_STRESS_252"]>features["US_DRAWDOWN_STRESS_252"]
assert features["HK_STRESS_AMPLIFICATION"]>0
assert "HK_BRIDGE_DIFFERENTIAL_60" in features
p=LatentHazardExperiment._percentile([float(x) for x in range(100)],90.0)
assert p is not None and p>0.8

events={
    "US":[{"breach_date":"2009-03-01","trough_date":"2009-03-20","max_drawdown":-0.3}],
    "CN":[{"breach_date":"2009-03-10","trough_date":"2009-03-25","max_drawdown":-0.28}],
    "HK":[{"breach_date":"2009-03-05","trough_date":"2009-03-22","max_drawdown":-0.42}],
}
clusters=LatentHazardExperiment._event_clusters(events)
assert len(clusters)==1
assert clusters[0]["market_count"]==3

print("TRIAID_LATENT_HAZARD_SMOKE_PASS",{
    "hk_amplification":features["HK_STRESS_AMPLIFICATION"],
    "bridge":features["HK_BRIDGE_DIFFERENTIAL_60"],
    "clusters":len(clusters),
})
