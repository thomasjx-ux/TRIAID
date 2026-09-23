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
from .tushare_auction import TushareETFAuctionProvider
from .provider_registry import ProviderRegistry
from .market_registry import MARKET_REGISTRY, market_ids, normalize_market_id
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


HK_HIGH_FREQUENCY_OPTIONAL_SYMBOLS={"2819.HK"}

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
    requested_symbols:list[str]|None=None
    degraded_symbols:list[str]|None=None

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
            "requested_symbols":list(self.requested_symbols or sorted(self.close)),
            "degraded_symbols":list(self.degraded_symbols or []),
            "partial_symbol_policy":(
                "HK_HIGH_FREQUENCY_SPARSE_DEFENSIVE_NO_INTERPOLATION"
                if self.market_id=="HK" and self.mode in {"INTRADAY","REALTIME"}
                else "STRICT_COMPLETE_PANEL"
            ),
        }


class YahooChartProvider:
    name="yahoo-chart"
    version="yahoo-chart@0.3.0"

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
        raw_close=quote.get("close") or []
        use_adjusted=bool(adj and len(adj)==len(ts))
        close=adj if use_adjusted else raw_close
        volume=quote.get("volume") or []

        rows=[]
        for i,(stamp,price) in enumerate(zip(ts,close)):
            if price is None or not self._finite(price) or price<=0:
                continue
            vol=volume[i] if i<len(volume) else 0.0
            vol=float(vol) if vol is not None and self._finite(vol) and vol>=0 else 0.0
            # Strategy history uses adjusted prices. Rescale volume so
            # adjusted_price * adjusted_volume preserves raw traded notional.
            # This prevents split/dividend adjustment from corrupting ADV/capacity.
            if use_adjusted and i<len(raw_close):
                raw_px=raw_close[i]
                if raw_px is not None and self._finite(raw_px) and raw_px>0:
                    vol*=float(raw_px)/float(price)
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
    version="market-data-hub@0.7.0"

    def __init__(self,provider:YahooChartProvider|None=None)->None:
        self.provider=provider or YahooChartProvider()
        self.alpaca=AlpacaMarketDataProvider()
        self.eastmoney=EastmoneyMarketDataProvider()
        self.sina_us=SinaUSMarketDataProvider()
        self.tencent_cn=TencentCNMarketDataProvider()
        self.tushare_auction=TushareETFAuctionProvider()
        self.registry=ProviderRegistry()

        # Market-specific routing: do not make one public endpoint a
        # cross-market single point of failure.
        self.registry.register(
            "yahoo_bars",
            self.provider,
            routes=("US:PREOPEN","US:REALTIME"),
        )
        # Compatibility alias retained for tests and callers that explicitly
        # re-route a capability to the generic research bar provider.
        self.registry.register("research_bars",self.provider)
        self.registry.register(
            "sina_us_primary",
            self.sina_us,
            routes=("US:DAILY","US:INTRADAY"),
        )
        self.registry.register(
            "tencent_equity_primary",
            self.tencent_cn,
            routes=(
                "CN:DAILY","CN:INTRADAY","CN:REALTIME",
                "HK:DAILY","HK:INTRADAY","HK:REALTIME",
            ),
        )
        self.registry.register(
            "us_l1_quotes",
            self.alpaca,
            routes=("US:QUOTE_L1",),
        )
        self.registry.register("tushare_cn_auction",self.tushare_auction,routes=("CN:PREOPEN",))
        # Eastmoney remains research-only because Railway has observed remote
        # disconnects from the current egress.
        self.registry.register("eastmoney_experimental",self.eastmoney)

        # Yahoo is now a last-resort bar fallback for regular-session equity
        # data rather than the default provider for all three markets.
        for route in ("US:DAILY","US:INTRADAY"):
            self.registry.add_fallback(route,"yahoo_bars")
        for route in (
            "CN:DAILY","CN:INTRADAY","CN:REALTIME",
            "HK:DAILY","HK:INTRADAY","HK:REALTIME",
        ):
            self.registry.add_fallback(route,"yahoo_bars")
        self._cache:dict[tuple[str,str],ProviderPanel]={}
        self._errors:dict[tuple[str,str],dict]={}
        self._failovers:list[dict]=[]
        self._lock=RLock()

    def register_market_provider(
        self,
        market_id:str,
        provider_name:str,
        provider,
        *,
        modes:list[str]|tuple[str,...]=("DAILY",),
        fallbacks:list[str]|tuple[str,...]=(),
    )->dict:
        try:
            market=normalize_market_id(market_id)
        except KeyError as exc:
            raise MarketDataError(f"unsupported_market:{market_id}") from exc
        routes=tuple(f"{market}:{str(mode).upper()}" for mode in modes)
        self.registry.register(provider_name,provider,routes=routes)
        for fallback_name in fallbacks:
            for route in routes:
                self.registry.add_fallback(route,str(fallback_name))
        return {
            "market_id":market,
            "provider_name":provider_name,
            "routes":list(routes),
            "provider_version":getattr(provider,"version",type(provider).__name__),
        }

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
        routing["CN:PREOPEN_AUCTION"]=self.tushare_auction.version if self.tushare_auction.configured else None
        routing.setdefault("CN:QUOTE_L1",None)
        routing.setdefault("HK:QUOTE_L1",None)
        return {
            "registry":registry,
            "registered_markets":list(market_ids()),
            "bar_provider":{
                "provider":"market_specific",
                "configured":True,
                "role":"US regular=Sina; CN/HK=Tencent; US preopen/realtime=Yahoo",
            },
            "primary_bar_providers":{
                "US_REGULAR":self.sina_us.version,
                "US_PREOPEN_REALTIME":self.provider.version,
                "CN":self.tencent_cn.version,
                "HK":self.tencent_cn.version,
            },
            "backup_bar_providers":{
                "US":{
                    "provider":self.provider.version,
                    "configured":True,
                    "role":"Yahoo fallback for US daily/intraday",
                },
                "CN_HK":{
                    "provider":self.provider.version,
                    "configured":True,
                    "role":"Yahoo last-resort fallback after Tencent",
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
        markets=[normalize_market_id(market_id)] if market_id else list(market_ids())
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
                        "note":"US regular-session research bars use Sina first with Yahoo fallback; not execution-grade.",
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
                    "BAR_DAILY":{"available":True,"provider":self.tencent_cn.version,"grade":"research"},
                    "BAR_INTRADAY":{"available":True,"provider":self.tencent_cn.version,"grade":"research"},
                    "QUOTE_L1":{"available":False,"provider":None,"grade":"unavailable"},
                    "ORDERBOOK_L2":{"available":False,"provider":None,"grade":"unavailable"},
                    "PREOPEN_EXTENDED":{"available":False,"provider":None,"grade":"not_applicable"},
                    "PREOPEN_AUCTION":{
                        "available":self.tushare_auction.configured,
                        "provider":self.tushare_auction.version if self.tushare_auction.configured else None,
                        "grade":"research_auction_final" if self.tushare_auction.configured else "credentials_required",
                        "note":"Tushare etf_auction final opening-auction snapshot after 09:25; requires TUSHARE_TOKEN and etf_auction entitlement.",
                    },
                    "SECTOR_BARS":{
                        "available":False,"provider":None,"grade":"interface_reserved",
                        "note":"A-share sector/industry provider not connected yet.",
                    },
                    "STOCK_BARS":{
                        "available":True,"provider":self.provider.version,"grade":"research_on_demand",
                        "note":"A-share research bars use Tencent first with Yahoo fallback; not execution-grade.",
                    },
                    "DERIVATIVES_CHAIN":{
                        "available":False,"provider":None,"grade":"interface_reserved",
                        "note":"China futures/options chain provider not connected yet.",
                    },
                    "BROKER_FILLS":{
                        "available":False,"provider":None,"grade":"unavailable",
                    },
                }
            elif market=="HK":
                out[market]={
                    "BAR_DAILY":{"available":True,"provider":self.tencent_cn.version,"grade":"research"},
                    "BAR_INTRADAY":{"available":True,"provider":self.tencent_cn.version,"grade":"research"},
                    "QUOTE_L1":{"available":False,"provider":None,"grade":"unavailable"},
                    "ORDERBOOK_L2":{"available":False,"provider":None,"grade":"unavailable"},
                    "PREOPEN_EXTENDED":{"available":False,"provider":None,"grade":"not_connected"},
                    "PREOPEN_AUCTION":{"available":False,"provider":None,"grade":"interface_reserved"},
                    "SECTOR_BARS":{"available":False,"provider":None,"grade":"interface_reserved"},
                    "STOCK_BARS":{
                        "available":True,"provider":self.provider.version,"grade":"research_on_demand",
                        "note":"Hong Kong research bars use Tencent first with Yahoo fallback; not execution-grade.",
                    },
                    "DERIVATIVES_CHAIN":{"available":False,"provider":None,"grade":"interface_reserved"},
                    "BROKER_FILLS":{"available":False,"provider":None,"grade":"unavailable"},
                }
            if market not in out:
                def _route_product(mode:str)->dict:
                    providers=self.registry.providers_for(f"{market}:{mode}")
                    provider=providers[0] if providers else None
                    return {
                        "available":bool(provider and getattr(provider,"configured",True)),
                        "provider":getattr(provider,"version",None) if provider else None,
                        "grade":"research" if provider else "not_connected",
                    }
                out[market]={
                    "BAR_DAILY":_route_product("DAILY"),
                    "BAR_INTRADAY":_route_product("INTRADAY"),
                    "QUOTE_L1":_route_product("QUOTE_L1"),
                    "ORDERBOOK_L2":{"available":False,"provider":None,"grade":"interface_reserved"},
                    "PREOPEN_EXTENDED":_route_product("PREOPEN"),
                    "PREOPEN_AUCTION":{"available":False,"provider":None,"grade":"interface_reserved"},
                    "SECTOR_BARS":{"available":False,"provider":None,"grade":"interface_reserved"},
                    "STOCK_BARS":{"available":False,"provider":None,"grade":"interface_reserved"},
                    "DERIVATIVES_CHAIN":{"available":False,"provider":None,"grade":"interface_reserved"},
                    "BROKER_FILLS":{"available":False,"provider":None,"grade":"unavailable"},
                }
        return out

    def auction_shadow_probe(self,market_id:str,symbols:list[str]|tuple[str,...])->dict:
        market=normalize_market_id(market_id)
        if market!="CN":
            return {
                "market_id":market,
                "available":False,
                "role":"SHADOW_ZERO_COST_VALIDATION_ONLY",
                "reason":"CN_ONLY",
                "symbols":{},
            }
        rows={}
        available=0
        for symbol in symbols:
            try:
                result=self.tencent_cn.auction_shadow_probe(symbol)
            except Exception as exc:
                result={
                    "provider":self.tencent_cn.version,
                    "symbol":symbol,
                    "available":False,
                    "role":"SHADOW_ZERO_COST_VALIDATION_ONLY",
                    "error":f"{type(exc).__name__}:{exc}",
                }
            rows[symbol]=result
            available+=1 if result.get("available") else 0
        return {
            "market_id":"CN",
            "provider":self.tencent_cn.version,
            "role":"SHADOW_ZERO_COST_VALIDATION_ONLY",
            "available_symbols":available,
            "total_symbols":len(rows),
            "all_symbols_available":bool(rows) and available==len(rows),
            "symbols":rows,
            "promotion_rule":"ZERO_COST_SOURCE_MUST_PASS_REPEATED_PROSPECTIVE_0925_AVAILABILITY_AND_VALUE_CONSISTENCY_BEFORE_ROUTING",
        }

    def latest_quotes(self,market_id:str,symbols:list[str]|tuple[str,...])->dict:
        market=normalize_market_id(market_id)
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
        try:
            market=normalize_market_id(market_id)
        except KeyError as exc:
            raise MarketDataError(f"unsupported_market:{market_id}") from exc
        mode=mode.upper()
        if mode not in MODE_CONFIGS:
            raise MarketDataError(f"unsupported_mode:{mode}")
        cfg=MODE_CONFIGS[mode]
        if market=="CN" and mode=="PREOPEN" and not self.tushare_auction.configured:
            raise MarketDataError("unsupported_market_mode:CN:PREOPEN:Tushare ETF auction credentials/entitlement not configured")
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
        required_errors=[]
        degraded_symbols=[]
        optional_sparse=(
            set(HK_HIGH_FREQUENCY_OPTIONAL_SYMBOLS)
            if market=="HK" and mode in {"INTRADAY","REALTIME"}
            else set()
        )
        for symbol in symbols:
            try:
                series.append(
                    provider.fetch_series(
                        symbol,
                        range_=cfg.range_,
                        interval=cfg.interval,
                        include_prepost=cfg.include_prepost,
                        min_points=(2 if symbol in optional_sparse else cfg.min_points),
                    )
                )
            except Exception as exc:
                message=f"{symbol}:{type(exc).__name__}:{exc}"
                if symbol in optional_sparse:
                    degraded_symbols.append(message)
                else:
                    required_errors.append(message)
        if required_errors:
            raise MarketDataError(
                f"provider_incomplete:{provider.version}:{' | '.join(required_errors)}"
            )

        by={s.symbol:s for s in series}
        if benchmark not in by:
            raise MarketDataError(f"benchmark_missing:{provider.version}:{market}:{mode}")

        required_series=[
            s for s in series
            if s.symbol not in optional_sparse
        ]
        common=set(by[benchmark].ts)
        for s in required_series:
            common &= set(s.ts)
        ts=[t for t in by[benchmark].ts if t in common]
        aligned_min=(
            1 if market=="CN" and mode=="PREOPEN"
            else max(2,min(cfg.min_points,30 if mode!="DAILY" else cfg.min_points))
        )
        if len(ts)<aligned_min:
            raise MarketDataError(
                f"insufficient_aligned_points:{provider.version}:{market}:{mode}:{len(ts)}<{aligned_min}"
            )

        close={}
        volume={}
        for symbol,s in by.items():
            cm={t:c for t,c in zip(s.ts,s.close)}
            vm={t:v for t,v in zip(s.ts,s.volume)}
            missing=[t for t in ts if t not in cm]
            if missing:
                if symbol in optional_sparse:
                    degraded_symbols.append(
                        f"{symbol}:sparse_alignment:{len(ts)-len(missing)}/{len(ts)}"
                    )
                    continue
                raise MarketDataError(
                    f"required_symbol_alignment_gap:{provider.version}:{symbol}:{len(missing)}"
                )
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
            quality=(
                cfg.quality+"_partial_optional_symbol"
                if degraded_symbols
                else cfg.quality
            ),
            execution_grade=cfg.execution_grade,
            requested_symbols=list(symbols),
            degraded_symbols=degraded_symbols,
        )

    def capabilities(self,market_id:str|None=None)->dict:
        markets=[normalize_market_id(market_id)] if market_id else list(market_ids())
        result={}
        for market in markets:
            modes={}
            for name,cfg in MODE_CONFIGS.items():
                providers=self.registry.providers_for(f"{market}:{name}")
                supported=bool(
                    providers
                    and any(bool(getattr(provider,"configured",True)) for provider in providers)
                )
                note="" if supported else "No configured provider route is registered for this market/mode."
                quality=cfg.quality if supported else "not_connected"
                if market=="HK" and name=="PREOPEN":
                    supported=False
                    quality="not_connected"
                    note="HKEX pre-opening auction is modeled in the official session calendar, but a dedicated auction data feed is not connected."
                elif market=="CN" and name=="PREOPEN":
                    supported=self.tushare_auction.configured
                    quality="research_auction_final" if supported else "auction_credentials_required"
                    note=(
                        "Tushare etf_auction supplies the official opening-auction final snapshot after 09:25. "
                        "Dynamic 09:15-09:25 auction path remains shadow-only/not connected."
                        if supported else
                        "Tushare ETF auction provider is wired but TUSHARE_TOKEN/etf_auction entitlement is not configured."
                    )
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
        try:
            market=normalize_market_id(market_id)
        except KeyError as exc:
            raise MarketDataError(f"unsupported_market:{market_id}") from exc
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

        def freshness_key(p:ProviderPanel):
            if mode=="DAILY":
                tz=ZoneInfo(MARKET_REGISTRY.get(market).timezone)
                return datetime.fromtimestamp(int(p.source_latest_ts),tz).date().toordinal()
            return int(p.source_latest_ts)

        # Provider success alone is not enough. A recovered primary can lag a
        # fresher fallback. DAILY compares local trading dates so provider-specific
        # timestamp conventions on the same session do not create false freshness.
        best_freshness=max(freshness_key(item[1]) for item in candidates)
        freshest=[item for item in candidates if freshness_key(item[1])==best_freshness]
        provider_index,panel=min(freshest,key=lambda item:int(item[0]))

        if cached is not None and freshness_key(panel)<freshness_key(cached):
            fresher=[
                item for item in candidates
                if freshness_key(item[1])>=freshness_key(cached)
            ]
            if fresher:
                best=max(freshness_key(item[1]) for item in fresher)
                provider_index,panel=min(
                    [item for item in fresher if freshness_key(item[1])==best],
                    key=lambda item:int(item[0]),
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
