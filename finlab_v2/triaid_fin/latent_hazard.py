from __future__ import annotations

import bisect
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev

from .cross_market_crash import CrossMarketCrashExperiment, MARKET_INDEXES, PRIMARY_INDEX
from .long_cycle_hypothesis import LongCycleHypothesisExperiment
from .store import RunStore


LEADS=(20,60,120,250)
FACTOR_ALERT_PERCENTILE=0.80
CONTROL_STEP=63
CRASH_EXCLUSION_SESSIONS=250


class LatentHazardExperiment:
    version="latent-hazard-discovery@0.1.1"
    protocol_version="point-in-time-hazard-protocol@0.1.0"
    latest_file="latent_hazard_latest.json"
    history_file="latent_hazard_history.jsonl"

    def __init__(self,store:RunStore)->None:
        self.store=store

    @staticmethod
    def _canonical(payload:dict)->str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()


    @staticmethod
    def _fred_with_retry(series_id:str,attempts:int=3)->list[tuple[date,float]]:
        errors=[]
        for attempt in range(max(1,int(attempts))):
            try:
                return LongCycleHypothesisExperiment._fred_series(series_id,timeout=30)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}:{exc}")
                if attempt+1<attempts:
                    time.sleep(1.5*(attempt+1))
        raise RuntimeError(f"fred_retry_exhausted:{series_id}:{' | '.join(errors)}")

    @staticmethod
    def _series_map(series:dict)->dict[date,float]:
        return {
            datetime.fromtimestamp(int(ts),timezone.utc).date():float(px)
            for ts,px in zip(series["ts"],series["close"])
            if float(px)>0
        }

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

    @staticmethod
    def _percentile(history:list[float],value:float)->float|None:
        vals=sorted(float(x) for x in history if math.isfinite(float(x)))
        if len(vals)<20 or not math.isfinite(float(value)):
            return None
        return bisect.bisect_right(vals,float(value))/len(vals)

    @staticmethod
    def _event_clusters(events_by_market:dict[str,list[dict]])->list[dict]:
        rows=[]
        for market,events in events_by_market.items():
            for event in events:
                rows.append({
                    "market":market,
                    "breach_date":date.fromisoformat(event["breach_date"]),
                    "trough_date":date.fromisoformat(event["trough_date"]),
                    "max_drawdown":float(event["max_drawdown"]),
                })
        rows.sort(key=lambda x:x["breach_date"])
        clusters=[]
        for row in rows:
            if not clusters or (row["breach_date"]-clusters[-1]["last_breach_date"]).days>180:
                clusters.append({
                    "anchor_date":row["breach_date"],
                    "last_breach_date":row["breach_date"],
                    "markets":{row["market"]},
                    "members":[row],
                })
            else:
                cluster=clusters[-1]
                cluster["last_breach_date"]=max(cluster["last_breach_date"],row["breach_date"])
                cluster["markets"].add(row["market"])
                cluster["members"].append(row)
        out=[]
        for i,c in enumerate(clusters,1):
            out.append({
                "event_id":f"CRASH_CLUSTER_{i:02d}_{c['anchor_date'].isoformat()}",
                "anchor_date":c["anchor_date"],
                "last_breach_date":c["last_breach_date"],
                "markets":sorted(c["markets"]),
                "market_count":len(c["markets"]),
                "max_drawdown":min(float(x["max_drawdown"]) for x in c["members"]),
                "members":c["members"],
            })
        return out

    @staticmethod
    def _aligned(primary:dict[str,dict])->tuple[list[date],dict[str,list[float]]]:
        maps={m:LatentHazardExperiment._series_map(s) for m,s in primary.items()}
        common=sorted(set.intersection(*(set(x) for x in maps.values())))
        prices={m:[maps[m][d] for d in common] for m in maps}
        return common,prices

    @staticmethod
    def _ret(prices:list[float],i:int,days:int)->float|None:
        if i-days<0 or prices[i-days]<=0:
            return None
        return prices[i]/prices[i-days]-1.0

    @staticmethod
    def _dd(prices:list[float],i:int,days:int)->float|None:
        if i-days+1<0:
            return None
        window=prices[i-days+1:i+1]
        peak=max(window)
        return prices[i]/peak-1.0 if peak>0 else None

    @staticmethod
    def _vol(prices:list[float],i:int,days:int)->float|None:
        if i-days<0:
            return None
        returns=[
            prices[j]/prices[j-1]-1.0
            for j in range(i-days+1,i+1)
            if prices[j-1]>0
        ]
        return pstdev(returns)*math.sqrt(252) if len(returns)>=20 else None

    @classmethod
    def _pair_corr(cls,a:list[float],b:list[float],i:int,days:int=60)->float|None:
        if i-days<0:
            return None
        ar=[];br=[]
        for j in range(i-days+1,i+1):
            if a[j-1]<=0 or b[j-1]<=0:
                continue
            ar.append(a[j]/a[j-1]-1.0)
            br.append(b[j]/b[j-1]-1.0)
        return cls._corr(ar,br)

    @staticmethod
    def _fred_value(rows:list[tuple[date,float]],d:date)->float|None:
        dates=[x[0] for x in rows]
        i=bisect.bisect_right(dates,d)-1
        return float(rows[i][1]) if i>=0 else None

    @classmethod
    def _fred_percentile(cls,rows:list[tuple[date,float]],d:date,value:float|None)->float|None:
        if value is None:
            return None
        hist=[float(v) for rd,v in rows if rd<=d]
        return cls._percentile(hist,value)

    @classmethod
    def _features(
        cls,
        dates:list[date],
        prices:dict[str,list[float]],
        i:int,
        fred:dict[str,list[tuple[date,float]]],
    )->dict[str,float]:
        d=dates[i]
        out={}
        drawdowns={}
        for market in ("US","CN","HK"):
            px=prices[market]
            dd=cls._dd(px,i,252)
            r63=cls._ret(px,i,63)
            vol=cls._vol(px,i,63)
            r5y=cls._ret(px,i,1260)
            if dd is not None:
                out[f"{market}_DRAWDOWN_STRESS_252"]=-dd
                drawdowns[market]=dd
            if r63 is not None:
                out[f"{market}_NEGATIVE_MOMENTUM_63"]=-r63
            if vol is not None:
                out[f"{market}_VOLATILITY_63"]=vol
            if r5y is not None:
                out[f"{market}_FIVE_YEAR_RETURN_STRETCH"]=r5y

        cn_hk=cls._pair_corr(prices["CN"],prices["HK"],i,60)
        hk_us=cls._pair_corr(prices["HK"],prices["US"],i,60)
        cn_us=cls._pair_corr(prices["CN"],prices["US"],i,60)
        if cn_hk is not None:
            out["CORR_CN_HK_60"]=cn_hk
        if hk_us is not None:
            out["CORR_HK_US_60"]=hk_us
        if cn_us is not None:
            out["CORR_CN_US_60"]=cn_us
        if cn_hk is not None and hk_us is not None and cn_us is not None:
            out["HK_BRIDGE_DIFFERENTIAL_60"]=(cn_hk+hk_us)/2.0-cn_us
        if len(drawdowns)==3:
            out["CROSS_MARKET_STRESS_COUNT_10PCT"]=float(sum(1 for x in drawdowns.values() if x<=-0.10))
            out["HK_STRESS_AMPLIFICATION"]=max(
                0.0,
                ((drawdowns["US"]+drawdowns["CN"])/2.0)-drawdowns["HK"],
            )

        hy=cls._fred_value(fred.get("HY_OAS",[]),d)
        if hy is not None:
            out["HY_CREDIT_SPREAD_LEVEL"]=hy
        nfci=cls._fred_value(fred.get("NFCI",[]),d)
        if nfci is not None:
            out["FINANCIAL_CONDITIONS_NFCI"]=nfci
        curve=cls._fred_value(fred.get("YIELD_CURVE_10Y2Y",[]),d)
        if curve is not None:
            out["YIELD_CURVE_INVERSION"]=-curve
        return {k:float(v) for k,v in out.items() if math.isfinite(float(v))}

    @staticmethod
    def _date_index_at_or_before(dates:list[date],d:date)->int|None:
        i=bisect.bisect_right(dates,d)-1
        return i if i>=0 else None

    @classmethod
    def _controls(
        cls,
        dates:list[date],
        event_indexes:list[int],
    )->list[int]:
        excluded=set()
        for anchor in event_indexes:
            lo=max(0,anchor-CRASH_EXCLUSION_SESSIONS)
            hi=min(len(dates)-1,anchor+CRASH_EXCLUSION_SESSIONS)
            excluded.update(range(lo,hi+1))
        start=1260
        return [
            i for i in range(start,len(dates),CONTROL_STEP)
            if i not in excluded
        ]

    def run(self,force:bool=False)->dict:
        previous=self.latest()
        indexes={}
        errors={}
        for market,specs in MARKET_INDEXES.items():
            rows={}
            for label,symbol in specs.items():
                try:
                    rows[label]=LongCycleHypothesisExperiment._fetch_yahoo_full(symbol,timeout=30)
                except Exception as exc:
                    errors[f"{market}:{label}"]=f"{type(exc).__name__}:{exc}"
            indexes[market]=rows

        primary={}
        for market,label in PRIMARY_INDEX.items():
            row=(indexes.get(market) or {}).get(label)
            if row is None:
                raise RuntimeError(f"primary_index_unavailable:{market}:{label}:{errors}")
            primary[market]=row

        dates,prices=self._aligned(primary)
        if len(dates)<2000:
            raise RuntimeError(f"insufficient_common_history:{len(dates)}")

        as_of=dates[-1].isoformat()
        if (
            previous
            and previous.get("as_of")==as_of
            and previous.get("version")==self.version
            and not force
        ):
            return previous

        events_by_market={
            market:CrossMarketCrashExperiment._detect_crashes(series,-0.20,-0.05)
            for market,series in primary.items()
        }
        clusters=self._event_clusters(events_by_market)
        event_indexes=[]
        valid_clusters=[]
        for event in clusters:
            idx=self._date_index_at_or_before(dates,event["anchor_date"])
            if idx is None or idx<1260:
                continue
            event_indexes.append(idx)
            valid_clusters.append({**event,"anchor_index":idx})

        fred={}
        for name,series_id in {
            "HY_OAS":"BAMLH0A0HYM2",
            "NFCI":"NFCI",
            "YIELD_CURVE_10Y2Y":"T10Y2Y",
        }.items():
            try:
                fred[name]=self._fred_with_retry(series_id)
            except Exception as exc:
                errors[f"FRED:{name}"]=f"{type(exc).__name__}:{exc}"

        control_indexes=self._controls(dates,event_indexes)
        feature_cache={}
        def features_at(i:int)->dict:
            if i not in feature_cache:
                feature_cache[i]=self._features(dates,prices,i,fred)
            return feature_cache[i]

        # Expanding negative-control percentiles: no future controls can define an earlier alert.
        control_evaluations=[]
        control_history:dict[str,list[float]]={}
        for idx in control_indexes:
            feats=features_at(idx)
            pct={}
            for factor,value in feats.items():
                p=self._percentile(control_history.get(factor,[]),value)
                pct[factor]=p
                control_history.setdefault(factor,[]).append(value)
            control_evaluations.append({
                "date":dates[idx].isoformat(),
                "index":idx,
                "percentiles":pct,
            })

        event_evaluations=[]
        for event in valid_clusters:
            anchor_i=event["anchor_index"]
            leads={}
            for lead in LEADS:
                i=anchor_i-lead
                if i<1260:
                    continue
                feats=features_at(i)
                percentiles={}
                # Baseline only from ordinary control dates available BEFORE this cutoff.
                prior_controls=[c for c in control_evaluations if c["index"]<i]
                for factor,value in feats.items():
                    hist=[
                        feature_cache[c["index"]].get(factor)
                        for c in prior_controls
                        if feature_cache.get(c["index"],{}).get(factor) is not None
                    ]
                    percentiles[factor]=self._percentile(hist,value)
                leads[str(lead)]={
                    "cutoff_date":dates[i].isoformat(),
                    "features":feats,
                    "point_in_time_percentiles":percentiles,
                }
            event_evaluations.append({
                "event_id":event["event_id"],
                "anchor_date":event["anchor_date"].isoformat(),
                "markets":event["markets"],
                "market_count":event["market_count"],
                "max_drawdown":event["max_drawdown"],
                "leads":leads,
            })

        # False-positive rates from expanding ordinary controls.
        control_factor_stats={}
        all_factors=sorted({
            factor
            for row in control_evaluations
            for factor,p in row["percentiles"].items()
            if p is not None
        })
        for factor in all_factors:
            vals=[
                row["percentiles"].get(factor)
                for row in control_evaluations
                if row["percentiles"].get(factor) is not None
            ]
            control_factor_stats[factor]={
                "evaluated_controls":len(vals),
                "false_positive_rate_at_80pct":(
                    sum(1 for p in vals if p>=FACTOR_ALERT_PERCENTILE)/len(vals)
                    if vals else None
                ),
            }

        candidates=[]
        by_factor={}
        for lead in LEADS:
            for factor in all_factors:
                event_pct=[]
                for event in event_evaluations:
                    p=((event.get("leads") or {}).get(str(lead)) or {}).get("point_in_time_percentiles",{}).get(factor)
                    if p is not None:
                        event_pct.append(float(p))
                controls=control_factor_stats.get(factor) or {}
                fpr=controls.get("false_positive_rate_at_80pct")
                if not event_pct or fpr is None:
                    continue
                hit=sum(1 for p in event_pct if p>=FACTOR_ALERT_PERCENTILE)/len(event_pct)
                lift=hit-float(fpr)
                row={
                    "factor":factor,
                    "lead_trading_days":lead,
                    "event_samples":len(event_pct),
                    "event_hit_rate":hit,
                    "control_false_positive_rate":fpr,
                    "hit_rate_lift":lift,
                    "mean_event_percentile":mean(event_pct),
                    "candidate":(
                        len(event_pct)>=3
                        and hit>=0.50
                        and float(fpr)<=0.30
                        and lift>=0.20
                    ),
                }
                candidates.append(row)
                by_factor.setdefault(factor,[]).append(row)

        factor_summary=[]
        for factor,rows in by_factor.items():
            selected=[r for r in rows if r["candidate"]]
            long_lead=[r for r in selected if r["lead_trading_days"]>=120]
            factor_summary.append({
                "factor":factor,
                "candidate_leads":[r["lead_trading_days"] for r in selected],
                "long_lead_candidate":bool(long_lead),
                "best_lift":max(r["hit_rate_lift"] for r in rows),
                "best_lead_trading_days":max(rows,key=lambda r:r["hit_rate_lift"])["lead_trading_days"],
                "mean_event_percentile":mean(r["mean_event_percentile"] for r in rows),
                "passes_any_lead":bool(selected),
            })
        factor_summary.sort(
            key=lambda r:(r["long_lead_candidate"],r["passes_any_lead"],r["best_lift"],r["mean_event_percentile"]),
            reverse=True,
        )

        payload={
            "version":self.version,
            "protocol_version":self.protocol_version,
            "as_of":as_of,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "shadow_only":True,
            "applied_to_weights":False,
            "production_action":"NONE",
            "markets":["US","CN","HK"],
            "lead_trading_days":list(LEADS),
            "alert_percentile":FACTOR_ALERT_PERCENTILE,
            "crash_definition":"Automatic 20% peak-to-current drawdown clusters across US/CN/HK; event anchors use the first 20% breach in each <=180-day cluster.",
            "anti_hindsight":{
                "point_in_time_only":True,
                "event_cutoff_rule":"At each lead, features use only prices and macro observations available on or before the historical cutoff.",
                "percentile_baseline_rule":"Event percentiles use only ordinary control observations that occurred before the event cutoff.",
                "negative_controls":"Ordinary dates sampled every 63 common sessions and excluded within +/-250 common sessions of any crash anchor.",
                "promotion_rule":"No factor may affect production weights until repeated prospective evidence improves realizable net return after opportunity cost and switching cost.",
            },
            "event_count":len(event_evaluations),
            "control_count":len(control_evaluations),
            "events":event_evaluations,
            "control_factor_stats":control_factor_stats,
            "factor_lead_results":candidates,
            "factor_summary":factor_summary,
            "top_long_lead_candidates":[x for x in factor_summary if x["long_lead_candidate"]][:10],
            "top_any_lead_candidates":[x for x in factor_summary if x["passes_any_lead"]][:15],
            "data_completeness":{
                "primary_markets":len(primary),
                "common_sessions":len(dates),
                "fred_series":len(fred),
                "errors":errors,
            },
            "interpretation_guard":"Historical recurrence identifies candidate latent hazards, not causal proof or calibrated crash probability. Long-lead candidates are prioritized; short-lead-only signals are treated as confirmation rather than early warning.",
        }
        digest=self._hash(payload)
        payload["experiment_hash"]=digest
        payload["experiment_id"]=f"LATENT-HAZARD-{as_of}-{digest[:10]}"
        payload["previous_experiment_id"]=previous.get("experiment_id") if previous else None
        self.store.save_json(self.latest_file,payload)
        history=self.history(5000)
        if not any(x.get("experiment_id")==payload["experiment_id"] for x in history):
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
            "top_long_lead_candidates":(latest.get("top_long_lead_candidates") or [])[:5] if latest else [],
        }
