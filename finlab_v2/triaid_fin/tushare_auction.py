from __future__ import annotations

import json
import math
import os
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


class TushareAuctionError(RuntimeError):
    pass


class TushareETFAuctionProvider:
    """A-share ETF opening-auction final snapshot provider.

    Tushare etf_auction publishes the current trading day's ETF opening-auction
    result after the 09:25 match. This provider is intentionally limited to
    CN PREOPEN auction-final use and does not fabricate 09:15-09:25 dynamics.
    """

    name="tushare-etf-auction"
    version="tushare-etf-auction@0.1.0"
    endpoint="https://api.tushare.pro"

    @property
    def configured(self)->bool:
        return bool(os.getenv("TUSHARE_TOKEN","").strip())

    @staticmethod
    def _ts_code(symbol:str)->str:
        upper=symbol.upper()
        code=upper.split(".")[0]
        if upper.endswith((".SS",".SH")):
            return f"{code}.SH"
        if upper.endswith(".SZ"):
            return f"{code}.SZ"
        raise TushareAuctionError(f"unsupported_symbol:{symbol}")

    def _post(self,payload:dict,timeout:int)->dict:
        token=os.getenv("TUSHARE_TOKEN","").strip()
        if not token:
            raise TushareAuctionError("not_configured:TUSHARE_TOKEN")
        body=json.dumps({**payload,"token":token},ensure_ascii=False).encode("utf-8")
        req=urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type":"application/json",
                "Accept":"application/json",
                "User-Agent":"TRIAID-FIN-V2-TUSHARE-AUCTION/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8","replace"))
        except Exception as exc:
            raise TushareAuctionError(f"fetch_failed:{type(exc).__name__}:{exc}") from exc

    def fetch_series(
        self,
        symbol:str,
        *,
        range_:str,
        interval:str,
        include_prepost:bool,
        min_points:int,
        timeout:int=15,
    ):
        del range_,interval,include_prepost,min_points
        ts_code=self._ts_code(symbol)
        trade_date=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        payload=self._post(
            {
                "api_name":"etf_auction",
                "params":{"ts_code":ts_code,"trade_date":trade_date},
                "fields":"ts_code,trade_date,vol,price,amount,pre_close,turnover_rate,volume_ratio",
            },
            timeout,
        )
        code=int(payload.get("code") or 0)
        if code!=0:
            raise TushareAuctionError(f"provider_error:{code}:{payload.get('msg')}")
        data=payload.get("data") or {}
        fields=list(data.get("fields") or [])
        items=list(data.get("items") or [])
        if not fields or not items:
            raise TushareAuctionError(f"auction_not_ready:{ts_code}:{trade_date}")
        row=dict(zip(fields,items[0]))
        try:
            price=float(row.get("price"))
            volume=float(row.get("vol") or 0.0)
        except Exception as exc:
            raise TushareAuctionError(f"invalid_auction_row:{ts_code}:{row}") from exc
        if not math.isfinite(price) or price<=0:
            raise TushareAuctionError(f"invalid_auction_price:{ts_code}:{price}")
        if not math.isfinite(volume) or volume<0:
            volume=0.0
        day=str(row.get("trade_date") or trade_date)
        dt=datetime.strptime(day+"0925","%Y%m%d%H%M").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        from .market_data import ProviderSeries
        return ProviderSeries(
            symbol=symbol,
            ts=[int(dt.timestamp())],
            close=[price],
            volume=[volume],
        )
