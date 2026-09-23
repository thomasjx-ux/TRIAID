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
    """Tencent public equity bars for mainland China and Hong Kong.

    The class name is kept for backward compatibility with the existing engine.
    Production routing now uses it as the primary CN/HK provider.
    """

    name="tencent-cn-hk-market-data"
    version="tencent-cn-hk-market-data@0.4.0"

    @property
    def configured(self)->bool:
        return True

    @staticmethod
    def _is_hk(symbol:str)->bool:
        return symbol.upper().endswith(".HK")

    def _symbol(self,symbol:str)->str:
        upper=symbol.upper()
        code=upper.split(".")[0]
        if self._is_hk(upper):
            return f"hk{code.zfill(5)}"
        if upper.endswith((".SS",".SH")) or code.startswith(("5","6","9")):
            return f"sh{code}"
        if upper.endswith(".SZ"):
            return f"sz{code}"
        raise TencentCNDataError(f"unsupported_symbol:{symbol}")

    def _tz(self,symbol:str)->ZoneInfo:
        return ZoneInfo("Asia/Hong_Kong" if self._is_hk(symbol) else "Asia/Shanghai")

    def _get(self,url:str,params:dict,timeout:int)->dict:
        query=urllib.parse.urlencode(params)
        req=urllib.request.Request(
            url+"?"+query,
            headers={
                "User-Agent":"Mozilla/5.0 TRIAID-FIN-V2-TENCENT/0.4",
                "Referer":"https://gu.qq.com/",
                "Accept":"application/json,text/plain,*/*",
            },
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                raw=response.read().decode("utf-8","replace").strip()
            # Tencent occasionally wraps JSON in a JS assignment.
            if raw and not raw.startswith(("{","[")) and "=" in raw:
                raw=raw.split("=",1)[1].strip().rstrip(";")
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
    def _minute_ts(raw:str,tz:ZoneInfo)->int:
        text=raw.strip()
        if len(text)>=12 and text[:12].isdigit():
            value=f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"
        else:
            value=text[:16]
        dt=datetime.strptime(value,"%Y-%m-%d %H:%M").replace(tzinfo=tz)
        return int(dt.timestamp())

    def _daily(self,symbol:str,min_points:int,timeout:int,count:int=1200):
        code=self._symbol(symbol)
        payload=self._get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            {"param":f"{code},day,,,{max(int(count),int(min_points)+20)},qfq"},
            timeout,
        )
        node=(payload.get("data") or {}).get(code) or {}
        qfq_rows=node.get("qfqday") or []
        raw_rows=node.get("day") or []
        rows=qfq_rows or raw_rows

        # Tencent index series may not expose qfqday through the fqkline
        # endpoint. Fall back to the unadjusted kline endpoint rather than
        # treating a valid index as missing.
        if not rows:
            payload=self._get(
                "https://web.ifzq.gtimg.cn/appstock/app/kline/kline",
                {"param":f"{code},day,,,{max(int(count),int(min_points)+20)}"},
                timeout,
            )
            node=(payload.get("data") or {}).get(code) or {}
            qfq_rows=[]
            raw_rows=node.get("day") or []
            rows=raw_rows

        raw_close_by_day={
            str(x[0]):float(x[2])
            for x in raw_rows
            if isinstance(x,list) and len(x)>=3 and str(x[2]).strip()
        }
        parsed=[]
        for raw in rows:
            if not isinstance(raw,list) or len(raw)<6:
                continue
            try:
                price=float(raw[2]);vol=float(raw[5] or 0.0)
                raw_price=float(raw_close_by_day.get(str(raw[0]),price))
                stamp=self._daily_ts(str(raw[0]))
            except Exception:
                continue
            if price>0 and math.isfinite(price):
                if qfq_rows and raw_price>0 and math.isfinite(raw_price):
                    vol*=raw_price/price
                parsed.append((stamp,price,max(0.0,vol)))
        parsed.sort(key=lambda x:x[0])
        if len(parsed)<min_points:
            raise TencentCNDataError(f"insufficient_daily:{symbol}:{len(parsed)}<{min_points}")
        return parsed

    def fetch_full_daily(self,symbol:str,min_points:int=300,timeout:int=25,count:int=10000)->dict:
        rows=self._daily(symbol,min_points,timeout,count=count)
        return {
            "symbol":symbol,
            "provider":self.version,
            "ts":[r[0] for r in rows],
            "close":[r[1] for r in rows],
            "volume":[r[2] for r in rows],
        }

    @staticmethod
    def _aggregate_one_minute_to_five(
        rows:list[tuple[int,float,float]],
        tz:ZoneInfo,
    )->list[tuple[int,float,float]]:
        """Build strict completed 5-minute bars from 1-minute observations.

        Buckets are labelled by the conventional 5-minute end clock
        (09:31..09:35 -> 09:35, ..., 11:56..12:00 -> 12:00).
        A bucket is emitted only when it contains five distinct source minutes.
        The isolated 09:30 opening print therefore never becomes a fake 5m bar.
        """
        buckets={}
        for stamp,price,volume in rows:
            dt=datetime.fromtimestamp(int(stamp),tz)
            minute_of_day=dt.hour*60+dt.minute
            end_minute=((minute_of_day+4)//5)*5
            end_day=dt.date()
            if end_minute>=24*60:
                end_minute-=24*60
                from datetime import timedelta
                end_day=end_day+timedelta(days=1)
            end_dt=datetime(
                end_day.year,end_day.month,end_day.day,
                end_minute//60,end_minute%60,
                tzinfo=tz,
            )
            key=int(end_dt.timestamp())
            bucket=buckets.setdefault(key,{})
            bucket[int(stamp)]=(float(price),max(0.0,float(volume)))

        out=[]
        for end_ts,points in sorted(buckets.items()):
            if len(points)!=5:
                continue
            ordered=sorted(points.items())
            close=float(ordered[-1][1][0])
            volume=sum(float(item[1][1]) for item in ordered)
            if close>0 and math.isfinite(close):
                out.append((int(end_ts),close,max(0.0,volume)))
        return out

    def _five_minute(self,symbol:str,min_points:int,timeout:int):
        code=self._symbol(symbol)
        payload=self._get(
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
            {"param":f"{code},m5,,640"},
            timeout,
        )
        node=(payload.get("data") or {}).get(code) or {}
        rows=node.get("m5") or []
        tz=self._tz(symbol)
        parsed=[]
        future_cutoff=int(datetime.now(tz).timestamp())+90
        for raw in rows:
            if not isinstance(raw,list) or len(raw)<6:
                continue
            try:
                price=float(raw[2]);vol=float(raw[5] or 0.0)
                stamp=self._minute_ts(str(raw[0]),tz)
                if stamp>future_cutoff:
                    continue
            except Exception:
                continue
            if price>0 and math.isfinite(price):
                parsed.append((stamp,price,max(0.0,vol)))

        # HK native m5 can lag the minute feed. Preserve native historical m5,
        # but replace today's completed bars with strict aggregation from the
        # same Tencent 1m source used by REALTIME. This keeps provider/time
        # semantics consistent and avoids a cross-provider Yahoo fallback.
        if self._is_hk(symbol):
            one_minute=self._one_minute(symbol,1,timeout)
            derived=self._aggregate_one_minute_to_five(one_minute,tz)
            if derived:
                today=datetime.now(tz).date()
                parsed=[
                    row for row in parsed
                    if datetime.fromtimestamp(int(row[0]),tz).date()!=today
                ]
                parsed.extend(
                    row for row in derived
                    if datetime.fromtimestamp(int(row[0]),tz).date()==today
                )

        # Deduplicate timestamps deterministically with the most recently
        # assembled row winning (derived HK bars override native current-day m5).
        by_ts={int(row[0]):row for row in parsed}
        parsed=[by_ts[key] for key in sorted(by_ts)]
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
        tz=self._tz(symbol)
        parsed=[]
        previous_cumulative=0.0
        # Tencent can publish a next-session placeholder exactly at the lunch
        # boundary (observed for HK as 13:00 while local time was 12:00).
        # Current bars are commonly labelled by their ending minute, so allow a
        # small forward tolerance but reject clearly future session placeholders.
        future_cutoff=int(datetime.now(tz).timestamp())+90
        for line in rows:
            parts=str(line).split()
            if len(parts)<3 or len(parts[0])<4:
                continue
            try:
                hhmm=parts[0][:4]
                price=float(parts[1])
                cumulative=float(parts[2] or 0.0)
                raw=f"{day}{hhmm}" if len(day)==8 else f"{day.replace('-','')}{hhmm}"
                stamp=self._minute_ts(raw,tz)
                if stamp>future_cutoff:
                    continue
                bar_volume=max(0.0,cumulative-previous_cumulative)
                previous_cumulative=max(previous_cumulative,cumulative)
            except Exception:
                continue
            if price>0 and math.isfinite(price):
                parsed.append((stamp,price,bar_volume))
        parsed.sort(key=lambda x:x[0])
        if len(parsed)<min_points:
            raise TencentCNDataError(f"insufficient_1m:{symbol}:{len(parsed)}<{min_points}")
        return parsed

    def auction_shadow_probe(self,symbol:str,timeout:int=10)->dict:
        if self._is_hk(symbol):
            return {
                "provider":self.version,
                "symbol":symbol,
                "available":False,
                "role":"SHADOW_ZERO_COST_VALIDATION_ONLY",
                "reason":"CN_ONLY",
            }
        rows=self._one_minute(symbol,1,timeout)
        local=ZoneInfo("Asia/Shanghai")
        matches=[]
        for stamp,price,volume in rows:
            dt=datetime.fromtimestamp(int(stamp),local)
            if dt.hour==9 and dt.minute==25:
                matches.append({"ts":int(stamp),"price":float(price),"volume":float(volume)})
        return {
            "provider":self.version,
            "symbol":symbol,
            "available":bool(matches),
            "auction_time":"09:25",
            "rows":matches[-1:],
            "role":"SHADOW_ZERO_COST_VALIDATION_ONLY",
        }

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
        if not upper.endswith((".SS",".SH",".SZ",".HK")):
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
