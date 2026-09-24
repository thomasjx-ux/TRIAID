from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine


def main()->None:
    engine=EvolutionLabEngine()
    payload=engine.refresh_volatility_forecasts(require_all=True)
    markets=payload.get("markets") or {}
    assert set(markets)=={"US","CN","HK"}, payload
    for market,row in markets.items():
        assert float(row.get("forecast_move_pct") or 0.0)>0.0, (market,row)
        wf=row.get("walk_forward") or {}
        assert int(wf.get("sample_count") or 0)>=60, (market,wf)
    cached=engine.volatility_forecasts()
    assert set(cached.get("markets") or {})=={"US","CN","HK"}, cached
    assert cached.get("cache_state")=="READY", cached
    print(
        "TRIAID_VOLATILITY_FORECAST_LIVE_BOOTSTRAP_PASS",
        {
            "markets":sorted(markets),
            "cache_state":cached.get("cache_state"),
            "updated_at":cached.get("updated_at"),
        },
    )


if __name__=="__main__":
    main()
