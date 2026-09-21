from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
caps=engine.market_data_capabilities()

assert caps["CN"]["PREOPEN"]["supported"] is False
assert caps["US"]["REALTIME"]["execution_grade"] is False

rows=[]
for market in ("US","CN"):
    for mode in ("DAILY","INTRADAY","REALTIME"):
        x=engine.refresh_market_data(market,mode)
        assert x["points"]>=2
        assert x["source_latest_ts"]>0
        assert x["execution_grade"] is False
        rows.append({
            "market":market,
            "mode":mode,
            "points":x["points"],
            "symbols":x["symbols"],
            "quality":x["quality"],
            "latest_ts":x["source_latest_ts"],
        })

pre=engine.refresh_market_data("US","PREOPEN")
assert pre["points"]>=2
rows.append({
    "market":"US",
    "mode":"PREOPEN",
    "points":pre["points"],
    "symbols":pre["symbols"],
    "quality":pre["quality"],
    "latest_ts":pre["source_latest_ts"],
})

try:
    engine.refresh_market_data("CN","PREOPEN")
except Exception as exc:
    cn_preopen_error=f"{type(exc).__name__}:{exc}"
else:
    raise AssertionError("CN PREOPEN must remain unsupported until a dedicated auction provider is connected")

print("TRIAID_MARKET_DATA_SMOKE_PASS")
for row in rows:
    print("TRIAID_MARKET_DATA_MODE",row)
print("TRIAID_CN_PREOPEN_EXPECTED_UNSUPPORTED",cn_preopen_error)
