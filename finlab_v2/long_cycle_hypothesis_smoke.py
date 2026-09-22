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


macro_snapshot={
    "TREASURY_2Y":{"latest_value":4.2,"change_calendar_days":{"30":0.3,"90":0.5,"180":0.7},"pct_change_calendar_days":{}},
    "TREASURY_5Y":{"latest_value":4.0,"change_calendar_days":{},"pct_change_calendar_days":{}},
    "TREASURY_10Y":{"latest_value":4.3,"change_calendar_days":{"90":0.4},"pct_change_calendar_days":{}},
    "TREASURY_30Y":{"latest_value":4.7,"change_calendar_days":{},"pct_change_calendar_days":{}},
    "REAL_YIELD_10Y":{"latest_value":2.1,"change_calendar_days":{"90":0.35},"pct_change_calendar_days":{},"percentiles":{"20":{"percentile":0.85}}},
    "FED_FUNDS_DAILY":{"latest_value":5.25,"change_calendar_days":{"90":0.25},"pct_change_calendar_days":{}},
    "FED_BALANCE_SHEET":{"latest_value":7000000.0,"change_calendar_days":{},"pct_change_calendar_days":{"180":-0.05}},
    "YIELD_CURVE_10Y2Y":{"latest_value":0.1,"change_calendar_days":{"180":0.8},"pct_change_calendar_days":{}},
    "YIELD_CURVE_10Y3M":{"latest_value":-0.2,"change_calendar_days":{"180":0.6},"pct_change_calendar_days":{}},
}
market_expectations={
    "MOVE_INDEX":{"latest_value":110.0,"historical_percentile":0.88,"change_calendar_days":{"30":15.0}},
    "FED_FUNDS_FUTURE":{"latest_value":4.8,"change_calendar_days":{"30":0.35}},
    "SOFR_1M_FUTURE":{"latest_value":4.7,"change_calendar_days":{"30":0.25}},
}
rp=LongCycleHypothesisExperiment._rates_policy_snapshot(macro_snapshot,market_expectations)
assert rp["treasury_curve"]["2y"]==4.2
assert rp["real_rates"]["10y_real_yield_20y_percentile"]==0.85
assert abs(rp["policy"]["2y_policy_repricing_proxy_abs_30d"]-0.3)<1e-9
assert rp["curve_state"]["10y2y_inverted"] is False
assert rp["curve_state"]["10y3m_inverted"] is True
assert abs(rp["balance_sheet"]["walcl_pct_change_180d"]+0.05)<1e-9
assert rp["market_expectations"]["move_level"]==110.0
assert rp["market_expectations"]["move_historical_percentile"]==0.88
assert abs(rp["market_expectations"]["nearby_futures_basis_abs"]-0.1)<1e-9

print("TRIAID_LONG_CYCLE_HYPOTHESIS_SMOKE_PASS",{
    "horizons":list(HORIZON_YEARS),
    "30y_available":m30["available"],
    "downturn_state":down["state"],
    "stretch_state":stretch["state"],
    "rates_policy_snapshot":True,
})
