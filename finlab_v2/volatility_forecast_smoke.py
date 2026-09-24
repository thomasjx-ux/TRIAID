from __future__ import annotations

import math

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

    print("TRIAID_VOLATILITY_FORECAST_SMOKE_PASS")
    print({
        "low_sigma":low_model["sigma"],
        "high_sigma":high_model["sigma"],
        "low_coverage_68":low_wf["coverage_68"],
        "high_coverage_68":high_wf["coverage_68"],
    })


if __name__=="__main__":
    main()
