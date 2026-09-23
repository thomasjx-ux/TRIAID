from __future__ import annotations

from triaid_fin.market_data import (
    MarketDataHub, MarketDataError, ProviderSeries, MODE_CONFIGS,
)

class FakeHKProvider:
    version="fake-hk@1"
    configured=True
    def fetch_series(self,symbol,*,range_,interval,include_prepost,min_points,timeout=15):
        base=list(range(1_000_000,1_000_000+40*300,300))
        if symbol=="2819.HK":
            ts=base[-4:]
        else:
            ts=base
        if len(ts)<min_points:
            raise MarketDataError(f"insufficient_points:{symbol}:{interval}:{len(ts)}<{min_points}")
        return ProviderSeries(
            symbol=symbol,
            ts=ts,
            close=[100.0+i*0.01 for i in range(len(ts))],
            volume=[1000.0]*len(ts),
        )

hub=MarketDataHub(provider=FakeHKProvider())
symbols=("2800.HK","2828.HK","3033.HK","2819.HK")
panel=hub._panel_from_provider(
    hub.provider,"HK","INTRADAY",symbols,"2800.HK",MODE_CONFIGS["INTRADAY"],0.0
)
assert len(panel.ts)==40
assert set(panel.close)=={"2800.HK","2828.HK","3033.HK"}
assert "2819.HK" not in panel.close
assert any("2819.HK:sparse_alignment" in x for x in (panel.degraded_symbols or []))
assert panel.quality.endswith("_partial_optional_symbol")
meta=panel.metadata()
assert meta["partial_symbol_policy"]=="HK_HIGH_FREQUENCY_SPARSE_DEFENSIVE_NO_INTERPOLATION"
assert meta["requested_symbols"]==list(symbols)

failed=False
try:
    hub._panel_from_provider(
        hub.provider,"HK","DAILY",symbols,"2800.HK",MODE_CONFIGS["DAILY"],0.0
    )
except MarketDataError:
    failed=True
assert failed is True

print("TRIAID_HK_HIGH_FREQUENCY_DEGRADATION_SMOKE_PASS",{
    "points":len(panel.ts),
    "available_symbols":sorted(panel.close),
    "degraded_symbols":panel.degraded_symbols,
    "daily_strict_failure":failed,
})
