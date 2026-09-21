from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


class AlpacaDataError(RuntimeError):
    pass


class AlpacaMarketDataProvider:
    name="alpaca-market-data"
    version="alpaca-market-data@0.1.0"
    base_url="https://data.alpaca.markets"

    def __init__(self)->None:
        self.key_id=os.getenv("ALPACA_API_KEY_ID","").strip()
        self.secret=os.getenv("ALPACA_API_SECRET_KEY","").strip()
        self.feed=os.getenv("ALPACA_DATA_FEED","iex").strip() or "iex"

    @property
    def configured(self)->bool:
        return bool(self.key_id and self.secret)

    def configuration_status(self)->dict:
        return {
            "configured":self.configured,
            "feed":self.feed,
            "provider":self.version,
            "credentials_present":bool(self.key_id and self.secret),
        }

    def _get(self,path:str,params:dict|None=None,timeout:int=15)->dict:
        if not self.configured:
            raise AlpacaDataError("alpaca_credentials_missing")
        query=urllib.parse.urlencode(params or {},doseq=True)
        url=self.base_url+path+("?" + query if query else "")
        req=urllib.request.Request(
            url,
            headers={
                "APCA-API-KEY-ID":self.key_id,
                "APCA-API-SECRET-KEY":self.secret,
                "Accept":"application/json",
                "User-Agent":"TRIAID-FIN-V2/0.7",
            },
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise AlpacaDataError(f"alpaca_fetch_failed:{type(exc).__name__}:{exc}") from exc

    def latest_quotes(self,symbols:list[str]|tuple[str,...])->dict:
        payload=self._get(
            "/v2/stocks/quotes/latest",
            {"symbols":",".join(symbols),"feed":self.feed},
        )
        quotes=payload.get("quotes") or {}
        out={}
        for symbol in symbols:
            q=quotes.get(symbol)
            if not q:
                continue
            out[symbol]={
                "bid_price":q.get("bp"),
                "bid_size":q.get("bs"),
                "ask_price":q.get("ap"),
                "ask_size":q.get("as"),
                "timestamp":q.get("t"),
                "bid_exchange":q.get("bx"),
                "ask_exchange":q.get("ax"),
                "conditions":q.get("c") or [],
                "feed":self.feed,
            }
        return {
            "provider":self.version,
            "feed":self.feed,
            "symbols":out,
            "execution_grade":False,
            "note":"L1 quote adapter. Execution-grade classification requires explicit entitlement and separate broker-fill validation.",
        }

    def latest_bars(self,symbols:list[str]|tuple[str,...])->dict:
        payload=self._get(
            "/v2/stocks/bars/latest",
            {"symbols":",".join(symbols),"feed":self.feed},
        )
        bars=payload.get("bars") or {}
        out={}
        for symbol in symbols:
            b=bars.get(symbol)
            if not b:
                continue
            out[symbol]={
                "open":b.get("o"),
                "high":b.get("h"),
                "low":b.get("l"),
                "close":b.get("c"),
                "volume":b.get("v"),
                "trade_count":b.get("n"),
                "vwap":b.get("vw"),
                "timestamp":b.get("t"),
                "feed":self.feed,
            }
        return {
            "provider":self.version,
            "feed":self.feed,
            "symbols":out,
            "execution_grade":False,
        }

    def bars(
        self,
        symbol:str,
        *,
        timeframe:str,
        days:int,
        limit:int=10000,
    )->list[dict]:
        end=datetime.now(timezone.utc)
        start=end-timedelta(days=days)
        params={
            "timeframe":timeframe,
            "start":start.isoformat().replace("+00:00","Z"),
            "end":end.isoformat().replace("+00:00","Z"),
            "limit":limit,
            "adjustment":"raw",
            "feed":self.feed,
            "sort":"asc",
        }
        payload=self._get(f"/v2/stocks/{urllib.parse.quote(symbol)}/bars",params)
        return list(payload.get("bars") or [])
