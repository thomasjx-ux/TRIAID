from triaid_fin.eastmoney_data import EastmoneyMarketDataProvider

provider=EastmoneyMarketDataProvider()

cases=[
    ("SPY","1d",300,False),
    ("QQQ","5m",30,False),
    ("IWM","1m",2,False),
    ("510300.SS","1d",300,False),
    ("159915.SZ","5m",30,False),
    ("512100.SS","1m",2,False),
]

rows=[]
for symbol,interval,min_points,prepost in cases:
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
        "symbol":symbol,
        "interval":interval,
        "points":len(s.ts),
        "latest_ts":s.ts[-1],
        "latest_close":s.close[-1],
    })

print("TRIAID_BACKUP_PROVIDER_SMOKE_PASS")
for row in rows:
    print("TRIAID_BACKUP_PROVIDER_CASE",row)
