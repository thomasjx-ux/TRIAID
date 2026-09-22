from __future__ import annotations

import math

from triaid_fin.long_cycle_hypothesis import (
    HORIZON_YEARS,
    LongCycleHypothesisExperiment,
)


# 35 years of deterministic synthetic daily prices: enough to test 30Y coverage.
n=35*252+20
ts=[946684800+i*86400 for i in range(n)]
prices=[100.0*math.exp(0.00025*i)*(1.0+0.02*math.sin(i/45.0)) for i in range(n)]

m30=LongCycleHypothesisExperiment._horizon_metrics(ts,prices,30)
assert m30["available"] is True
assert m30["years"]==30
assert m30["historical_comparison_windows"]>10
assert m30["cagr"]>0
assert -1.0<=m30["max_drawdown"]<=0.0
assert 0.0<=m30["cagr_historical_percentile"]<=1.0

short=LongCycleHypothesisExperiment._horizon_metrics(ts[:1000],prices[:1000],10)
assert short["available"] is False

assets={}
for label,scale in (
    ("SP500",1.0),
    ("NASDAQ_COMPOSITE",1.1),
    ("RUSSELL_2000",0.9),
    ("DOW_JONES",0.95),
):
    local=[x**scale for x in prices]
    assets[label]={
        "horizons":{
            str(y):LongCycleHypothesisExperiment._horizon_metrics(ts,local,y)
            for y in HORIZON_YEARS
        }
    }
assets["HIGH_YIELD"]={
    "horizons":{
        "2":{
            "available":True,
            "cagr":0.03,
            "current_drawdown_from_window_peak":-0.02,
        }
    }
}

horizons={
    str(y):LongCycleHypothesisExperiment._horizon_summary(assets,y)
    for y in HORIZON_YEARS
}
assert horizons["30"]["available"] is True
assert horizons["30"]["equity_count"]==4

macro={
    "HY_OAS":{
        "latest_value":3.0,
        "percentiles":{"20":{"percentile":0.30}},
    },
    "NFCI":{
        "latest_value":-0.2,
        "percentiles":{"20":{"percentile":0.25}},
    },
    "UNEMPLOYMENT":{"change_12_observations":0.1},
    "YIELD_CURVE_10Y2Y":{"latest_value":0.5},
}

down=LongCycleHypothesisExperiment._downturn_hypothesis(horizons,assets,macro)
assert down["state"] in {"NOT_CONFIRMED","WATCH","MULTI_DIMENSION_CONFIRMED"}
assert down["available_weight"]>0
assert 0.0<=down["support_ratio"]<=1.0

stretch=LongCycleHypothesisExperiment._stretch_hypothesis(horizons,assets)
assert stretch["state"] in {"NORMAL_RANGE","ELEVATED_STRETCH_EVIDENCE","HIGH_STRETCH_EVIDENCE"}
assert stretch["available_weight"]>0
assert 0.0<=stretch["support_ratio"]<=1.0
assert "not by itself a bearish trading signal" in stretch["claim"]

print("TRIAID_LONG_CYCLE_HYPOTHESIS_SMOKE_PASS",{
    "horizons":list(HORIZON_YEARS),
    "30y_available":m30["available"],
    "downturn_state":down["state"],
    "stretch_state":stretch["state"],
})
