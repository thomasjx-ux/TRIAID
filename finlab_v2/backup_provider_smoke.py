from triaid_fin.sina_us_data import SinaUSMarketDataProvider
from triaid_fin.tencent_cn_data import TencentCNMarketDataProvider

us=SinaUSMarketDataProvider()
cn=TencentCNMarketDataProvider()

cases=[
    (us,"SPY","1d",300,False),
    (us,"QQQ","5m",30,False),
    (us,"IWM","1m",2,False),
    (cn,"510300.SS","1d",300,False),
    (cn,"159915.SZ","5m",30,False),
    (cn,"512100.SS","1m",2,False),
]

rows=[]
for provider,symbol,interval,min_points,prepost in cases:
    s=provider.fetch_series(
        symbol,
        range_="10y" if interval=="1d" else ("5d" if interval=="5m" else "1d"),
        interval=interval,
        include_prepost=prepost,
        min_points=min_points,
        timeout=20,
    )
    assert len(s.ts)>=min_points
    assert len(s.ts)==len(s.close)==len(s.volume)
    assert s.ts[-1]>0
    assert s.close[-1]>0
    rows.append({
        "provider":provider.version,
        "symbol":symbol,
        "interval":interval,
        "points":len(s.ts),
        "latest_ts":s.ts[-1],
        "latest_close":s.close[-1],
    })

print("TRIAID_BACKUP_PROVIDER_SMOKE_PASS")
for row in rows:
    print("TRIAID_BACKUP_PROVIDER_CASE",row)
