from __future__ import annotations

import triaid_fin.volatility_forecast as vf


class MemoryStore:
    def __init__(self)->None:
        self.rows={}
    def load_json(self,name,default=None):
        return self.rows.get(name,{} if default is None else default)
    def save_json(self,name,payload):
        self.rows[name]=payload


def row(market:str)->dict:
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
    empty=vf.cached_all_market_volatility_forecasts(store)
    assert empty["markets"]=={}
    assert empty["cache_state"]=="EMPTY"

    original=vf.volatility_forecast
    calls=[]
    try:
        vf.volatility_forecast=lambda market: calls.append(market) or row(market)
        refreshed=vf.refresh_all_market_volatility_forecasts(store,require_all=True)
        assert sorted(calls)==["CN","HK","US"]
        assert set(refreshed["markets"])=={"US","CN","HK"}

        calls.clear()
        cached=vf.cached_all_market_volatility_forecasts(store)
        assert calls==[]
        assert cached["cache_state"]=="READY"
        assert vf.cached_volatility_forecast(store,"US")["market_id"]=="US"
        assert calls==[]

        vf.volatility_forecast=lambda market: (_ for _ in ()).throw(RuntimeError("provider-down"))
        assert vf.cached_all_market_volatility_forecasts(store)["markets"]["CN"]["market_id"]=="CN"
    finally:
        vf.volatility_forecast=original

    print("TRIAID_VOLATILITY_PERSISTENT_CACHE_SMOKE_PASS")


if __name__=="__main__":
    main()
