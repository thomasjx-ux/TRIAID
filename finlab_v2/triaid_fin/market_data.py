from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time
from threading import RLock
from zoneinfo import ZoneInfo

from .alpaca_data import AlpacaMarketDataProvider
from .eastmoney_data import EastmoneyMarketDataProvider
from .sina_us_data import SinaUSMarketDataProvider
from .tencent_cn_data import TencentCNMarketDataProvider
from .provider_registry import ProviderRegistry
from .trading_calendar import official_session_phase


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModeConfig:
    mode: str
    range_: str
    interval: str
    include_prepost: bool
    min_points: int
    cache_ttl_seconds: int
    quality: str
    execution_grade: bool


MODE_CONFIGS={
    "DAILY":ModeConfig("DAILY","10y","1d",False,300,900,"research_grade",False),
    "INTRADAY":ModeConfig("INTRADAY","5d","5m",False,30,90,"research_intraday",False),
    "PREOPEN":ModeConfig("PREOPEN","1d","5m",True,2,60,"indicative_extended_hours",False),
    "REALTIME":ModeConfig("REALTIME","1d","1m",True,2,20,"indicative_not_execution_grade",False),
}


@dataclass
class ProviderSeries:
    symbol:str
    ts:list[int]
    close:list[float]
    volume:list[float]


@dataclass
class ProviderPanel:
    market_id:str
    mode:str
    provider:str
    ts:list[int]
    close:dict[str,list[float]]
    volume:dict[str,list[float]]
    fetched_at:float
    source_latest_ts:int
    interval:str
    include_prepost:bool
    quality:str
    execution_grade:bool

    def metadata(self)->dict:
        return {
            "market_id":self.market_id,
            "mode":self.mode,
            "provider":self.provider,
            "fetched_at":self.fetched_at,
            "source_latest_ts":self.source_latest_ts,
            "points":len(self.ts),
            "symbols":sorted(self.close),
            "interval":self.interval,
            "include_prepost":self.include_prepost,
            "quality":self.quality,
            "execution_grade":self.execution_grade,
        }


class YahooChartProvider:
    name="yahoo-chart"
    version="yahoo-chart@0.2.0"

    @staticmethod
    def _finite(x)->bool:
        return isinstance(x,(int,float)) and math.isfinite(x)

    def fetch_series(
        self,
        symbol:str,
        *,
        range_:str,
        interval:str,
        include_prepost:bool,
        min_points:int,
        timeout:int=15,
    )->ProviderSeries:
        query=urllib.parse.urlencode({
            "range":range_,
            "interval":interval,
            "includeAdjustedClose":"true",
            "includePrePost":"true" if include_prepost else "false",
        })
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}"
        req=urllib.request.Request(
            url,
            headers={"User-Agent":"Mozilla/5.0 TRIAID-FIN-V2-MARKET-DATA/0.7"},
        )
        try:
            with urllib.request.urlopen(req,timeout=timeout) as response:
                payload=json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise MarketDataError(f"fetch_failed:{symbol}:{interval}:{type(exc).__name__}:{exc}") from exc

        chart=payload.get("chart") or {}
        if chart.get("error"):
            raise MarketDataError(f"provider_error:{symbol}:{chart['error']}")
        result=(chart.get("result") or [None])[0]
        if not result:
            raise MarketDataError(f"empty_result:{symbol}:{interval}")

        ts=result.get("timestamp") or []
        indicators=result.get("indicators") or {}
        quote=(indicators.get("quote") or [{}])[0]
        adj=(indicators.get("adjclose") or [{}])[0].get("adjclose")
        close=adj if adj and len(adj)==len(ts) else (quote.get("close") or [])
        volume=quote.get("volume") or []

        rows=[]
        for i,(stamp,price) in enumerate(zip(ts,close)):
            if price is None or not self._finite(price) or price<=0:
                continue
            vol=volume[i] if i<len(volume) else 0.0
            vol=float(vol) if vol is not None and self._finite(vol) and vol>=0 else 0.0
            rows.append((int(stamp),float(price),vol))

        if len(rows)<min_points:
            raise MarketDataError(
                f"insufficient_points:{symbol}:{interval}:{len(rows)}<{min_points}"
            )
        return ProviderSeries(
            symbol=symbol,
            ts=[r[0] for r in rows],
            close=[r[1] for r in rows],
            volume=[r[2] for r in rows],
        )


class MarketDataHub:
    version="market-data-hub@0.4.0"

    def __init__(self,provider:YahooChartProvider|None=None)->None:
        self.provider=provider or YahooChartProvider()
        self.alpaca=AlpacaMarketDataProvider()
        self.eastmoney=EastmoneyMarketDataProvider()
        self.sina_us=SinaUSMarketDataProvider()
        self.tencent_cn=TencentCNMarketDataProvider()
        self.registry=ProviderRegistry()
        self.registry.register(
            "research_bars",
            self.provider,
            routes=(
                "US:DAILY","US:INTRADAY","US:PREOPEN","US:REALTIME",
                "CN:DAILY","CN:INTRADAY","CN:REALTIME",
            ),
        )
        self.registry.register(
            "us_l1_quotes",
            self.alpaca,
            routes=("US:QUOTE_L1",),
        )
        self.registry.register("sina_us_backup",self.sina_us)
        self.registry.register("tencent_cn_backup",self.tencent_cn)
        # Eastmoney adapter remains available for research, but its public hosts
        # are not in the automatic failover chain because Railway smoke observed
        # remote disconnects from the current egress.
        self.registry.register("eastmoney_experimental",self.eastmoney)
        for route in ("US:DAILY","US:INTRADAY","US:REALTIME"):
            self.registry.add_fallback(route,"sina_us_backup")
        for route in ("CN:DAILY","CN:INTRADAY","CN:REALTIME"):
            self.registry.add_fallback(route,"tencent_cn_backup")
        self._cache:dict[tuple[str,str],ProviderPanel]={}
        self._errors:dict[tuple[str,str],dict]={}
        self._failovers:list[dict]=[]
        self._lock=RLock()

    def provider_status(self)->dict:
        registry=self.registry.status()
        routing={}
        chains={}
        for route,provider_names in registry.get("chains",{}).items():
            chain=[]
            for provider_name in provider_names:
                provider=self.registry.provider(provider_name)
                configured=bool(getattr(provider,"configured",True)) if provider else False
                chain.append({
                    "name":provider_name,
                    "version":getattr(provider,"version",None),
                    "configured":configured,
                })
            chains[route]=chain
            routing[route]=next(
                (x["version"] for x in chain if x["configured"]),
                None,
            )
        routing.setdefault("CN:PREOPEN_AUCTION",None)
        routing.setdefault("CN:QUOTE_L1",None)
        return {
            "registry":registry,
            "bar_provider":{
                "provider":getattr(self.registry.provider("research_bars"),"version",None),
                "configured":True,
                "role":"default research bars",
            },
            "backup_bar_providers":{
                "US":{
                    "provider":self.sina_us.version,
                    "configured":True,
                    "role":"automatic regular-session US fallback",
                },
                "CN":{
                    "provider":self.tencent_cn.version,
                    "configured":True,
                    "role":"automatic A-share fallback",
                },
                "experimental":{
                    "provider":self.eastmoney.version,
                    "configured":True,
                    "role":"not routed automatically after Railway connectivity smoke failure",
                },
            },
            "us_l1_quote_provider":self.alpaca.configuration_status(),
            "routing":routing,
            "chains":chains,
            "recent_failovers":list(self._failovers[-50:]),
        }

    def product_capabilities(self,market_id:str|None=None)->dict:
        markets=[market_id.upper()] if market_id else ["US","CN"]
        out={}
        for market in markets:
            if market=="US":
                out[market]={
                    "BAR_DAILY":{"available":True,"provider":self.provider.version,"grade":"research"},
                    "BAR_INTRADAY":{"available":True,"provider":self.provider.version,"grade":"research"},
                    "QUOTE_L1":{
                        "available":self.alpaca.configured,
                        "provider":self.alpaca.version if self.alpaca.configured else None,
                        "grade":"provider_entitlement_dependent",
                        "note":"Best bid/ask requires Alpaca credentials. No production trading decision is enabled by this capability alone.",
                    },
                    "ORDERBOOK_L2":{
                        "available":False,"provider":None,"grade":"unavailable",
                        "note":"No L2 order-book provider connected.",
                    },
                    "PREOPEN_EXTENDED":{
                        "available":True,"provider":self.provider.version,"grade":"indicative",
                    },
                    "PREOPEN_AUCTION":{
                        "available":False,"provider":None,"grade":"not_applicable",
                    },
                    "SECTOR_BARS":{
                        "available":False,"provider":None,"grade":"interface_reserved",
                        "note":"Sector/industry universe provider not connected yet.",
                    },
                    "STOCK_BARS":{
                        "available":True,"provider":self.provider.version,"grade":"research_on_demand",
                        "note":"Arbitrary Yahoo-supported US symbols can be requested for research bars; not yet part of the active strategy universe.",
                    },
                    "DERIVATIVES_CHAIN":{
                        "available":False,"provider":None,"grade":"interface_reserved",
                        "note":"Options/futures chain provider not connected yet.",
                    },
                    "BROKER_FILLS":{
                        "available":False,"provider":None,"grade":"unavailable",
                        "note":"No broker execution/fill connector is attached.",
                    },
                }
            elif market=="CN":
                out[market]={
                    "BAR_DAILY":{"available":True,"provider":self.provider.version,"grade":"research"},
                    "BAR_INTRADAY":{"available":True,"provider":self.provider.version,"grade":"research"},
                    "QUOTE_L1":{"available":False,"provider":None,"grade":"unavailable"},
                    "ORDERBOOK_L2":{"available":False,"provider":None,"grade":"unavailable"},
                    "PREOPEN_EXTENDED":{"available":False,"provider":None,"grade":"not_applicable"},
                    "PREOPEN_AUCTION":{
                        "available":False,"provider":None,"grade":"unavailable",
                        "note":"Dedicated A-share call-auction feed required.",
                    },
                    "SECTOR_BARS":{
                        "available":False,"provider":None,"grade":"interface_reserved",
                        "note":"A-share sector/industry provider not connected yet.",
                    },
                    "STOCK_BARS":{
                        "available":True,"provider":self.provider.version,"grade":"research_on_demand",
                        "note":"Yahoo-supported A-share symbols can be requested for research bars; not execution-grade.",
                    },
                    "DERIVATIVES_CHAIN":{
                        "available":False,"provider":None,"grade":"interface_reserved",
                        "note":"China futures/options chain provider not connected yet.",
                    },
                    "BROKER_FILLS":{
                        "available":False,"provider":None,"grade":"unavailable",
                    },
                }
        return out

    def latest_quotes(self,market_id:str,symbols:list[str]|tuple[str,...])->dict:
        market=market_id.upper()
        if market!="US":
            return {
                "available":False,
                "market_id":market,
                "product":"QUOTE_L1",
                "provider":None,
                "symbols":{},
                "reason":"NO_AUTHORIZED_L1_PROVIDER",
            }
        provider=self.registry.provider_for("US:QUOTE_L1")
        if provider is None or not bool(getattr(provider,"configured",False)):
            return {
                "available":False,
                "market_id":"US",
                "product":"QUOTE_L1",
                "provider":None,
                "symbols":{},
                "reason":"ALPACA_CREDENTIALS_NOT_CONFIGURED",
            }
        result=provider.latest_quotes(symbols)
        return {
            "available":True,
            "market_id":"US",
            "product":"QUOTE_L1",
            **result,
        }

    def instrument_series(self,market_id:str,symbol:str,mode:str="DAILY")->dict:
        market=market_id.upper();mode=mode.upper()
        if market not in {"US","CN"}:
            raise MarketDataError(f"unsupported_market:{market}")
        if mode not in MODE_CONFIGS:
            raise MarketDataError(f"unsupported_mode:{mode}")
        cfg=MODE_CONFIGS[mode]
        if market=="CN" and mode=="PREOPEN":
            raise MarketDataError("unsupported_market_mode:CN:PREOPEN")
        providers=self.registry.providers_for(f"{market}:{mode}")
        if not providers:
            raise MarketDataError(f"provider_route_missing:{market}:{mode}")
        errors=[]
        selected=None
        s=None
        for provider in providers:
            try:
                s=provider.fetch_series(
                    symbol,
                    range_=cfg.range_,
                    interval=cfg.interval,
                    include_prepost=cfg.include_prepost,
                    min_points=cfg.min_points if mode=="DAILY" else 2,
                )
                selected=provider
                if errors:
                    self._record_failover(market,mode,errors,provider.version,[symbol])
                break
            except Exception as exc:
                errors.append(f"{provider.version}:{type(exc).__name__}:{exc}")
        if s is None or selected is None:
            raise MarketDataError(f"all_providers_failed:{market}:{mode}:{' | '.join(errors)}")
        return {
            "market_id":market,
            "symbol":symbol,
            "mode":mode,
            "provider":selected.version,
            "quality":cfg.quality,
            "execution_grade":False,
            "points":len(s.ts),
            "source_latest_ts":s.ts[-1],
            "latest":{"close":s.close[-1],"volume":s.volume[-1]},
        }

    def _record_failover(
        self,
        market:str,
        mode:str,
        errors:list[str],
        provider_version:str,
        symbols:list[str]|tuple[str,...],
    )->None:
        event={
            "at":time.time(),
            "market_id":market,
            "mode":mode,
            "selected_provider":provider_version,
            "failed_attempts":list(errors),
            "symbols":list(symbols),
        }
        with self._lock:
            self._failovers.append(event)
            if len(self._failovers)>200:
                del self._failovers[:-200]

    def _panel_from_provider(
        self,
        provider,
        market:str,
        mode:str,
        symbols:list[str]|tuple[str,...],
        benchmark:str,
        cfg:ModeConfig,
        now:float,
    )->ProviderPanel:
        series=[]
        fetch_errors=[]
        for symbol in symbols:
            try:
                series.append(
                    provider.fetch_series(
                        symbol,
                        range_=cfg.range_,
                        interval=cfg.interval,
                        include_prepost=cfg.include_prepost,
                        min_points=cfg.min_points,
                    )
                )
            except Exception as exc:
                fetch_errors.append(f"{symbol}:{type(exc).__name__}:{exc}")
        if fetch_errors:
            raise MarketDataError(
                f"provider_incomplete:{provider.version}:{' | '.join(fetch_errors)}"
            )

        by={s.symbol:s for s in series}
        if benchmark not in by:
            raise MarketDataError(f"benchmark_missing:{provider.version}:{market}:{mode}")

        common=set(by[benchmark].ts)
        for s in series:
            common &= set(s.ts)
        ts=[t for t in by[benchmark].ts if t in common]
        aligned_min=max(2,min(cfg.min_points,30 if mode!="DAILY" else cfg.min_points))
        if len(ts)<aligned_min:
            raise MarketDataError(
                f"insufficient_aligned_points:{provider.version}:{market}:{mode}:{len(ts)}<{aligned_min}"
            )

        close={}
        volume={}
        for symbol,s in by.items():
            cm={t:c for t,c in zip(s.ts,s.close)}
            vm={t:v for t,v in zip(s.ts,s.volume)}
            close[symbol]=[cm[t] for t in ts]
            volume[symbol]=[vm.get(t,0.0) for t in ts]

        return ProviderPanel(
            market_id=market,
            mode=mode,
            provider=provider.version,
            ts=ts,
            close=close,
            volume=volume,
            fetched_at=now,
            source_latest_ts=ts[-1],
            interval=cfg.interval,
            include_prepost=cfg.include_prepost,
            quality=cfg.quality,
            execution_grade=cfg.execution_grade,
        )

    def capabilities(self,market_id:str|None=None)->dict:
        markets=[market_id.upper()] if market_id else ["US","CN"]
        result={}
        for market in markets:
            modes={}
            for name,cfg in MODE_CONFIGS.items():
                supported=True
                note=""
                quality=cfg.quality
                if market=="CN" and name=="PREOPEN":
                    supported=False
                    note="Yahoo Chart does not provide a reliable A-share call-auction feed; keep this mode disabled until a dedicated provider is connected."
                if name=="REALTIME":
                    note=(note+" " if note else "")+"Indicative chart data only; no bid/ask, order book, exchange entitlement or broker execution guarantee."
                modes[name]={
                    **asdict(cfg),
                    "supported":supported,
                    "note":note,
                }
            result[market]=modes
        return result

    def refresh_panel(
        self,
        market_id:str,
        symbols:list[str]|tuple[str,...],
        benchmark:str,
        mode:str="DAILY",
        *,
        force:bool=False,
    )->ProviderPanel:
        market=market_id.upper()
        mode=mode.upper()
        if mode not in MODE_CONFIGS:
            raise MarketDataError(f"unsupported_mode:{mode}")
        cap=self.capabilities(market)[market][mode]
        if not cap["supported"]:
            raise MarketDataError(f"unsupported_market_mode:{market}:{mode}:{cap['note']}")
        cfg=MODE_CONFIGS[mode]
        key=(market,mode)
        now=time.time()

        with self._lock:
            cached=self._cache.get(key)
            if cached and not force and now-cached.fetched_at<=cfg.cache_ttl_seconds:
                return cached

        providers=self.registry.providers_for(f"{market}:{mode}")
        if not providers:
            raise MarketDataError(f"provider_route_missing:{market}:{mode}")

        attempt_errors=[]
        candidates=[]
        for provider_index,provider in enumerate(providers):
            if not bool(getattr(provider,"configured",True)):
                attempt_errors.append(f"{getattr(provider,'version',type(provider).__name__)}:not_configured")
                continue
            try:
                candidate=self._panel_from_provider(
                    provider,market,mode,symbols,benchmark,cfg,now
                )
                candidates.append((provider_index,candidate))
            except Exception as exc:
                attempt_errors.append(
                    f"{getattr(provider,'version',type(provider).__name__)}:{type(exc).__name__}:{exc}"
                )

        if not candidates:
            with self._lock:
                self._errors[key]={
                    "at":now,
                    "errors":list(attempt_errors),
                    "mode":mode,
                    "market_id":market,
                }
            raise MarketDataError(
                f"all_providers_failed:{market}:{mode}:{' | '.join(attempt_errors)}"
            )

        # For realtime research, provider success alone is not enough: a recovered
        # primary may still lag a fresher fallback. Choose the freshest successful
        # source and never allow the cached source timestamp to move backwards.
        # For slower modes, preserve route priority unless the primary regresses.
        if mode=="REALTIME":
            provider_index,panel=max(
                candidates,
                key=lambda item:(int(item[1].source_latest_ts),-int(item[0])),
            )
        else:
            provider_index,panel=candidates[0]

        if cached is not None and int(panel.source_latest_ts)<int(cached.source_latest_ts):
            fresher=[
                item for item in candidates
                if int(item[1].source_latest_ts)>=int(cached.source_latest_ts)
            ]
            if fresher:
                provider_index,panel=max(
                    fresher,
                    key=lambda item:(int(item[1].source_latest_ts),-int(item[0])),
                )
            else:
                with self._lock:
                    self._errors.pop(key,None)
                return cached

        selected_provider=providers[provider_index]
        if provider_index>0:
            primary_version=getattr(providers[0],"version",type(providers[0]).__name__)
            selected_version=getattr(selected_provider,"version",type(selected_provider).__name__)
            freshness_note=(
                f"{primary_version}:freshness_override:"
                f"selected={selected_version}:source_latest_ts={panel.source_latest_ts}"
            )
            self._record_failover(
                market,mode,[*attempt_errors,freshness_note],selected_version,symbols
            )
        elif attempt_errors:
            self._record_failover(
                market,mode,attempt_errors,
                getattr(selected_provider,"version",type(selected_provider).__name__),
                symbols,
            )

        with self._lock:
            self._cache[key]=panel
            self._errors.pop(key,None)
        return panel

    def cached_panel(self,market_id:str,mode:str)->ProviderPanel|None:
        with self._lock:
            return self._cache.get((market_id.upper(),mode.upper()))

    def status(self)->dict:
        with self._lock:
            cache={
                f"{market}:{mode}":{
                    **panel.metadata(),
                    "age_seconds":max(0.0,time.time()-panel.fetched_at),
                }
                for (market,mode),panel in self._cache.items()
            }
            errors={f"{m}:{mode}":dict(v) for (m,mode),v in self._errors.items()}
        return {
            "version":self.version,
            "provider":self.provider.version,
            "providers":self.provider_status(),
            "products":self.product_capabilities(),
            "provider_failover":{
                "enabled":True,
                "recent_events":list(self._failovers[-50:]),
            },
            "cache":cache,
            "errors":errors,
            "capabilities":self.capabilities(),
        }


def session_phase(market_id:str,now:datetime|None=None)->str:
    try:
        return official_session_phase(market_id,now)
    except ValueError as exc:
        raise MarketDataError(str(exc)) from exc


_DEFAULT_HUB=MarketDataHub()


def get_market_data_hub()->MarketDataHub:
    return _DEFAULT_HUB
