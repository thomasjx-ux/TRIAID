from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import urllib.parse
import urllib.request
import time
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any

from .store import RunStore


HORIZON_YEARS=(2,3,5,7,10,15,20,25,30)
TRADING_DAYS_PER_YEAR=252
CORE_EQUITY={
    "SP500":"^GSPC",
    "NASDAQ_COMPOSITE":"^IXIC",
    "RUSSELL_2000":"^RUT",
    "DOW_JONES":"^DJI",
}
CROSS_ASSET={
    "HIGH_YIELD":"HYG",
    "LONG_TREASURY":"TLT",
    "GOLD":"GLD",
}
FRED_SERIES={
    "HY_OAS":"BAMLH0A0HYM2",
    "YIELD_CURVE_10Y2Y":"T10Y2Y",
    "NFCI":"NFCI",
    "FED_FUNDS":"FEDFUNDS",
    "CPI":"CPIAUCSL",
    "UNEMPLOYMENT":"UNRATE",
}


class LongCycleHypothesisExperiment:
    version="us-long-cycle-hypothesis@0.2.1"
    protocol_version="secular-hypothesis-protocol@0.1.0"
    latest_file="us_long_cycle_hypothesis_latest.json"
    history_file="us_long_cycle_hypothesis_history.jsonl"

    def __init__(self,store:RunStore)->None:
        self.store=store

    @staticmethod
    def _canonical(payload:dict)->str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()


    @staticmethod
    def _fetch_yahoo_full(symbol:str,timeout:int=25)->dict:
        query=urllib.parse.urlencode({
            "period1":"0",
            "period2":str(int(time.time())+86400),
            "interval":"1d",
            "includeAdjustedClose":"true",
            "includePrePost":"false",
            "events":"div,splits",
        })
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}"
        req=urllib.request.Request(
            url,
            headers={"User-Agent":"Mozilla/5.0 TRIAID-FIN-LONG-CYCLE/0.2"},
        )
        with urllib.request.urlopen(req,timeout=timeout) as response:
            payload=json.loads(response.read().decode("utf-8"))
        chart=payload.get("chart") or {}
        if chart.get("error"):
            raise RuntimeError(f"yahoo_provider_error:{symbol}:{chart.get('error')}")
        result=(chart.get("result") or [None])[0]
        if not result:
            raise RuntimeError(f"yahoo_empty_result:{symbol}")
        ts=result.get("timestamp") or []
        indicators=result.get("indicators") or {}
        quote=(indicators.get("quote") or [{}])[0]
        raw_close=quote.get("close") or []
        adj=(indicators.get("adjclose") or [{}])[0].get("adjclose")
        close=adj if adj and len(adj)==len(ts) else raw_close
        rows=[]
        for stamp,price in zip(ts,close):
            try:
                x=float(price)
            except Exception:
                continue
            if not math.isfinite(x) or x<=0:
                continue
            rows.append((int(stamp),x))
        if len(rows)<300:
            raise RuntimeError(f"yahoo_insufficient_full_history:{symbol}:{len(rows)}")
        return {
            "symbol":symbol,
            "ts":[x[0] for x in rows],
            "close":[x[1] for x in rows],
        }

    @staticmethod
    def _percentile(values:list[float],current:float)->float|None:
        clean=[float(x) for x in values if math.isfinite(float(x))]
        if len(clean)<8:
            return None
        return sum(1 for x in clean if x<=current)/len(clean)

    @staticmethod
    def _max_drawdown(values:list[float])->float|None:
        if not values:
            return None
        peak=float(values[0])
        max_dd=0.0
        for value in values:
            x=float(value)
            peak=max(peak,x)
            if peak>0:
                max_dd=min(max_dd,x/peak-1.0)
        return max_dd

    @classmethod
    def _horizon_metrics(cls,ts:list[int],close:list[float],years:int)->dict:
        days=int(round(years*TRADING_DAYS_PER_YEAR))
        available=len(close)>=days+1 and len(ts)>=days+1
        base={
            "years":years,
            "trading_days":days,
            "available":available,
            "available_points":len(close),
        }
        if not available:
            return base

        start=float(close[-days-1])
        end=float(close[-1])
        window=[float(x) for x in close[-days-1:]]
        total_return=end/start-1.0 if start>0 else 0.0
        cagr=(end/start)**(TRADING_DAYS_PER_YEAR/days)-1.0 if start>0 and end>0 else 0.0
        daily=[
            float(close[i])/float(close[i-1])-1.0
            for i in range(len(close)-days,len(close))
            if float(close[i-1])>0
        ]
        vol=pstdev(daily)*math.sqrt(TRADING_DAYS_PER_YEAR) if len(daily)>=2 else 0.0
        peak=max(window)
        low=min(window)
        current_drawdown=end/peak-1.0 if peak>0 else 0.0
        distance_from_low=end/low-1.0 if low>0 else 0.0

        rolling=[]
        step=21
        for i in range(days,len(close),step):
            a=float(close[i-days])
            b=float(close[i])
            if a>0 and b>0:
                rolling.append((b/a)**(TRADING_DAYS_PER_YEAR/days)-1.0)
        percentile=cls._percentile(rolling,cagr)

        return {
            **base,
            "start_date":datetime.fromtimestamp(int(ts[-days-1]),timezone.utc).date().isoformat(),
            "end_date":datetime.fromtimestamp(int(ts[-1]),timezone.utc).date().isoformat(),
            "start_price":start,
            "end_price":end,
            "total_return":total_return,
            "cagr":cagr,
            "annualized_volatility":vol,
            "max_drawdown":cls._max_drawdown(window),
            "current_drawdown_from_window_peak":current_drawdown,
            "distance_from_window_low":distance_from_low,
            "cagr_historical_percentile":percentile,
            "historical_comparison_windows":len(rolling),
        }

    @classmethod
    def _asset_report(cls,label:str,symbol:str)->dict:
        series=cls._fetch_yahoo_full(symbol,timeout=25)
        horizons={
            str(year):cls._horizon_metrics(series["ts"],series["close"],year)
            for year in HORIZON_YEARS
        }
        return {
            "label":label,
            "symbol":symbol,
            "points":len(series["close"]),
            "first_date":datetime.fromtimestamp(int(series["ts"][0]),timezone.utc).date().isoformat(),
            "latest_date":datetime.fromtimestamp(int(series["ts"][-1]),timezone.utc).date().isoformat(),
            "latest_price":float(series["close"][-1]),
            "horizons":horizons,
        }

    @staticmethod
    def _fred_series(series_id:str,timeout:int=20)->list[tuple[date,float]]:
        query=urllib.parse.urlencode({"id":series_id})
        url=f"https://fred.stlouisfed.org/graph/fredgraph.csv?{query}"
        req=urllib.request.Request(
            url,
            headers={"User-Agent":"Mozilla/5.0 TRIAID-FIN-LONG-CYCLE/0.1"},
        )
        with urllib.request.urlopen(req,timeout=timeout) as response:
            text=response.read().decode("utf-8")
        rows=[]
        reader=csv.DictReader(io.StringIO(text))
        fields=reader.fieldnames or []
        value_key=series_id if series_id in fields else (fields[-1] if fields else "VALUE")
        date_key="observation_date" if "observation_date" in fields else ("DATE" if "DATE" in fields else (fields[0] if fields else "DATE"))
        for row in reader:
            raw=(row.get(value_key) or "").strip()
            if not raw or raw==".":
                continue
            try:
                d=date.fromisoformat((row.get(date_key) or "").strip())
                value=float(raw)
            except Exception:
                continue
            if math.isfinite(value):
                rows.append((d,value))
        if not rows:
            raise RuntimeError(f"empty_fred_series:{series_id}")
        return rows

    @classmethod
    def _fred_report(cls,name:str,series_id:str)->dict:
        rows=cls._fred_series(series_id)
        latest_date,latest_value=rows[-1]
        percentiles={}
        for years in (10,20,30):
            cutoff=latest_date-timedelta(days=int(365.25*years))
            sample=[v for d,v in rows if d>=cutoff]
            percentiles[str(years)]={
                "available":len(sample)>=24,
                "observations":len(sample),
                "percentile":cls._percentile(sample,latest_value),
            }
        return {
            "name":name,
            "series_id":series_id,
            "latest_date":latest_date.isoformat(),
            "latest_value":latest_value,
            "first_date":rows[0][0].isoformat(),
            "observations":len(rows),
            "percentiles":percentiles,
            "change_12_observations":(
                latest_value-rows[-13][1] if len(rows)>=13 else None
            ),
        }

    @staticmethod
    def _available_metric(asset:dict|None,years:int)->dict|None:
        if not asset:
            return None
        row=(asset.get("horizons") or {}).get(str(years))
        return row if row and row.get("available") else None

    @classmethod
    def _horizon_summary(cls,assets:dict,years:int)->dict:
        rows=[]
        for label in CORE_EQUITY:
            metric=cls._available_metric(assets.get(label),years)
            if metric:
                rows.append((label,metric))
        if not rows:
            return {
                "years":years,
                "available":False,
                "equity_count":0,
            }
        cagr=[float(r["cagr"]) for _,r in rows]
        pct=[
            float(r["cagr_historical_percentile"])
            for _,r in rows
            if r.get("cagr_historical_percentile") is not None
        ]
        current_dd=[float(r["current_drawdown_from_window_peak"]) for _,r in rows]
        return {
            "years":years,
            "available":True,
            "equity_count":len(rows),
            "equity_positive_fraction":sum(1 for x in cagr if x>0)/len(cagr),
            "equity_negative_fraction":sum(1 for x in cagr if x<0)/len(cagr),
            "mean_equity_cagr":mean(cagr),
            "min_equity_cagr":min(cagr),
            "max_equity_cagr":max(cagr),
            "cagr_dispersion":max(cagr)-min(cagr),
            "weak_historical_fraction":(
                sum(1 for x in pct if x<=0.20)/len(pct) if pct else None
            ),
            "stretched_historical_fraction":(
                sum(1 for x in pct if x>=0.80)/len(pct) if pct else None
            ),
            "drawdown_10pct_fraction":sum(1 for x in current_dd if x<=-0.10)/len(current_dd),
            "drawdown_15pct_fraction":sum(1 for x in current_dd if x<=-0.15)/len(current_dd),
            "drawdown_20pct_fraction":sum(1 for x in current_dd if x<=-0.20)/len(current_dd),
            "members":{
                label:{
                    "cagr":metric.get("cagr"),
                    "cagr_historical_percentile":metric.get("cagr_historical_percentile"),
                    "current_drawdown_from_window_peak":metric.get("current_drawdown_from_window_peak"),
                    "max_drawdown":metric.get("max_drawdown"),
                    "annualized_volatility":metric.get("annualized_volatility"),
                }
                for label,metric in rows
            },
        }

    @staticmethod
    def _fred_percentile(macro:dict,name:str,years:int=20)->float|None:
        row=macro.get(name) or {}
        p=(row.get("percentiles") or {}).get(str(years)) or {}
        value=p.get("percentile")
        return float(value) if value is not None else None

    @staticmethod
    def _evidence_row(name:str,category:str,available:bool,supported:bool|None,weight:float,evidence:dict)->dict:
        return {
            "name":name,
            "category":category,
            "available":bool(available),
            "supported":bool(supported) if available else None,
            "weight":float(weight),
            "evidence":evidence,
        }

    @classmethod
    def _downturn_hypothesis(cls,horizons:dict,assets:dict,macro:dict)->dict:
        evidence=[]
        h2=horizons.get("2") or {}
        h3=horizons.get("3") or {}
        h5=horizons.get("5") or {}
        evidence.append(cls._evidence_row(
            "2Y_BROAD_EQUITY_DETERIORATION","market_breadth",
            bool(h2.get("available")),
            bool(float(h2.get("equity_positive_fraction",1.0))<0.50) if h2.get("available") else None,
            1.0,
            {"equity_positive_fraction":h2.get("equity_positive_fraction")},
        ))
        evidence.append(cls._evidence_row(
            "3Y_BROAD_EQUITY_DETERIORATION","market_breadth",
            bool(h3.get("available")),
            bool(float(h3.get("equity_positive_fraction",1.0))<0.50) if h3.get("available") else None,
            1.0,
            {"equity_positive_fraction":h3.get("equity_positive_fraction")},
        ))
        weak5=h5.get("weak_historical_fraction")
        evidence.append(cls._evidence_row(
            "5Y_HISTORICAL_RETURN_WEAKNESS","long_cycle_return",
            bool(h5.get("available") and weak5 is not None),
            bool(float(weak5)>=0.50) if weak5 is not None else None,
            1.0,
            {"weak_historical_fraction":weak5},
        ))
        dd2=h2.get("drawdown_15pct_fraction")
        evidence.append(cls._evidence_row(
            "BROAD_EQUITY_DRAWDOWN_CONFIRMATION","drawdown",
            bool(h2.get("available") and dd2 is not None),
            bool(float(dd2)>=0.50) if dd2 is not None else None,
            1.0,
            {"drawdown_15pct_fraction":dd2},
        ))

        hyg=cls._available_metric(assets.get("HIGH_YIELD"),2)
        credit_supported=None
        if hyg:
            credit_supported=(
                float(hyg.get("cagr") or 0.0)<=0.0
                or float(hyg.get("current_drawdown_from_window_peak") or 0.0)<=-0.10
            )
        evidence.append(cls._evidence_row(
            "MARKET_CREDIT_STRESS","credit",
            hyg is not None,
            credit_supported,
            1.0,
            {
                "hyg_2y_cagr":hyg.get("cagr") if hyg else None,
                "hyg_2y_current_drawdown":hyg.get("current_drawdown_from_window_peak") if hyg else None,
            },
        ))

        hy_pct=cls._fred_percentile(macro,"HY_OAS",20)
        evidence.append(cls._evidence_row(
            "CREDIT_SPREAD_STRESS","credit_spread",
            hy_pct is not None,
            bool(hy_pct>=0.80) if hy_pct is not None else None,
            1.5,
            {"hy_oas_20y_percentile":hy_pct,"latest":(macro.get("HY_OAS") or {}).get("latest_value")},
        ))
        nfci_pct=cls._fred_percentile(macro,"NFCI",20)
        evidence.append(cls._evidence_row(
            "FINANCIAL_CONDITIONS_TIGHT","liquidity",
            nfci_pct is not None,
            bool(nfci_pct>=0.80) if nfci_pct is not None else None,
            1.0,
            {"nfci_20y_percentile":nfci_pct,"latest":(macro.get("NFCI") or {}).get("latest_value")},
        ))
        unemp=(macro.get("UNEMPLOYMENT") or {}).get("change_12_observations")
        evidence.append(cls._evidence_row(
            "UNEMPLOYMENT_DETERIORATION","macro",
            unemp is not None,
            bool(float(unemp)>=0.50) if unemp is not None else None,
            0.5,
            {"unemployment_change_12_observations":unemp},
        ))
        curve=(macro.get("YIELD_CURVE_10Y2Y") or {}).get("latest_value")
        evidence.append(cls._evidence_row(
            "YIELD_CURVE_INVERSION","macro",
            curve is not None,
            bool(float(curve)<0.0) if curve is not None else None,
            0.5,
            {"10y2y_spread":curve},
        ))

        available_weight=sum(x["weight"] for x in evidence if x["available"])
        support_weight=sum(x["weight"] for x in evidence if x["available"] and x["supported"])
        support_ratio=support_weight/available_weight if available_weight>0 else None
        if support_ratio is None:
            state="INSUFFICIENT_EVIDENCE"
        elif support_ratio>=0.67:
            state="MULTI_DIMENSION_CONFIRMED"
        elif support_ratio>=0.33:
            state="WATCH"
        else:
            state="NOT_CONFIRMED"
        return {
            "hypothesis_id":"SECULAR_DOWNTURN_CONFIRMATION",
            "claim":"A multi-year US equity downturn has enough cross-horizon and cross-asset evidence to justify testing a defensive overlay.",
            "state":state,
            "support_weight":support_weight,
            "available_weight":available_weight,
            "support_ratio":support_ratio,
            "evidence":evidence,
            "thresholds":{
                "watch_support_ratio":0.33,
                "confirmed_support_ratio":0.67,
            },
        }

    @classmethod
    def _stretch_hypothesis(cls,horizons:dict,assets:dict)->dict:
        evidence=[]
        for years in (7,10,15,20,25,30):
            row=horizons.get(str(years)) or {}
            fraction=row.get("stretched_historical_fraction")
            evidence.append(cls._evidence_row(
                f"{years}Y_BROAD_RETURN_STRETCH","secular_return_stretch",
                bool(row.get("available") and fraction is not None),
                bool(float(fraction)>=0.50) if fraction is not None else None,
                1.0,
                {
                    "stretched_historical_fraction":fraction,
                    "mean_equity_cagr":row.get("mean_equity_cagr"),
                },
            ))

        nasdaq=cls._available_metric(assets.get("NASDAQ_COMPOSITE"),5)
        russell=cls._available_metric(assets.get("RUSSELL_2000"),5)
        gap=None
        if nasdaq and russell:
            gap=float(nasdaq.get("cagr") or 0.0)-float(russell.get("cagr") or 0.0)
        evidence.append(cls._evidence_row(
            "5Y_GROWTH_SMALLCAP_CONCENTRATION_GAP","market_concentration_proxy",
            gap is not None,
            bool(gap>=0.05) if gap is not None else None,
            1.0,
            {"nasdaq_minus_russell_5y_cagr":gap},
        ))
        available_weight=sum(x["weight"] for x in evidence if x["available"])
        support_weight=sum(x["weight"] for x in evidence if x["available"] and x["supported"])
        support_ratio=support_weight/available_weight if available_weight>0 else None
        if support_ratio is None:
            state="INSUFFICIENT_EVIDENCE"
        elif support_ratio>=0.67:
            state="HIGH_STRETCH_EVIDENCE"
        elif support_ratio>=0.33:
            state="ELEVATED_STRETCH_EVIDENCE"
        else:
            state="NORMAL_RANGE"
        return {
            "hypothesis_id":"SECULAR_STRETCH_VULNERABILITY",
            "claim":"Long-horizon equity returns are unusually stretched relative to each asset's own history, creating vulnerability but not by itself a bearish trading signal.",
            "state":state,
            "support_weight":support_weight,
            "available_weight":available_weight,
            "support_ratio":support_ratio,
            "evidence":evidence,
            "thresholds":{
                "elevated_support_ratio":0.33,
                "high_support_ratio":0.67,
            },
            "interpretation_guard":"High long-run return stretch is a vulnerability indicator only. It must not trigger a defensive allocation without independent deterioration/credit/liquidity confirmation.",
        }

    def run(self,force:bool=False)->dict:
        previous=self.latest()
        assets={}
        errors={}
        for label,symbol in {**CORE_EQUITY,**CROSS_ASSET}.items():
            try:
                assets[label]=self._asset_report(label,symbol)
            except Exception as exc:
                errors[f"asset:{label}"]=f"{type(exc).__name__}:{exc}"

        if not any(label in assets for label in CORE_EQUITY):
            raise RuntimeError(f"no_core_equity_history:{errors}")

        as_of=max(
            assets[label]["latest_date"]
            for label in CORE_EQUITY
            if label in assets
        )
        if (
            previous
            and previous.get("as_of")==as_of
            and previous.get("version")==self.version
            and not force
        ):
            return previous

        macro={}
        for name,series_id in FRED_SERIES.items():
            try:
                macro[name]=self._fred_report(name,series_id)
            except Exception as exc:
                errors[f"fred:{name}"]=f"{type(exc).__name__}:{exc}"

        cpi=macro.get("CPI")
        fed=macro.get("FED_FUNDS")
        if cpi and fed:
            try:
                cpi_rows=self._fred_series(FRED_SERIES["CPI"])
                latest_cpi=float(cpi_rows[-1][1])
                yoy=None
                if len(cpi_rows)>=13 and float(cpi_rows[-13][1])>0:
                    yoy=latest_cpi/float(cpi_rows[-13][1])-1.0
                macro["REAL_POLICY_RATE_PROXY"]={
                    "latest_date":min(cpi.get("latest_date"),fed.get("latest_date")),
                    "fed_funds_percent":float(fed.get("latest_value")),
                    "cpi_yoy":yoy,
                    "real_policy_rate_proxy_percent":(
                        float(fed.get("latest_value"))-(100.0*yoy)
                        if yoy is not None else None
                    ),
                    "semantics":"Fed funds minus trailing CPI inflation; descriptive proxy, not a causal recession rule.",
                }
            except Exception as exc:
                errors["macro:REAL_POLICY_RATE_PROXY"]=f"{type(exc).__name__}:{exc}"

        horizons={
            str(year):self._horizon_summary(assets,year)
            for year in HORIZON_YEARS
        }
        hypotheses={
            "downturn_confirmation":self._downturn_hypothesis(horizons,assets,macro),
            "stretch_vulnerability":self._stretch_hypothesis(horizons,assets),
        }

        payload={
            "version":self.version,
            "protocol_version":self.protocol_version,
            "market_id":"US",
            "as_of":as_of,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "shadow_only":True,
            "applied_to_weights":False,
            "production_action":"NONE",
            "horizon_years":list(HORIZON_YEARS),
            "horizon_trading_days":{str(y):int(y*TRADING_DAYS_PER_YEAR) for y in HORIZON_YEARS},
            "assets":assets,
            "horizon_summary":horizons,
            "macro":macro,
            "hypotheses":hypotheses,
            "errors":errors,
            "data_completeness":{
                "core_equity_assets":sum(1 for x in CORE_EQUITY if x in assets),
                "core_equity_assets_required":len(CORE_EQUITY),
                "cross_assets":sum(1 for x in CROSS_ASSET if x in assets),
                "cross_assets_requested":len(CROSS_ASSET),
                "macro_series":sum(1 for x in FRED_SERIES if x in macro),
                "macro_series_requested":len(FRED_SERIES),
                "valuation_model_connected":False,
                "earnings_revision_model_connected":False,
            },
            "experiment_design":{
                "objective":"Test whether very-long-horizon bearish narratives are supported by measurable multi-cycle market, credit and macro evidence without allowing narrative prestige or repetition to alter weights.",
                "cycles":"2/3/5/7/10/15/20/25/30 years; 30 years is the target full secular window where data history permits.",
                "separation":"SECULAR_STRETCH_VULNERABILITY and SECULAR_DOWNTURN_CONFIRMATION are deliberately separate. Stretch alone is not a short signal.",
                "promotion_rule":"Shadow-only. Any future production use requires prospective evidence that the long-cycle layer improves realizable net return after opportunity cost, switching cost, drawdown and capacity effects.",
                "anti_hindsight":"Current observations are evaluated against pre-registered rules and historical percentiles. No production weight is changed by this experiment.",
            },
            "known_limits":[
                "ETF histories for HYG/TLT/GLD are shorter than 30 years; unavailable horizons remain unavailable rather than imputed.",
                "Valuation and analyst earnings-revision feeds are not yet connected to this experiment.",
                "Macro series are descriptive evidence and do not establish causality by themselves.",
                "Historical/model evidence is not a calibrated future-return forecast.",
            ],
        }
        body=dict(payload)
        digest=self._hash(body)
        payload["experiment_hash"]=digest
        payload["experiment_id"]=f"US-LONG-{as_of}-{digest[:10]}"
        payload["previous_experiment_id"]=previous.get("experiment_id") if previous else None

        self.store.save_json(self.latest_file,payload)
        history=self.history(5000)
        if not any(row.get("experiment_id")==payload["experiment_id"] for row in history):
            self.store.append_jsonl(self.history_file,payload)
        return payload

    def latest(self)->dict|None:
        payload=self.store.load_json(self.latest_file,default={})
        return payload or None

    def history(self,limit:int=100)->list[dict]:
        return self.store.read_jsonl(self.history_file,limit=limit)

    def status(self)->dict:
        latest=self.latest()
        return {
            "version":self.version,
            "protocol_version":self.protocol_version,
            "shadow_only":True,
            "applied_to_weights":False,
            "latest_experiment_id":latest.get("experiment_id") if latest else None,
            "latest_as_of":latest.get("as_of") if latest else None,
            "history_count":len(self.history(5000)),
        }
