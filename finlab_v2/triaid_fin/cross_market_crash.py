from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from statistics import mean

from .long_cycle_hypothesis import LongCycleHypothesisExperiment
from .store import RunStore


MARKET_INDEXES={
    "US":{
        "SP500":"^GSPC",
        "NASDAQ_COMPOSITE":"^IXIC",
    },
    "CN":{
        "SHANGHAI_COMPOSITE":"000001.SS",
        "CSI300":"000300.SS",
        "SHENZHEN_COMPONENT":"399001.SZ",
        "CHINEXT":"399006.SZ",
    },
    "HK":{
        "HANG_SENG":"^HSI",
        "HANG_SENG_CHINA_ENTERPRISES":"^HSCE",
    },
}
PRIMARY_INDEX={
    "US":"SP500",
    "CN":"SHANGHAI_COMPOSITE",
    "HK":"HANG_SENG",
}
DATA_SOURCE_REVISION="market-provider-split@0.1.0"
CANONICAL_EPISODES={
    "ASIAN_FINANCIAL_CRISIS_1997_98":{
        "start":"1997-06-01","end":"1999-01-31",
        "description":"Asian financial crisis and regional equity stress.",
    },
    "GLOBAL_FINANCIAL_CRISIS":{
        "start":"2007-10-01","end":"2009-06-30",
        "description":"Global financial crisis / Great Recession equity collapse.",
    },
    "CHINA_EQUITY_CRASH_2015_16":{
        "start":"2015-05-01","end":"2016-03-31",
        "description":"China equity crash and subsequent regional/global spillover period.",
    },
    "COVID_2020":{
        "start":"2020-01-01","end":"2020-06-30",
        "description":"COVID-19 global market shock.",
    },
    "GLOBAL_RATE_SHOCK_2022":{
        "start":"2021-12-01","end":"2022-12-31",
        "description":"Global tightening / rate-shock equity drawdown.",
    },
}


class CrossMarketCrashExperiment:
    version="us-cn-hk-crash-linkage@0.2.0"
    protocol_version="cross-market-crash-linkage-protocol@0.2.0"
    latest_file="us_cn_hk_crash_linkage_latest.json"
    history_file="us_cn_hk_crash_linkage_history.jsonl"

    def __init__(self,store:RunStore)->None:
        self.store=store

    @staticmethod
    def _canonical(payload:dict)->str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _series_map(series:dict)->dict[date,float]:
        return {
            datetime.fromtimestamp(int(ts),timezone.utc).date():float(px)
            for ts,px in zip(series["ts"],series["close"])
            if float(px)>0
        }

    @staticmethod
    def _returns(values:list[tuple[date,float]])->dict[date,float]:
        out={}
        for i in range(1,len(values)):
            prior=float(values[i-1][1])
            current=float(values[i][1])
            if prior>0:
                out[values[i][0]]=current/prior-1.0
        return out

    @staticmethod
    def _corr(xs:list[float],ys:list[float])->float|None:
        if len(xs)<20 or len(xs)!=len(ys):
            return None
        mx=mean(xs);my=mean(ys)
        vx=sum((x-mx)**2 for x in xs)
        vy=sum((y-my)**2 for y in ys)
        if vx<=0 or vy<=0:
            return None
        return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.sqrt(vx*vy)

    @classmethod
    def _lead_lag_corr(
        cls,
        a_returns:dict[date,float],
        b_returns:dict[date,float],
        start:date,
        end:date,
        *,
        market_a:str,
        market_b:str,
        max_lag:int=20,
    )->dict:
        dates=sorted(d for d in set(a_returns)&set(b_returns) if start<=d<=end)
        if len(dates)<40:
            return {"available":False,"observations":len(dates)}
        a=[float(a_returns[d]) for d in dates]
        b=[float(b_returns[d]) for d in dates]
        rows=[]
        for lag in range(-max_lag,max_lag+1):
            if lag>0:
                xs=a[:-lag];ys=b[lag:]
            elif lag<0:
                k=-lag;xs=a[k:];ys=b[:-k]
            else:
                xs=a;ys=b
            corr=cls._corr(xs,ys)
            if corr is not None:
                rows.append({"lag_trading_days":lag,"correlation":corr,"observations":len(xs)})
        if not rows:
            return {"available":False,"observations":len(dates)}
        best=max(rows,key=lambda r:abs(float(r["correlation"])))
        return {
            "available":True,
            "market_a":market_a,
            "market_b":market_b,
            "same_day_correlation":next((r["correlation"] for r in rows if r["lag_trading_days"]==0),None),
            "strongest_absolute_correlation":best["correlation"],
            "strongest_lag_trading_days":best["lag_trading_days"],
            "lag_semantics":f"positive lag = {market_a} daily return leads {market_b}; negative lag = {market_b} leads {market_a}",
            "rows":rows,
        }

    @staticmethod
    def _window_stats(series:dict,start:date,end:date)->dict:
        rows=sorted(
            (d,p) for d,p in CrossMarketCrashExperiment._series_map(series).items()
            if start<=d<=end
        )
        if len(rows)<20:
            return {"available":False,"observations":len(rows)}
        first_d,first_p=rows[0]
        last_d,last_p=rows[-1]
        peak=float(first_p);peak_date=first_d
        trough_dd=0.0;trough_date=first_d;trough_price=float(first_p);event_peak_date=peak_date
        for d,p0 in rows:
            p=float(p0)
            if p>peak:
                peak=p;peak_date=d
            dd=p/peak-1.0 if peak>0 else 0.0
            if dd<trough_dd:
                trough_dd=dd
                trough_date=d
                trough_price=p
                event_peak_date=peak_date
        total=float(last_p)/float(first_p)-1.0 if float(first_p)>0 else None
        return {
            "available":True,
            "observations":len(rows),
            "first_date":first_d.isoformat(),
            "last_date":last_d.isoformat(),
            "first_price":float(first_p),
            "last_price":float(last_p),
            "window_return":total,
            "max_drawdown":trough_dd,
            "peak_date_for_max_drawdown":event_peak_date.isoformat(),
            "trough_date":trough_date.isoformat(),
            "trough_price":trough_price,
            "crash_20pct":trough_dd<=-0.20,
            "stress_10pct":trough_dd<=-0.10,
        }

    @staticmethod
    def _detect_crashes(series:dict,threshold:float=-0.20,recovery_drawdown:float=-0.05)->list[dict]:
        rows=sorted(CrossMarketCrashExperiment._series_map(series).items())
        if not rows:
            return []
        events=[]
        peak_price=float(rows[0][1]);peak_date=rows[0][0]
        active=None
        for d,p0 in rows[1:]:
            p=float(p0)
            if active is None:
                if p>peak_price:
                    peak_price=p;peak_date=d
                dd=p/peak_price-1.0 if peak_price>0 else 0.0
                if dd<=threshold:
                    active={
                        "peak_date":peak_date,
                        "peak_price":peak_price,
                        "breach_date":d,
                        "trough_date":d,
                        "trough_price":p,
                        "max_drawdown":dd,
                    }
            else:
                dd=p/float(active["peak_price"])-1.0
                if dd<float(active["max_drawdown"]):
                    active["max_drawdown"]=dd
                    active["trough_date"]=d
                    active["trough_price"]=p
                if dd>=recovery_drawdown:
                    active["recovery_date"]=d
                    active["recovery_price"]=p
                    active["calendar_days_peak_to_trough"]=(active["trough_date"]-active["peak_date"]).days
                    active["calendar_days_peak_to_recovery"]=(d-active["peak_date"]).days
                    events.append({
                        **active,
                        "peak_date":active["peak_date"].isoformat(),
                        "breach_date":active["breach_date"].isoformat(),
                        "trough_date":active["trough_date"].isoformat(),
                        "recovery_date":d.isoformat(),
                    })
                    active=None
                    peak_price=p;peak_date=d
        if active is not None:
            active["recovery_date"]=None
            active["recovery_price"]=None
            active["calendar_days_peak_to_trough"]=(active["trough_date"]-active["peak_date"]).days
            active["calendar_days_peak_to_recovery"]=None
            events.append({
                **active,
                "peak_date":active["peak_date"].isoformat(),
                "breach_date":active["breach_date"].isoformat(),
                "trough_date":active["trough_date"].isoformat(),
            })
        return events

    @staticmethod
    def _nearest_event(target:dict,events:list[dict],max_days:int=180)->dict|None:
        td=date.fromisoformat(str(target["trough_date"]))
        candidates=[]
        for row in events:
            d=date.fromisoformat(str(row["trough_date"]))
            gap=(d-td).days
            if abs(gap)<=max_days:
                candidates.append((abs(gap),gap,row))
        if not candidates:
            return None
        _,gap,row=min(candidates,key=lambda x:(x[0],x[1]))
        return {"event":row,"trough_lag_calendar_days":gap}

    @classmethod
    def _pairwise_automatic_linkage(cls,events_by_market:dict[str,list[dict]])->dict:
        out={}
        markets=sorted(events_by_market)
        for i,a in enumerate(markets):
            for b in markets[i+1:]:
                key=f"{a}_{b}"
                rows=[]
                for origin,counterpart in ((a,b),(b,a)):
                    for event in events_by_market.get(origin,[]):
                        match=cls._nearest_event(event,events_by_market.get(counterpart,[]),180)
                        rows.append({
                            "origin_market":origin,
                            "counterpart_market":counterpart,
                            "event":event,
                            "counterpart_within_180_days":match,
                            "classification":"SYNCHRONIZED_20PCT_CRASH" if match else "NO_20PCT_COUNTERPART_WITHIN_180D",
                        })
                out[key]=rows
        return out

    @classmethod
    def _episode_report(
        cls,
        episode_id:str,
        spec:dict,
        primary_series:dict[str,dict],
    )->dict:
        start=date.fromisoformat(spec["start"])
        end=date.fromisoformat(spec["end"])
        stats={
            market:cls._window_stats(series,start,end)
            for market,series in primary_series.items()
        }
        returns={
            market:cls._returns(sorted(cls._series_map(series).items()))
            for market,series in primary_series.items()
        }
        pairwise={}
        markets=sorted(primary_series)
        for i,a in enumerate(markets):
            for b in markets[i+1:]:
                pairwise[f"{a}_{b}"]=cls._lead_lag_corr(
                    returns[a],returns[b],start,end,
                    market_a=a,market_b=b,
                )

        trough_rows=[]
        for market,row in stats.items():
            if row.get("available") and row.get("trough_date"):
                trough_rows.append((date.fromisoformat(row["trough_date"]),market))
        trough_rows.sort()
        trough_order=[{"market":market,"trough_date":d.isoformat()} for d,market in trough_rows]
        trough_span_days=(
            (trough_rows[-1][0]-trough_rows[0][0]).days
            if len(trough_rows)>=2 else None
        )

        crash_count=sum(1 for row in stats.values() if row.get("crash_20pct"))
        stress_count=sum(1 for row in stats.values() if row.get("stress_10pct"))
        available_count=sum(1 for row in stats.values() if row.get("available"))
        if available_count>=3 and crash_count==3:
            relation="ALL_THREE_20PCT_CRASH"
        elif crash_count>=2:
            relation="TWO_OR_MORE_20PCT_CRASH"
        elif available_count>=3 and stress_count==3:
            relation="ALL_THREE_STRESSED"
        elif stress_count>=2:
            relation="TWO_OR_MORE_STRESSED"
        elif stress_count==1:
            relation="LOCALIZED_STRESS"
        else:
            relation="WEAK_SHARED_STRESS"

        return {
            "episode_id":episode_id,
            "description":spec["description"],
            "window":{"start":spec["start"],"end":spec["end"]},
            "markets":stats,
            "pairwise_daily_return_linkage":pairwise,
            "trough_order":trough_order,
            "trough_span_calendar_days":trough_span_days,
            "crash_20pct_market_count":crash_count,
            "stress_10pct_market_count":stress_count,
            "relation":relation,
        }

    def run(self,force:bool=False)->dict:
        previous=self.latest()
        errors={}
        indexes={}
        for market,specs in MARKET_INDEXES.items():
            rows={}
            for label,symbol in specs.items():
                try:
                    rows[label]=LongCycleHypothesisExperiment._fetch_market_full(symbol,timeout=30)
                except Exception as exc:
                    errors[f"{market}:{label}"]=f"{type(exc).__name__}:{exc}"
            indexes[market]=rows

        primary_series={}
        for market,label in PRIMARY_INDEX.items():
            row=(indexes.get(market) or {}).get(label)
            if row is None:
                raise RuntimeError(f"primary_index_unavailable:{market}:{label}:{errors}")
            primary_series[market]=row

        as_of=min(
            datetime.fromtimestamp(int(series["ts"][-1]),timezone.utc).date()
            for series in primary_series.values()
        ).isoformat()
        if (
            previous
            and previous.get("as_of")==as_of
            and previous.get("version")==self.version
            and previous.get("data_source_revision")==DATA_SOURCE_REVISION
            and not force
        ):
            return previous

        events_by_market={
            market:self._detect_crashes(series,-0.20,-0.05)
            for market,series in primary_series.items()
        }
        pairwise_automatic=self._pairwise_automatic_linkage(events_by_market)
        episodes={
            key:self._episode_report(key,spec,primary_series)
            for key,spec in CANONICAL_EPISODES.items()
        }

        synchronized_rows=sum(
            1 for rows in pairwise_automatic.values() for row in rows
            if row.get("counterpart_within_180_days")
        )
        independent_rows=sum(
            1 for rows in pairwise_automatic.values() for row in rows
            if not row.get("counterpart_within_180_days")
        )

        payload={
            "version":self.version,
            "protocol_version":self.protocol_version,
            "data_source_revision":DATA_SOURCE_REVISION,
            "as_of":as_of,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "shadow_only":True,
            "applied_to_weights":False,
            "production_action":"NONE",
            "markets":["US","CN","HK"],
            "primary_indexes":{
                "US":"S&P 500 (^GSPC)",
                "CN":"Shanghai Composite (000001.SS)",
                "HK":"Hang Seng Index (^HSI)",
            },
            "secondary_indexes":{
                market:list(specs)
                for market,specs in MARKET_INDEXES.items()
            },
            "crash_definition":{
                "trigger":"20% peak-to-current drawdown",
                "recovery":"returns to within 5% of pre-crash peak",
                "pairing_window_calendar_days":180,
                "stress_threshold":"10% maximum drawdown within canonical episode window",
            },
            "detected_crashes":{
                **events_by_market,
                "pairwise_automatic_linkage":pairwise_automatic,
                "synchronized_pair_rows":synchronized_rows,
                "independent_pair_rows":independent_rows,
            },
            "canonical_episode_studies":episodes,
            "data_completeness":{
                **{
                    f"{market}_indexes":len(indexes.get(market) or {})
                    for market in MARKET_INDEXES
                },
                **{
                    f"{market}_indexes_requested":len(specs)
                    for market,specs in MARKET_INDEXES.items()
                },
                "primary_indexes_complete":all(
                    (indexes.get(market) or {}).get(label) is not None
                    for market,label in PRIMARY_INDEX.items()
                ),
                "errors":errors,
            },
            "interpretation":{
                "purpose":"Measure whether major US, A-share and Hong Kong equity crashes are synchronized, lead-lag linked, regionally transmitted, or predominantly local.",
                "causality_guard":"Temporal lead/lag and return correlation do not establish causal transmission.",
                "selection_guard":"Canonical episodes are disclosed in advance; automatic crash detection is also reported to reduce cherry-picking.",
                "hong_kong_role":"Hong Kong is treated as an open, globally connected China-sensitive market that can differ materially from onshore A shares.",
                "production_guard":"This experiment cannot change strategy weights until prospective incremental net-return value is established.",
            },
        }
        digest=self._hash(payload)
        payload["experiment_hash"]=digest
        payload["experiment_id"]=f"USCNHK-CRASH-{as_of}-{digest[:10]}"
        payload["previous_experiment_id"]=previous.get("experiment_id") if previous else None
        self.store.save_json(self.latest_file,payload)
        history=self.history(5000)
        if not any(row.get("experiment_id")==payload["experiment_id"] for row in history):
            self.store.append_jsonl(self.history_file,payload)
        return payload

    def latest(self)->dict|None:
        row=self.store.load_json(self.latest_file,default={})
        return row or None

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
