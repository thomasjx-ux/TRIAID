from __future__ import annotations

import math
from threading import Barrier

import triaid_fin.volatility_forecast as vf
from triaid_fin.volatility_forecast import _log_returns, _sigma_from_returns, _walk_forward


def make_prices(n:int,amp:float)->list[float]:
    prices=[100.0]
    for i in range(1,n):
        r=amp*math.sin(i*0.37)+0.35*amp*math.sin(i*1.11)
        prices.append(prices[-1]*math.exp(r))
    return prices


def main()->None:
    low=make_prices(360,0.004)
    high=make_prices(360,0.016)

    low_model=_sigma_from_returns(_log_returns(low))
    high_model=_sigma_from_returns(_log_returns(high))
    assert high_model["sigma"]>low_model["sigma"]>0.0

    low_wf=_walk_forward(low,252)
    high_wf=_walk_forward(high,252)
    for row in (low_wf,high_wf):
        assert row["sample_count"]>=200
        assert 0.0<=row["coverage_68"]<=1.0
        assert 0.0<=row["coverage_95"]<=1.0
        assert row["mae_abs_move"]>=0.0
        assert row["rms_calibration_ratio"]>0.0

    original_forecast=vf.volatility_forecast
    original_payload=vf._CACHE_PAYLOAD
    original_at=vf._CACHE_AT
    barrier=Barrier(3,timeout=2.0)
    try:
        def fake_forecast(market_id:str)->dict:
            barrier.wait()
            return {"market_id":market_id,"forecast_move_pct":1.0}

        vf.volatility_forecast=fake_forecast
        vf._CACHE_PAYLOAD=None
        vf._CACHE_AT=0.0
        payload=vf.all_market_volatility_forecasts(force_refresh=True)
        assert set(payload["markets"])=={"US","CN","HK"}
        assert payload["errors"]=={}
        assert payload["cache_hit"] is False
        cached=vf.all_market_volatility_forecasts()
        assert cached["cache_hit"] is True
        assert set(cached["markets"])=={"US","CN","HK"}
    finally:
        vf.volatility_forecast=original_forecast
        vf._CACHE_PAYLOAD=original_payload
        vf._CACHE_AT=original_at

    print("TRIAID_VOLATILITY_FORECAST_SMOKE_PASS")
    print({
        "low_sigma":low_model["sigma"],
        "high_sigma":high_model["sigma"],
        "low_coverage_68":low_wf["coverage_68"],
        "high_coverage_68":high_wf["coverage_68"],
    })


if __name__=="__main__":
    main()
