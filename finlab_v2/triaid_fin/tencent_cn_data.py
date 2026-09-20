from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class TencentCNDataError(RuntimeError):
    pass


class TencentCNMarketDataProvider:
    name="tencent-cn-market-data"
    version="tencent-cn-market-data@0.1.0"

    @property
    def configured(self)->bool:
        return True

    def _symbol(self,symbol:str)->str:
        upper=symbol.upper()
        code=upper.split(".")[0]
        if upper.endswith((".SS",".SH")) or code.startswith(("5","6","9")):
            return f"sh{code}"
        return f"sz{code}"

    def _get(self,url:str,params:dict,timeout:int)->dict:
        query=urllib.parse.urlencode(params)
        req=urllib.request.Request(
            url+"?"+query,
            headers={
                "User-Agent":"Mozilla/5.0 TRIAID-FIN-V2-TENCENT/0.1",
                "Referer":"https://gu.qq.com/",
                "Accept":"application/json,text/plain,*/*",
            },
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                raw=response.read().decode("utf-8","replace")
            return json.loads(raw)
        except Exception as exc:
            raise TencentCNDataError(f"fetch_failed:{type(exc).__name__}:{exc}") from exc

    @staticmethod
    def _daily_ts(raw:str)->int:
        day=raw[:10]
        if len(day)==8 and "-" not in day:
            day=f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        d=datetime.strptime(day,"%Y-%m-%d")
        return int(d.replace(tzinfo=timezone.utc,hour=12).timestamp())

    @staticmethod
    def _minute_ts(raw:str)->int:
        text=raw.strip()
        if len(text)>=12 and text[:12].isdigit():
            value=f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"
        else:
            value=text[:16]
        dt=datetime.strptime(value,"%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return int(dt.timestamp())

    def _daily(self,symbol:str,min_points:int,timeout:int):
        code=self._symbol(symbol)
        payload=self._get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            {"param":f"{code},day,,,640,hfq"},
            timeout,
        )
        node=(payload.get("data") or {}).get(code) or {}
        rows=node.get("hfqday") or node.get("day") or []
        parsed=[]
        for raw in rows:
            if not isinstance(raw,list) or len(raw)<6:
                continue
            try:
                price=float(raw[2]);vol=float(raw[5] or 0.0)
                stamp=self._daily_ts(str(raw[0]))
            except Exception:
                continue
            if price>0 and math.isfinite(price):
                parsed.append((stamp,price,max(0.0,vol)))
        if len(parsed)<min_points:
            raise TencentCNDataError(f"insufficient_daily:{symbol}:{len(parsed)}<{min_points}")
        return parsed

    def _five_minute(self,symbol:str,min_points:int,timeout:int):
        code=self._symbol(symbol)
        payload=self._get(
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
            {"param":f"{code},m5,,640"},
            timeout,
        )
        node=(payload.get("data") or {}).get(code) or {}
        rows=node.get("m5") or []
        parsed=[]
        for raw in rows:
            if not isinstance(raw,list) or len(raw)<6:
                continue
            try:
                price=float(raw[2]);vol=float(raw[5] or 0.0)
                stamp=self._minute_ts(str(raw[0]))
            except Exception:
                continue
            if price>0 and math.isfinite(price):
                parsed.append((stamp,price,max(0.0,vol)))
        if len(parsed)<min_points:
            raise TencentCNDataError(f"insufficient_5m:{symbol}:{len(parsed)}<{min_points}")
        return parsed

    def _one_minute(self,symbol:str,min_points:int,timeout:int):
        code=self._symbol(symbol)
        payload=self._get(
            "https://web.ifzq.gtimg.cn/appstock/app/minute/query",
            {"code":code},
            timeout,
        )
        node=(payload.get("data") or {}).get(code) or {}
        inner=node.get("data") or {}
        day=str(inner.get("date") or "")
        rows=inner.get("data") or []
        parsed=[]
        previous_cumulative=0.0
        for line in rows:
            parts=str(line).split()
            if len(parts)<3 or len(parts[0])<4:
                continue
            try:
                hhmm=parts[0][:4]
                price=float(parts[1])
                cumulative=float(parts[2] or 0.0)
                raw=f"{day}{hhmm}" if len(day)==8 else f"{day.replace('-','')}{hhmm}"
                stamp=self._minute_ts(raw)
                bar_volume=max(0.0,cumulative-previous_cumulative)
                previous_cumulative=max(previous_cumulative,cumulative)
            except Exception:
                continue
            if price>0 and math.isfinite(price):
                parsed.append((stamp,price,bar_volume))
        if len(parsed)<min_points:
            raise TencentCNDataError(f"insufficient_1m:{symbol}:{len(parsed)}<{min_points}")
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
        if not symbol.upper().endswith((".SS",".SH",".SZ")):
            raise TencentCNDataError(f"unsupported_symbol:{symbol}")
        if interval=="1d":
            rows=self._daily(symbol,min_points,timeout)
        elif interval=="5m":
            rows=self._five_minute(symbol,min_points,timeout)
        elif interval=="1m":
            rows=self._one_minute(symbol,min_points,timeout)
        else:
            raise TencentCNDataError(f"unsupported_interval:{interval}")

        from .market_data import ProviderSeries
        return ProviderSeries(
            symbol=symbol,
            ts=[r[0] for r in rows],
            close=[r[1] for r in rows],
            volume=[r[2] for r in rows],
        )
