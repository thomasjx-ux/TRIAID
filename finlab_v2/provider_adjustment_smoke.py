from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from triaid_fin.market_data import YahooChartProvider
from triaid_fin.tencent_cn_data import TencentCNMarketDataProvider
from triaid_fin.eastmoney_data import EastmoneyMarketDataProvider


class FakeResponse:
    def __init__(self,payload):
        self.payload=payload
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def read(self):
        return json.dumps(self.payload).encode("utf-8")


payload={
    "chart":{
        "error":None,
        "result":[{
            "timestamp":[1_700_000_000,1_700_086_400],
            "indicators":{
                "quote":[{
                    "close":[100.0,50.0],
                    "volume":[1000.0,2000.0],
                }],
                "adjclose":[{
                    "adjclose":[50.0,50.0],
                }],
            },
        }],
    }
}
original=urllib.request.urlopen
urllib.request.urlopen=lambda *args,**kwargs:FakeResponse(payload)
try:
    s=YahooChartProvider().fetch_series(
        "TEST",range_="1y",interval="1d",include_prepost=False,min_points=2
    )
finally:
    urllib.request.urlopen=original

assert s.close==[50.0,50.0]
assert abs(s.close[0]*s.volume[0]-100.0*1000.0)<1e-9
assert abs(s.close[1]*s.volume[1]-50.0*2000.0)<1e-9

tencent=TencentCNMarketDataProvider()
tencent._get=lambda *args,**kwargs:{
    "data":{
        "sh510300":{
            "qfqday":[
                ["2026-09-18","0","5.0","0","0","2000"],
                ["2026-09-21","0","10.0","0","0","1000"],
            ],
            "day":[
                ["2026-09-18","0","10.0","0","0","2000"],
                ["2026-09-21","0","10.0","0","0","1000"],
            ],
        }
    }
}
rows=tencent._daily("510300.SS",2,15)
assert rows[0][1]==5.0
assert abs(rows[0][1]*rows[0][2]-10.0*2000.0)<1e-9
assert abs(rows[1][1]*rows[1][2]-10.0*1000.0)<1e-9

east=EastmoneyMarketDataProvider()
us=east._minute_timestamp("2026-09-21 09:30","US")
cn=east._minute_timestamp("2026-09-21 09:30","CN")
assert us==int(datetime(2026,9,21,9,30,tzinfo=ZoneInfo("America/New_York")).timestamp())
assert cn==int(datetime(2026,9,21,9,30,tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
assert us!=cn

print("TRIAID_PROVIDER_ADJUSTMENT_SMOKE_PASS")
