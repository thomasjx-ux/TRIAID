from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class SinaUSDataError(RuntimeError):
    pass


class SinaUSMarketDataProvider:
    name="sina-us-market-data"
    version="sina-us-market-data@0.1.0"

    @property
    def configured(self)->bool:
        return True

    def _get_payload(self,url:str,params:dict,timeout:int):
        query=urllib.parse.urlencode(params)
        req=urllib.request.Request(
            url+"?"+query,
            headers={
                "User-Agent":"Mozilla/5.0 TRIAID-FIN-V2-SINA-US/0.1",
                "Referer":"https://finance.sina.com.cn/",
                "Accept":"*/*",
            },
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                raw=response.read().decode("utf-8","replace").strip()
        except Exception as exc:
            raise SinaUSDataError(f"fetch_failed:{type(exc).__name__}:{exc}") from exc

        candidates=[]
        left=raw.find("[");right=raw.rfind("]")
        if left>=0 and right>left:
            candidates.append(raw[left:right+1])
        left=raw.find("{");right=raw.rfind("}")
        if left>=0 and right>left:
            candidates.append(raw[left:right+1])
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except Exception:
                continue
        raise SinaUSDataError(f"jsonp_parse_failed:{raw[:120]}")

    @staticmethod
    def _daily_ts(raw:str)->int:
        text=raw.strip()[:10]
        if len(text)==8 and text.isdigit():
            text=f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        d=datetime.strptime(text,"%Y-%m-%d")
        return int(d.replace(tzinfo=timezone.utc,hour=12).timestamp())

    @staticmethod
    def _minute_ts(raw:str)->int:
        text=raw.strip().replace("T"," ")
        formats=("%Y-%m-%d %H:%M","%Y-%m-%d %H:%M:%S","%Y%m%d%H%M")
        for fmt in formats:
            try:
                dt=datetime.strptime(text[:19] if "%S" in fmt else text[:16] if "-" in fmt else text[:12],fmt)
                return int(dt.replace(tzinfo=ZoneInfo("America/New_York")).timestamp())
            except Exception:
                continue
        raise SinaUSDataError(f"bad_minute_time:{raw}")

    @staticmethod
    def _value(row:dict,*names):
        for name in names:
            if name in row and row[name] not in (None,""):
                return row[name]
        return None

    def _normalize_rows(self,payload,*,daily:bool):
        if isinstance(payload,dict):
            for key in ("data","result","items","list"):
                value=payload.get(key)
                if isinstance(value,list):
                    payload=value
                    break
        if not isinstance(payload,list):
            raise SinaUSDataError("payload_not_list")

        parsed=[]
        for row in payload:
            raw_time=None;raw_close=None;raw_volume=0.0
            if isinstance(row,dict):
                raw_time=self._value(row,"d","date","day","time","t")
                raw_close=self._value(row,"c","close","price","p")
                raw_volume=self._value(row,"v","volume","vol") or 0.0
            elif isinstance(row,(list,tuple)):
                if len(row)>=6:
                    raw_time=row[0]
                    # Common K-line array order: time/open/close/high/low/volume.
                    raw_close=row[2]
                    raw_volume=row[5] or 0.0
                elif len(row)>=3:
                    raw_time=row[0]
                    raw_close=row[1]
                    raw_volume=row[2] or 0.0
            elif isinstance(row,str):
                parts=re.split(r"[,\s]+",row.strip())
                if len(parts)>=3:
                    raw_time=parts[0]
                    raw_close=parts[1]
                    raw_volume=parts[2] or 0.0
            if raw_time is None or raw_close is None:
                continue
            try:
                price=float(raw_close)
                volume=float(raw_volume or 0.0)
                stamp=self._daily_ts(str(raw_time)) if daily else self._minute_ts(str(raw_time))
            except Exception:
                continue
            if price>0 and math.isfinite(price):
                parsed.append((stamp,price,max(0.0,volume)))
        parsed.sort(key=lambda x:x[0])
        return parsed

    def _daily(self,symbol:str,min_points:int,timeout:int):
        payload=self._get_payload(
            "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var/US_MinKService.getDailyK",
            {"symbol":symbol.upper(),"num":10000},
            timeout,
        )
        rows=self._normalize_rows(payload,daily=True)
        if len(rows)<min_points:
            raise SinaUSDataError(f"insufficient_daily:{symbol}:{len(rows)}<{min_points}")
        return rows

    def _five_minute(self,symbol:str,min_points:int,timeout:int):
        payload=self._get_payload(
            "https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/var%20triaid=/US_MinKService.getMinK",
            {"symbol":symbol.upper(),"type":5,"___qn":3},
            timeout,
        )
        rows=self._normalize_rows(payload,daily=False)
        if len(rows)<min_points:
            raise SinaUSDataError(f"insufficient_5m:{symbol}:{len(rows)}<{min_points}")
        return rows

    def _one_minute(self,symbol:str,min_points:int,timeout:int):
        payload=self._get_payload(
            "https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/var%20triaid=/US_MinlineNService.getMinline",
            {"symbol":symbol.upper(),"day":1,"random":int(time.time()*1000)},
            timeout,
        )
        rows=self._normalize_rows(payload,daily=False)
        if len(rows)<min_points:
            raise SinaUSDataError(f"insufficient_1m:{symbol}:{len(rows)}<{min_points}")
        return rows

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
        upper=symbol.upper()
        if upper.endswith((".SS",".SH",".SZ")):
            raise SinaUSDataError(f"unsupported_symbol:{symbol}")
        if include_prepost:
            raise SinaUSDataError("extended_hours_not_supported")
        if interval=="1d":
            rows=self._daily(upper,min_points,timeout)
        elif interval=="5m":
            rows=self._five_minute(upper,min_points,timeout)
        elif interval=="1m":
            rows=self._one_minute(upper,min_points,timeout)
        else:
            raise SinaUSDataError(f"unsupported_interval:{interval}")

        from .market_data import ProviderSeries
        return ProviderSeries(
            symbol=symbol,
            ts=[r[0] for r in rows],
            close=[r[1] for r in rows],
            volume=[r[2] for r in rows],
        )
