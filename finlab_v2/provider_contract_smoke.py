from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()

providers=engine.market_data_provider_status()
products=engine.market_data_product_capabilities()

assert providers["bar_provider"]["configured"] is True
assert products["US"]["ORDERBOOK_L2"]["available"] is False
assert products["US"]["BROKER_FILLS"]["available"] is False
assert products["CN"]["PREOPEN_AUCTION"]["available"] is False
assert products["CN"]["ORDERBOOK_L2"]["available"] is False

us_stock=engine.market_data_instrument_series("US","AAPL","DAILY")
assert us_stock["points"]>=300
assert us_stock["execution_grade"] is False

cn_stock=engine.market_data_instrument_series("CN","600519.SS","DAILY")
assert cn_stock["points"]>=300
assert cn_stock["execution_grade"] is False

us_quotes=engine.market_data_latest_quotes("US",["SPY","QQQ"])
if providers["us_l1_quote_provider"]["configured"]:
    assert us_quotes["available"] is True
    assert us_quotes["execution_grade"] is False
else:
    assert us_quotes["available"] is False
    assert us_quotes["reason"]=="ALPACA_CREDENTIALS_NOT_CONFIGURED"

cn_quotes=engine.market_data_latest_quotes("CN",["510300.SS"])
assert cn_quotes["available"] is False
assert cn_quotes["reason"]=="NO_AUTHORIZED_L1_PROVIDER"

print("TRIAID_PROVIDER_CONTRACT_SMOKE_PASS")
print("TRIAID_PROVIDER_STATUS",providers)
print("TRIAID_PRODUCT_STATUS",products)
print("TRIAID_US_STOCK_ON_DEMAND",us_stock)
print("TRIAID_CN_STOCK_ON_DEMAND",cn_stock)
print("TRIAID_US_L1_STATUS",us_quotes)
print("TRIAID_CN_L1_STATUS",cn_quotes)
