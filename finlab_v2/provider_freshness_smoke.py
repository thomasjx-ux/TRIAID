from __future__ import annotations

from triaid_fin.market_data import MarketDataHub, ProviderSeries


class FakeProvider:
    configured=True

    def __init__(self, version: str, latest_ts: int) -> None:
        self.version=version
        self.latest_ts=latest_ts

    def fetch_series(self, symbol: str, **kwargs) -> ProviderSeries:
        prev=self.latest_ts-60
        return ProviderSeries(
            symbol=symbol,
            ts=[prev,self.latest_ts],
            close=[100.0,101.0],
            volume=[1000.0,1200.0],
        )


def main() -> None:
    primary=FakeProvider("primary-selftest@1",110)
    backup=FakeProvider("backup-selftest@1",120)
    hub=MarketDataHub(provider=primary)
    hub.registry.register("fresh_backup",backup)
    hub.registry.route("CN:REALTIME","research_bars")
    hub.registry.add_fallback("CN:REALTIME","fresh_backup")

    panel=hub.refresh_panel(
        "CN",["510300.SS"],"510300.SS","REALTIME",force=True
    )
    assert panel.provider=="backup-selftest@1"
    assert panel.source_latest_ts==120

    primary.latest_ts=115
    backup.latest_ts=118
    panel2=hub.refresh_panel(
        "CN",["510300.SS"],"510300.SS","REALTIME",force=True
    )
    assert panel2.source_latest_ts==120
    assert panel2.provider=="backup-selftest@1"

    primary.latest_ts=130
    backup.latest_ts=125
    panel3=hub.refresh_panel(
        "CN",["510300.SS"],"510300.SS","REALTIME",force=True
    )
    assert panel3.provider=="primary-selftest@1"
    assert panel3.source_latest_ts==130

    print("PROVIDER_FRESHNESS_SMOKE PASS")


if __name__=="__main__":
    main()
