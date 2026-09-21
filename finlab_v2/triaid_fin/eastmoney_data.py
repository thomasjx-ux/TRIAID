from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class EastmoneyDataError(RuntimeError):
    pass


class EastmoneyMarketDataProvider:
    name="eastmoney-market-data"
    version="eastmoney-market-data@0.2.0"
    push_token="7eea3edcaed734bea9cbfc24409ed989"
    suggest_token="D43BF722C8E33BDC906FB84D85E326E8"

    def __init__(self)->None:
        self._us_secid_cache:dict[str,str]={}

    @property
    def configured(self)->bool:
        return True

    @staticmethod
    def _finite(value)->bool:
        return isinstance(value,(int,float)) and math.isfinite(value)

    def _get_json(self,url:str,params:dict,timeout:int)->dict:
        query=urllib.parse.urlencode(params)
        req=urllib.request.Request(
            url+"?"+query,
            headers={
                "User-Agent":"Mozilla/5.0 TRIAID-FIN-V2-EASTMONEY/0.1",
                "Referer":"https://quote.eastmoney.com/",
                "Accept":"application/json,text/plain,*/*",
            },
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                raw=response.read().decode("utf-8","replace").strip()
        except Exception as exc:
            raise EastmoneyDataError(f"fetch_failed:{type(exc).__name__}:{exc}") from exc

        if raw.startswith("{"):
            try:
                return json.loads(raw)
            except Exception as exc:
                raise EastmoneyDataError(f"json_parse_failed:{exc}") from exc

        match=re.search(r"\((\{.*\})\)\s*;?$",raw,re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception as exc:
                raise EastmoneyDataError(f"jsonp_parse_failed:{exc}") from exc
        raise EastmoneyDataError("unexpected_response_format")

    def _resolve_cn_secid(self,symbol:str)->str:
        upper=symbol.upper()
        code=upper.split(".")[0]
        if upper.endswith(".SS") or upper.endswith(".SH"):
            return f"1.{code}"
        if upper.endswith(".SZ"):
            return f"0.{code}"
        if code.startswith(("5","6","9")):
            return f"1.{code}"
        return f"0.{code}"

    def _resolve_us_secid(self,symbol:str,timeout:int)->str:
        ticker=symbol.upper().replace(".US","")
        cached=self._us_secid_cache.get(ticker)
        if cached:
            return cached

        try:
            payload=self._get_json(
                "https://searchapi.eastmoney.com/api/suggest/get",
                {
                    "input":ticker,
                    "type":14,
                    "token":self.suggest_token,
                    "count":10,
                },
                timeout,
            )
            rows=((payload.get("QuotationCodeTable") or {}).get("Data") or [])
            for row in rows:
                code=str(row.get("Code") or row.get("SecurityCode") or "").upper()
                quote_id=str(row.get("QuoteID") or row.get("QuoteId") or "")
                mkt=str(row.get("MktNum") or row.get("Market") or "")
                if code==ticker and (quote_id or mkt in {"105","106","107"}):
                    secid=quote_id if "." in quote_id else f"{mkt}.{ticker}"
                    self._us_secid_cache[ticker]=secid
                    return secid
        except Exception:
            pass

        for prefix in ("105","106","107"):
            secid=f"{prefix}.{ticker}"
            try:
                payload=self._get_json(
                    "https://63.push2his.eastmoney.com/api/qt/stock/kline/get",
                    {
                        "fields1":"f1,f2,f3,f4,f5,f6",
                        "fields2":"f51,f52,f53,f54,f55,f56",
                        "ut":self.push_token,
                        "klt":"101",
                        "fqt":"0",
                        "secid":secid,
                        "beg":"19700101",
                        "end":"20500101",
                        "lmt":"1",
                    },
                    timeout,
                )
                data=payload.get("data") or {}
                if data.get("klines"):
                    self._us_secid_cache[ticker]=secid
                    return secid
            except Exception:
                continue
        raise EastmoneyDataError(f"us_symbol_unresolved:{ticker}")

    def _resolve(self,symbol:str,timeout:int)->tuple[str,str]:
        upper=symbol.upper()
        if upper.endswith((".SS",".SH",".SZ")):
            return "CN",self._resolve_cn_secid(upper)
        return "US",self._resolve_us_secid(upper,timeout)

    @staticmethod
    def _daily_timestamp(raw:str)->int:
        try:
            day=datetime.strptime(raw[:10],"%Y-%m-%d").date()
            return int(datetime(day.year,day.month,day.day,12,0,tzinfo=timezone.utc).timestamp())
        except Exception as exc:
            raise EastmoneyDataError(f"bad_daily_time:{raw}") from exc

    @staticmethod
    def _minute_timestamp(raw:str,market:str)->int:
        try:
            value=raw.strip()[:16]
            tz=ZoneInfo("America/New_York" if str(market).upper()=="US" else "Asia/Shanghai")
            dt=datetime.strptime(value,"%Y-%m-%d %H:%M").replace(tzinfo=tz)
            return int(dt.timestamp())
        except Exception as exc:
            raise EastmoneyDataError(f"bad_minute_time:{market}:{raw}") from exc

    def _kline(
        self,
        symbol:str,
        secid:str,
        market:str,
        klt:str,
        min_points:int,
        timeout:int,
    ):
        url=(
            "https://63.push2his.eastmoney.com/api/qt/stock/kline/get"
            if market=="US"
            else "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        )
        payload=self._get_json(
            url,
            {
                "fields1":"f1,f2,f3,f4,f5,f6",
                "fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "ut":self.push_token,
                "klt":klt,
                "fqt":"1" if klt=="101" else "0",
                "secid":secid,
                "beg":"19700101" if klt=="101" else "0",
                "end":"20500101" if klt=="101" else "20500000",
                "lmt":"10000",
            },
            timeout,
        )
        data=payload.get("data")
        rows=(data or {}).get("klines") if isinstance(data,dict) else None
        if not isinstance(rows,list) or not rows:
            raise EastmoneyDataError(f"empty_kline:{symbol}:{klt}")

        parsed=[]
        for line in rows:
            cols=str(line).split(",")
            if len(cols)<6:
                continue
            try:
                price=float(cols[2])
                volume=float(cols[5] or 0.0)
                stamp=self._daily_timestamp(cols[0]) if klt=="101" else self._minute_timestamp(cols[0],market)
            except Exception:
                continue
            if price>0 and self._finite(price):
                parsed.append((stamp,price,max(0.0,volume)))

        if len(parsed)<min_points:
            raise EastmoneyDataError(
                f"insufficient_points:{symbol}:{klt}:{len(parsed)}<{min_points}"
            )
        return parsed

    def _trend(
        self,
        symbol:str,
        secid:str,
        market:str,
        min_points:int,
        timeout:int,
    ):
        url=(
            "https://63.push2his.eastmoney.com/api/qt/stock/trends2/get"
            if market=="US"
            else "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
        )
        payload=self._get_json(
            url,
            {
                "fields1":"f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                "fields2":"f51,f52,f53,f54,f55,f56,f57,f58",
                "ut":self.push_token,
                "ndays":"5",
                "iscr":"0",
                "secid":secid,
            },
            timeout,
        )
        data=payload.get("data")
        rows=(data or {}).get("trends") if isinstance(data,dict) else None
        if not isinstance(rows,list) or not rows:
            raise EastmoneyDataError(f"empty_trends:{symbol}")

        parsed=[]
        for line in rows:
            cols=str(line).split(",")
            if len(cols)<6:
                continue
            try:
                price=float(cols[2])
                volume=float(cols[5] or 0.0)
                stamp=self._minute_timestamp(cols[0],market)
            except Exception:
                continue
            if price>0 and self._finite(price):
                parsed.append((stamp,price,max(0.0,volume)))

        if len(parsed)<min_points:
            raise EastmoneyDataError(
                f"insufficient_points:{symbol}:1m:{len(parsed)}<{min_points}"
            )
        return parsed

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
        # Eastmoney US minute data is regular-session only. It is deliberately
        # not used as a PREOPEN replacement.
        market,secid=self._resolve(symbol,timeout)
        if include_prepost and market=="US":
            raise EastmoneyDataError("us_extended_hours_not_supported")

        if interval=="1d":
            rows=self._kline(symbol,secid,market,"101",min_points,timeout)
        elif interval=="5m":
            rows=self._kline(symbol,secid,market,"5",min_points,timeout)
        elif interval=="1m":
            rows=self._trend(symbol,secid,market,min_points,timeout)
        else:
            raise EastmoneyDataError(f"unsupported_interval:{interval}")

        # Local import avoids coupling this adapter to the hub at import time.
        from .market_data import ProviderSeries
        return ProviderSeries(
            symbol=symbol,
            ts=[x[0] for x in rows],
            close=[x[1] for x in rows],
            volume=[x[2] for x in rows],
        )
