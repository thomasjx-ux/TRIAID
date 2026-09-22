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

d=dates[i]
fred_dates=[d-timedelta(days=1000-10*k) for k in range(101)]
fred={
    "HY_OAS":[(fd,3.0+0.01*k) for k,fd in enumerate(fred_dates)],
    "NFCI":[(fd,-0.5+0.005*k) for k,fd in enumerate(fred_dates)],
    "YIELD_CURVE_10Y2Y":[(fd,-1.0+0.01*k) for k,fd in enumerate(fred_dates)],
    "YIELD_CURVE_10Y3M":[(fd,-1.2+0.012*k) for k,fd in enumerate(fred_dates)],
    "TREASURY_2Y":[(fd,2.0+0.02*k) for k,fd in enumerate(fred_dates)],
    "TREASURY_10Y":[(fd,3.0+0.015*k) for k,fd in enumerate(fred_dates)],
    "TREASURY_30Y":[(fd,3.5+0.01*k) for k,fd in enumerate(fred_dates)],
    "REAL_YIELD_10Y":[(fd,1.0+0.01*k) for k,fd in enumerate(fred_dates)],
    "FED_FUNDS_DAILY":[(fd,2.0+0.015*k) for k,fd in enumerate(fred_dates)],
    "FED_BALANCE_SHEET":[(fd,9000.0-5.0*k) for k,fd in enumerate(fred_dates)],
}
rate_features=LatentHazardExperiment._features(dates,prices,i,fred)
assert rate_features["US_REAL_YIELD_10Y_RISE_90D"]>0
assert rate_features["US_POLICY_REPRICING_PROXY_2Y_ABS_30D"]>0
assert rate_features["YIELD_CURVE_10Y2Y_RESTEEPENING_180D"]>0
assert rate_features["YIELD_CURVE_10Y3M_RESTEEPENING_180D"]>0
assert rate_features["FED_POLICY_TIGHTENING_90D"]>0
assert rate_features["FED_BALANCE_SHEET_CONTRACTION_180D"]>0

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
    "real_yield_shock":rate_features["US_REAL_YIELD_10Y_RISE_90D"],
    "policy_repricing":rate_features["US_POLICY_REPRICING_PROXY_2Y_ABS_30D"],
    "curve_resteepening":rate_features["YIELD_CURVE_10Y2Y_RESTEEPENING_180D"],
})
