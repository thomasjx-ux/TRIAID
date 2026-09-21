from triaid_fin.tencent_cn_data import TencentCNMarketDataProvider
from unittest.mock import patch

p=TencentCNMarketDataProvider()
fake=[
    (1790030700,4.60,100.0),
    (1790030700+300,4.61,120.0),
]
# Use explicit Shanghai 09:25 timestamp so the probe contract is deterministic.
from datetime import datetime
from zoneinfo import ZoneInfo
stamp=int(datetime(2026,9,22,9,25,tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
with patch.object(p,"_one_minute",return_value=[(stamp,4.61,120.0)]):
    r=p.auction_shadow_probe("510300.SS")
    assert r["available"] is True
    assert r["role"]=="SHADOW_ZERO_COST_VALIDATION_ONLY"
print("TRIAID_TENCENT_AUCTION_SHADOW_SMOKE_PASS")
