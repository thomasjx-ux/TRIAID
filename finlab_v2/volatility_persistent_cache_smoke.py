from __future__ import annotations

import triaid_fin.volatility_forecast as vf


class MemoryStore:
    def __init__(self)->None:
        self.rows={}
    def load_json(self,name,default=None):
        return self.rows.get(name,{} if default is None else default)
    def save_json(self,name,payload):
        self.rows[name]=payload


def sample(market:str)->dict:
    return {
        "version":vf.VERSION,
        "model":vf.MODEL,
        "market_id":market,
        "forecast_move_pct":1.0,
        "expected_abs_move_pct":0.8,
        "range_68":{"lower":99.0,"upper":101.0},
        "walk_forward":{
            "sample_count":100,
            "coverage_68":0.68,
            "rms_calibration_ratio":1.0,
            "calibration_quality":"WELL_CALIBRATED",
        },
    }


def main()->None:
    store=MemoryStore()
    assert vf.cached_all_market_volatility_forecasts(store)["cache_state"]=="EMPTY"

    original_all=vf.all_market_volatility_forecasts
    original_one=vf.volatility_forecast
    calls=[]
    try:
        vf.all_market_volatility_forecasts=lambda force_refresh=False: {
            "version":vf.VERSION,
            "model":vf.MODEL,
            "markets":{m:sample(m) for m in ("US","CN","HK")},
            "errors":{},
            "research_only":True,
            "cache_hit":False,
        }
        first=vf.refresh_all_market_volatility_forecasts(store,require_all=True)
        assert first["cache_state"]=="READY"
        assert set(first["markets"])=={"US","CN","HK"}

        vf.all_market_volatility_forecasts=lambda force_refresh=False: (_ for _ in ()).throw(RuntimeError("provider-down"))
        cached=vf.cached_all_market_volatility_forecasts(store)
        assert set(cached["markets"])=={"US","CN","HK"}

        vf.volatility_forecast=lambda market: calls.append(market) or sample(market)
        vf.refresh_market_volatility_forecast(store,"US")
        assert calls==["US"]

        vf.volatility_forecast=lambda market: (_ for _ in ()).throw(RuntimeError("provider-down"))
        assert vf.cached_volatility_forecast(store,"US")["market_id"]=="US"
    finally:
        vf.all_market_volatility_forecasts=original_all
        vf.volatility_forecast=original_one

    print("TRIAID_VOLATILITY_PERSISTENT_CACHE_SMOKE_PASS")


if __name__=="__main__":
    main()
