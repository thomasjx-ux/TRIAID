import os
from unittest.mock import patch

from triaid_fin.tushare_auction import TushareETFAuctionProvider

p=TushareETFAuctionProvider()
with patch.dict(os.environ,{"TUSHARE_TOKEN":"test-token"},clear=False):
    assert p.configured is True
    with patch.object(p,"_post",return_value={
        "code":0,
        "msg":None,
        "data":{
            "fields":["ts_code","trade_date","vol","price","amount","pre_close","turnover_rate","volume_ratio"],
            "items":[["510300.SH","20260922",123456,4.66,575304.96,4.608,0.1,1.2]],
        },
    }):
        s=p.fetch_series("510300.SS",range_="1d",interval="5m",include_prepost=True,min_points=1)
        assert s.symbol=="510300.SS"
        assert len(s.ts)==1
        assert abs(s.close[0]-4.66)<1e-12
        assert s.volume[0]==123456
print("TRIAID_TUSHARE_AUCTION_SMOKE_PASS")
