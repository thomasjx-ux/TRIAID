from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import mean, pstdev

from .market_lab import fetch_panel
from .market_registry import MARKETS, normalize_market_id


VERSION="volatility-forecast@0.2.0"
CACHE_FILE="volatility_forecast_latest.json"
MODEL="EWMA94_MULTI_WINDOW_REALIZED_VOL"
SQRT_2_OVER_PI=math.sqrt(2.0/math.pi)


def _log_returns(prices:list[float])->list[float]:
    out=[]
    for i in range(1,len(prices)):
        a=float(prices[i-1]);b=float(prices[i])
        if a>0.0 and b>0.0:
            out.append(math.log(b/a))
    return out


def _std(values:list[float],window:int)->float:
    xs=values[-window:]
    if len(xs)<max(3,window//2):
        return 0.0
    return float(pstdev(xs))


def _ewma_sigma(values:list[float],lam:float=0.94,window:int=63)->float:
    xs=values[-window:]
    if len(xs)<5:
        return _std(values,min(20,len(values)))
    var=mean(x*x for x in xs[:min(10,len(xs))])
    for x in xs:
        var=lam*var+(1.0-lam)*(x*x)
    return math.sqrt(max(0.0,var))


def _sigma_from_returns(values:list[float])->dict:
    if len(values)<20:
        raise ValueError("insufficient_return_history")
    vol20=_std(values,20)
    vol63=_std(values,63)
    if vol63<=0.0:
        vol63=vol20
    ewma=_ewma_sigma(values,0.94,63)
    variance=0.50*(ewma**2)+0.30*(vol20**2)+0.20*(vol63**2)
    sigma=math.sqrt(max(0.0,variance))
    return {
        "sigma":sigma,
        "ewma94":ewma,
        "realized_20":vol20,
        "realized_63":vol63,
    }


def _walk_forward(prices:list[float],max_samples:int=252)->dict:
    if len(prices)<90:
        return {
            "sample_count":0,
            "coverage_68":None,
            "coverage_95":None,
            "mae_abs_move":None,
            "rms_calibration_ratio":None,
            "forecast_sigmas":[],
        }
    rows=[]
    start=max(64,len(prices)-max_samples)
    for i in range(start,len(prices)):
        hist=_log_returns(prices[:i])
        if len(hist)<20:
            continue
        model=_sigma_from_returns(hist)
        sigma=float(model["sigma"])
        actual=abs(math.log(float(prices[i])/float(prices[i-1])))
        rows.append((sigma,actual))
    if not rows:
        return {
            "sample_count":0,
            "coverage_68":None,
            "coverage_95":None,
            "mae_abs_move":None,
            "rms_calibration_ratio":None,
            "forecast_sigmas":[],
        }
    sigmas=[x[0] for x in rows]
    actuals=[x[1] for x in rows]
    n=len(rows)
    forecast_rms=math.sqrt(mean(x*x for x in sigmas)) if sigmas else 0.0
    realized_rms=math.sqrt(mean(x*x for x in actuals)) if actuals else 0.0
    return {
        "sample_count":n,
        "coverage_68":sum(1 for s,a in rows if a<=s)/n,
        "coverage_95":sum(1 for s,a in rows if a<=1.96*s)/n,
        "mae_abs_move":mean(abs(a-s*SQRT_2_OVER_PI) for s,a in rows),
        "rms_calibration_ratio":(realized_rms/forecast_rms) if forecast_rms>0 else None,
        "forecast_sigmas":sigmas,
    }


def _percentile(value:float,history:list[float])->float|None:
    xs=[float(x) for x in history if math.isfinite(float(x))]
    if not xs:
        return None
    return sum(1 for x in xs if x<=value)/len(xs)


def _band(percentile:float|None)->str:
    if percentile is None:
        return "UNKNOWN"
    if percentile<0.25:
        return "LOW"
    if percentile<0.60:
        return "NORMAL"
    if percentile<0.85:
        return "ELEVATED"
    return "HIGH"


def _quality(coverage_68:float|None,ratio:float|None,n:int)->str:
    if n<60 or coverage_68 is None or ratio is None:
        return "INSUFFICIENT"
    coverage_error=abs(coverage_68-0.68)
    ratio_error=abs(ratio-1.0)
    if coverage_error<=0.08 and ratio_error<=0.15:
        return "WELL_CALIBRATED"
    if coverage_error<=0.15 and ratio_error<=0.30:
        return "USABLE"
    return "POORLY_CALIBRATED"


def volatility_forecast(market_id:str)->dict:
    market=normalize_market_id(market_id)
    panel=fetch_panel(market,"DAILY",force=False)
    benchmark=MARKETS[market].benchmark
    prices=[float(x) for x in (panel.close.get(benchmark) or []) if float(x)>0.0]
    if len(prices)<90:
        raise ValueError(f"insufficient_daily_history:{market}:{len(prices)}")
    returns=_log_returns(prices)
    model=_sigma_from_returns(returns)
    sigma=float(model["sigma"])
    latest=float(prices[-1])
    wf=_walk_forward(prices,252)
    pct=_percentile(sigma,wf.get("forecast_sigmas") or [])
    lower=latest*math.exp(-sigma)
    upper=latest*math.exp(sigma)
    return {
        "version":VERSION,
        "model":MODEL,
        "market_id":market,
        "benchmark":benchmark,
        "as_of_source_ts":int(panel.ts[-1]),
        "provider":panel.provider,
        "data_quality":panel.quality,
        "research_only":True,
        "horizon":"NEXT_TRADING_DAY_CLOSE_TO_CLOSE",
        "forecast_sigma":sigma,
        "forecast_move_pct":sigma*100.0,
        "expected_abs_move_pct":sigma*SQRT_2_OVER_PI*100.0,
        "reference_close":latest,
        "range_68":{
            "lower":lower,
            "upper":upper,
            "lower_return_pct":(math.exp(-sigma)-1.0)*100.0,
            "upper_return_pct":(math.exp(sigma)-1.0)*100.0,
        },
        "volatility_percentile":pct,
        "volatility_band":_band(pct),
        "components":{
            "ewma94_pct":float(model["ewma94"])*100.0,
            "realized_20_pct":float(model["realized_20"])*100.0,
            "realized_63_pct":float(model["realized_63"])*100.0,
        },
        "walk_forward":{
            "window":"UP_TO_252_TRADING_DAYS",
            "sample_count":int(wf["sample_count"]),
            "coverage_68":wf["coverage_68"],
            "coverage_95":wf["coverage_95"],
            "mae_abs_move_pct":(
                None if wf["mae_abs_move"] is None
                else float(wf["mae_abs_move"])*100.0
            ),
            "rms_calibration_ratio":wf["rms_calibration_ratio"],
            "calibration_quality":_quality(
                wf["coverage_68"],
                wf["rms_calibration_ratio"],
                int(wf["sample_count"]),
            ),
        },
        "interpretation_guard":"Volatility forecast estimates next-session close-to-close magnitude, not direction. The 68% range is a model interval, not a guarantee. Walk-forward calibration uses only information available before each realized day.",
    }


def all_market_volatility_forecasts()->dict:
    rows={}
    errors={}
    for market in ("US","CN","HK"):
        try:
            rows[market]=volatility_forecast(market)
        except Exception as exc:
            errors[market]=f"{type(exc).__name__}:{exc}"
    return {
        "version":VERSION,
        "model":MODEL,
        "markets":rows,
        "errors":errors,
        "research_only":True,
    }


def _empty_cache()->dict:
    return {
        "version":VERSION,
        "model":MODEL,
        "markets":{},
        "errors":{},
        "research_only":True,
        "cache_state":"EMPTY",
        "updated_at":None,
    }


def cached_all_market_volatility_forecasts(store)->dict:
    payload=store.load_json(CACHE_FILE,_empty_cache())
    if not isinstance(payload,dict):
        payload=_empty_cache()
    payload.setdefault("version",VERSION)
    payload.setdefault("model",MODEL)
    payload.setdefault("markets",{})
    payload.setdefault("errors",{})
    payload.setdefault("research_only",True)
    payload["cache_state"]="READY" if payload.get("markets") else "EMPTY"
    return payload


def cached_volatility_forecast(store,market_id:str)->dict:
    market=normalize_market_id(market_id)
    payload=cached_all_market_volatility_forecasts(store)
    row=(payload.get("markets") or {}).get(market)
    if not isinstance(row,dict):
        raise ValueError(f"volatility_forecast_cache_not_ready:{market}")
    return row


def refresh_all_market_volatility_forecasts(store,require_all:bool=True)->dict:
    rows={}
    errors={}
    for market in ("US","CN","HK"):
        try:
            rows[market]=volatility_forecast(market)
        except Exception as exc:
            errors[market]=f"{type(exc).__name__}:{exc}"
    payload={
        "version":VERSION,
        "model":MODEL,
        "markets":rows,
        "errors":errors,
        "research_only":True,
        "cache_state":"READY" if rows else "EMPTY",
        "updated_at":datetime.now(timezone.utc).isoformat(),
    }
    store.save_json(CACHE_FILE,payload)
    if require_all and set(rows)!={"US","CN","HK"}:
        raise RuntimeError(f"volatility_forecast_refresh_incomplete:{errors}")
    return payload


def refresh_market_volatility_forecast(store,market_id:str)->dict:
    market=normalize_market_id(market_id)
    payload=cached_all_market_volatility_forecasts(store)
    markets=dict(payload.get("markets") or {})
    errors=dict(payload.get("errors") or {})
    try:
        row=volatility_forecast(market)
        markets[market]=row
        errors.pop(market,None)
    except Exception as exc:
        errors[market]=f"{type(exc).__name__}:{exc}"
        payload.update({
            "version":VERSION,
            "model":MODEL,
            "markets":markets,
            "errors":errors,
            "research_only":True,
            "cache_state":"READY" if markets else "EMPTY",
            "updated_at":datetime.now(timezone.utc).isoformat(),
        })
        store.save_json(CACHE_FILE,payload)
        raise
    payload.update({
        "version":VERSION,
        "model":MODEL,
        "markets":markets,
        "errors":errors,
        "research_only":True,
        "cache_state":"READY",
        "updated_at":datetime.now(timezone.utc).isoformat(),
    })
    store.save_json(CACHE_FILE,payload)
    return row
