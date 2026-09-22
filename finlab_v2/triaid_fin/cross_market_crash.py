from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from statistics import mean
from typing import Any

from .long_cycle_hypothesis import LongCycleHypothesisExperiment
from .store import RunStore


US_INDEXES={
    "SP500":"^GSPC",
    "NASDAQ_COMPOSITE":"^IXIC",
}
CN_INDEXES={
    "SHANGHAI_COMPOSITE":"000001.SS",
    "CSI300":"000300.SS",
    "SHENZHEN_COMPONENT":"399001.SZ",
    "CHINEXT":"399006.SZ",
}
CANONICAL_EPISODES={
    "GLOBAL_FINANCIAL_CRISIS":{
        "start":"2007-10-01","end":"2009-06-30",
        "description":"Global financial crisis / Great Recession equity collapse.",
    },
    "CHINA_EQUITY_CRASH_2015_16":{
        "start":"2015-05-01","end":"2016-03-31",
        "description":"China equity crash and subsequent global spillover period.",
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
    version="us-cn-crash-linkage@0.1.0"
    protocol_version="cross-market-crash-linkage-protocol@0.1.0"
    latest_file="us_cn_crash_linkage_latest.json"
    history_file="us_cn_crash_linkage_history.jsonl"

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
        us_returns:dict[date,float],
        cn_returns:dict[date,float],
        start:date,
        end:date,
        max_lag:int=20,
    )->dict:
        dates=sorted(d for d in set(us_returns)&set(cn_returns) if start<=d<=end)
        if len(dates)<40:
            return {"available":False,"observations":len(dates)}
        us=[float(us_returns[d]) for d in dates]
        cn=[float(cn_returns[d]) for d in dates]
        rows=[]
        for lag in range(-max_lag,max_lag+1):
            if lag>0:
                # US[t] versus CN[t+lag]: positive lag means US leads CN.
                xs=us[:-lag]
                ys=cn[lag:]
            elif lag<0:
                k=-lag
                xs=us[k:]
                ys=cn[:-k]
            else:
                xs=us
                ys=cn
            corr=cls._corr(xs,ys)
            if corr is not None:
                rows.append({"lag_trading_days":lag,"correlation":corr,"observations":len(xs)})
        if not rows:
            return {"available":False,"observations":len(dates)}
        best=max(rows,key=lambda r:abs(float(r["correlation"])))
        return {
            "available":True,
            "same_day_correlation":next((r["correlation"] for r in rows if r["lag_trading_days"]==0),None),
            "strongest_absolute_correlation":best["correlation"],
            "strongest_lag_trading_days":best["lag_trading_days"],
            "lag_semantics":"positive lag = US daily return leads CN daily return; negative lag = CN leads US",
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
        peak=float(rows[0][1]);peak_date=rows[0][0]
        trough_dd=0.0;trough_date=rows[0][0];trough_price=float(rows[0][1]);event_peak_date=peak_date
        for d,p in rows:
            p=float(p)
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
        return {
            "event":row,
            "trough_lag_calendar_days":gap,
        }

    @classmethod
    def _automatic_linkage(cls,us_events:list[dict],cn_events:list[dict])->list[dict]:
        rows=[]
        for origin,events,counterparts in (
            ("US",us_events,cn_events),
            ("CN",cn_events,us_events),
        ):
            for event in events:
                match=cls._nearest_event(event,counterparts,180)
                rows.append({
                    "origin_market":origin,
                    "event":event,
                    "counterpart_within_180_days":match,
                    "classification":"SYNCHRONIZED_20PCT_CRASH" if match else "NO_20PCT_COUNTERPART_WITHIN_180D",
                })
        return rows

    @classmethod
    def _episode_report(
        cls,
        episode_id:str,
        spec:dict,
        us:dict,
        cn:dict,
    )->dict:
        start=date.fromisoformat(spec["start"])
        end=date.fromisoformat(spec["end"])
        us_stats=cls._window_stats(us,start,end)
        cn_stats=cls._window_stats(cn,start,end)
        us_ret=cls._returns(sorted(cls._series_map(us).items()))
        cn_ret=cls._returns(sorted(cls._series_map(cn).items()))
        leadlag=cls._lead_lag_corr(us_ret,cn_ret,start,end)

        lag=None
        if us_stats.get("available") and cn_stats.get("available"):
            lag=(
                date.fromisoformat(cn_stats["trough_date"])
                -date.fromisoformat(us_stats["trough_date"])
            ).days
        if us_stats.get("crash_20pct") and cn_stats.get("crash_20pct"):
            relation="BOTH_20PCT_CRASH"
        elif us_stats.get("stress_10pct") and cn_stats.get("stress_10pct"):
            relation="BOTH_STRESSED"
        elif us_stats.get("stress_10pct") or cn_stats.get("stress_10pct"):
            relation="ONE_SIDE_DOMINANT"
        else:
            relation="WEAK_SHARED_STRESS"
        return {
            "episode_id":episode_id,
            "description":spec["description"],
            "window":{"start":spec["start"],"end":spec["end"]},
            "US":us_stats,
            "CN":cn_stats,
            "cn_trough_minus_us_trough_calendar_days":lag,
            "trough_lag_semantics":"negative = CN trough occurred earlier; positive = US trough occurred earlier",
            "daily_return_linkage":leadlag,
            "relation":relation,
        }

    def run(self,force:bool=False)->dict:
        previous=self.latest()
        errors={}
        indexes={}
        for market,specs in (("US",US_INDEXES),("CN",CN_INDEXES)):
            market_rows={}
            for label,symbol in specs.items():
                try:
                    market_rows[label]=LongCycleHypothesisExperiment._fetch_yahoo_full(symbol,timeout=30)
                except Exception as exc:
                    errors[f"{market}:{label}"]=f"{type(exc).__name__}:{exc}"
            indexes[market]=market_rows

        us_primary=(indexes.get("US") or {}).get("SP500")
        cn_primary=(indexes.get("CN") or {}).get("SHANGHAI_COMPOSITE")
        if us_primary is None or cn_primary is None:
            raise RuntimeError(f"primary_index_unavailable:{errors}")

        as_of=min(
            datetime.fromtimestamp(int(us_primary["ts"][-1]),timezone.utc).date(),
            datetime.fromtimestamp(int(cn_primary["ts"][-1]),timezone.utc).date(),
        ).isoformat()
        if (
            previous
            and previous.get("as_of")==as_of
            and previous.get("version")==self.version
            and not force
        ):
            return previous

        us_events=self._detect_crashes(us_primary,-0.20,-0.05)
        cn_events=self._detect_crashes(cn_primary,-0.20,-0.05)
        episodes={
            key:self._episode_report(key,spec,us_primary,cn_primary)
            for key,spec in CANONICAL_EPISODES.items()
        }
        linkage=self._automatic_linkage(us_events,cn_events)

        paired=[r for r in linkage if r.get("counterpart_within_180_days")]
        independent=[r for r in linkage if not r.get("counterpart_within_180_days")]
        payload={
            "version":self.version,
            "protocol_version":self.protocol_version,
            "as_of":as_of,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "shadow_only":True,
            "applied_to_weights":False,
            "production_action":"NONE",
            "primary_indexes":{"US":"S&P 500 (^GSPC)","CN":"Shanghai Composite (000001.SS)"},
            "secondary_indexes":{
                "US":list(US_INDEXES),
                "CN":list(CN_INDEXES),
            },
            "crash_definition":{
                "trigger":"20% peak-to-current drawdown",
                "recovery":"returns to within 5% of pre-crash peak",
                "pairing_window_calendar_days":180,
                "stress_threshold":"10% maximum drawdown within canonical episode window",
            },
            "detected_crashes":{
                "US":us_events,
                "CN":cn_events,
                "automatic_linkage":linkage,
                "paired_event_rows":len(paired),
                "independent_event_rows":len(independent),
            },
            "canonical_episode_studies":episodes,
            "data_completeness":{
                "US_indexes":len(indexes.get("US") or {}),
                "US_indexes_requested":len(US_INDEXES),
                "CN_indexes":len(indexes.get("CN") or {}),
                "CN_indexes_requested":len(CN_INDEXES),
                "errors":errors,
            },
            "interpretation":{
                "purpose":"Measure whether major US and A-share crashes are synchronized, lead-lag linked, or predominantly local.",
                "causality_guard":"Temporal lead/lag and return correlation do not establish causal transmission.",
                "selection_guard":"Canonical episodes are disclosed in advance; automatic crash detection is also reported to reduce cherry-picking.",
                "production_guard":"This experiment cannot change strategy weights until prospective incremental net-return value is established.",
            },
            "historical_source_context":{
                "GLOBAL_FINANCIAL_CRISIS":"Federal Reserve History reports the S&P 500 fell 57% from its October 2007 peak to March 2009 trough.",
                "CHINA_EQUITY_CRASH_2015_16":"IMF reported Chinese equities fell more than 30% in less than three weeks after mid-June 2015 and documented spillovers to other markets.",
                "COVID_2020":"Federal Reserve reported broad US equities fell as much as 34% peak-to-trough during the COVID shock.",
            },
        }
        digest=self._hash(payload)
        payload["experiment_hash"]=digest
        payload["experiment_id"]=f"USCN-CRASH-{as_of}-{digest[:10]}"
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
