from __future__ import annotations

import bisect
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev

from .cross_market_crash import CrossMarketCrashExperiment, market_indexes, primary_indexes
from .long_cycle_hypothesis import LongCycleHypothesisExperiment
from .store import RunStore


LEADS=(20,60,120,250)
DATA_SOURCE_REVISION="market-provider-split@0.1.0"
FACTOR_ALERT_PERCENTILE=0.80
CONTROL_STEP=63
CRASH_EXCLUSION_SESSIONS=250
MARKET_EXPECTATION_YAHOO={
    "MOVE_INDEX":"^MOVE",
    "FED_FUNDS_FUTURE":"ZQ=F",
    "SOFR_1M_FUTURE":"SR1=F",
}
COMPOSITE_RULES={
    "RATES_POLICY_PRESSURE":{
        "factors":(
            "US_TREASURY_2Y_RISE_90D",
            "US_REAL_YIELD_10Y_RISE_90D",
            "MOVE_LEVEL",
            "FED_POLICY_RATE_LEVEL",
            "FED_FUNDS_FUTURES_REPRICING_ABS_30D",
        ),
        "min_hits":3,
    },
    "HK_RATES_EARLY_WARNING":{
        "factors":(
            "HK_NEGATIVE_MOMENTUM_63",
            "US_TREASURY_2Y_RISE_90D",
            "MOVE_LEVEL",
        ),
        "min_hits":2,
    },
    "POLICY_REPRICING_STRESS":{
        "factors":(
            "US_TREASURY_2Y_RISE_90D",
            "FED_FUNDS_FUTURES_REPRICING_ABS_30D",
            "SOFR_FUTURES_REPRICING_ABS_30D",
            "MOVE_RISE_30D",
        ),
        "min_hits":2,
    },
    "SYSTEMIC_TRANSMISSION":{
        "factors":(
            "CROSS_MARKET_STRESS_SHARE_10PCT",
            "CROSS_MARKET_CORR_MEAN_60",
            "CROSS_MARKET_STRESS_COUNT_10PCT",
            "CORR_HK_US_60",
            "MOVE_LEVEL",
        ),
        "min_hits":3,
    },
}


class LatentHazardExperiment:
    version="latent-hazard-discovery@0.5.0"
    protocol_version="point-in-time-hazard-protocol@0.5.0"
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
    def _fisher_right(event_hits:int,event_misses:int,control_hits:int,control_misses:int)->float|None:
        a=int(event_hits);b=int(event_misses);c=int(control_hits);d=int(control_misses)
        n1=a+b;n2=c+d;success=a+c;total=n1+n2
        if n1<=0 or n2<=0:
            return None
        denominator=math.comb(total,n1)
        if denominator<=0:
            return None
        p=0.0
        for x in range(a,min(n1,success)+1):
            failures_selected=n1-x
            if failures_selected<0 or failures_selected>(total-success):
                continue
            p+=(
                math.comb(success,x)
                *math.comb(total-success,failures_selected)
                /denominator
            )
        return min(1.0,max(0.0,p))

    @staticmethod
    def _bh_adjust(rows:list[dict],p_key:str="fisher_p_value",q_key:str="bh_q_value")->None:
        indexed=[
            (i,float(row[p_key]))
            for i,row in enumerate(rows)
            if row.get(p_key) is not None
        ]
        if not indexed:
            return
        ordered=sorted(indexed,key=lambda x:x[1])
        m=len(ordered)
        adjusted=[None]*len(rows)
        running=1.0
        for rank in range(m,0,-1):
            idx,p=ordered[rank-1]
            q=min(running,p*m/rank)
            running=q
            adjusted[idx]=min(1.0,max(0.0,q))
        for i,q in enumerate(adjusted):
            if q is not None:
                rows[i][q_key]=q

    @staticmethod
    def _leave_one_out_min_hit_rate(values:list[bool])->float|None:
        if len(values)<2:
            return None
        rates=[]
        for i in range(len(values)):
            subset=values[:i]+values[i+1:]
            rates.append(sum(1 for x in subset if x)/len(subset))
        return min(rates) if rates else None

    @staticmethod
    def _yahoo_observations(symbol:str,transform:str="identity")->list[tuple[date,float]]:
        series=LongCycleHypothesisExperiment._fetch_market_full(symbol,timeout=30)
        rows=[]
        for ts,price in zip(series["ts"],series["close"]):
            value=float(price)
            if transform=="imm_implied_rate":
                value=100.0-value
            if math.isfinite(value):
                rows.append((datetime.fromtimestamp(int(ts),timezone.utc).date(),value))
        if len(rows)<100:
            raise RuntimeError(f"insufficient_yahoo_observations:{symbol}:{len(rows)}")
        return rows

    @staticmethod
    def _composite_signals(percentiles:dict[str,float|None])->dict[str,dict]:
        out={}
        for name,spec in COMPOSITE_RULES.items():
            factors=tuple(spec["factors"])
            available=[f for f in factors if percentiles.get(f) is not None]
            hits=[f for f in available if float(percentiles[f])>=FACTOR_ALERT_PERCENTILE]
            min_hits=int(spec["min_hits"])
            out[name]={
                "available":len(available)>=min_hits,
                "available_count":len(available),
                "factor_count":len(factors),
                "alert_count":len(hits),
                "min_hits":min_hits,
                "score":(len(hits)/len(available) if available else None),
                "triggered":(len(available)>=min_hits and len(hits)>=min_hits),
                "triggered_factors":hits,
                "factors":list(factors),
            }
        return out

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
    def _fred_prior_value(cls,rows:list[tuple[date,float]],d:date,calendar_days:int)->float|None:
        return cls._fred_value(rows,d-timedelta(days=int(calendar_days)))

    @classmethod
    def _fred_change(cls,rows:list[tuple[date,float]],d:date,calendar_days:int)->float|None:
        current=cls._fred_value(rows,d)
        prior=cls._fred_prior_value(rows,d,calendar_days)
        if current is None or prior is None:
            return None
        return float(current)-float(prior)

    @classmethod
    def _fred_pct_change(cls,rows:list[tuple[date,float]],d:date,calendar_days:int)->float|None:
        current=cls._fred_value(rows,d)
        prior=cls._fred_prior_value(rows,d,calendar_days)
        if current is None or prior is None or abs(float(prior))<1e-12:
            return None
        return float(current)/float(prior)-1.0

    @staticmethod
    def _fred_window_min(rows:list[tuple[date,float]],d:date,calendar_days:int)->float|None:
        start=d-timedelta(days=int(calendar_days))
        vals=[float(v) for rd,v in rows if start<=rd<=d]
        return min(vals) if vals else None

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
        markets=sorted(prices)
        for market in markets:
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

        pair_corrs={}
        for left_index,left in enumerate(markets):
            for right in markets[left_index+1:]:
                corr=cls._pair_corr(prices[left],prices[right],i,60)
                if corr is None:
                    continue
                pair_corrs[(left,right)]=corr
                out[f"CORR_{left}_{right}_60"]=corr
                out[f"CORR_{right}_{left}_60"]=corr
        if pair_corrs:
            out["CROSS_MARKET_CORR_MEAN_60"]=sum(pair_corrs.values())/len(pair_corrs)
            out["CROSS_MARKET_CORR_MAX_60"]=max(pair_corrs.values())

        cn_hk=pair_corrs.get(tuple(sorted(("CN","HK"))))
        hk_us=pair_corrs.get(tuple(sorted(("HK","US"))))
        cn_us=pair_corrs.get(tuple(sorted(("CN","US"))))
        if cn_hk is not None and hk_us is not None and cn_us is not None:
            out["HK_BRIDGE_DIFFERENTIAL_60"]=(cn_hk+hk_us)/2.0-cn_us

        if len(drawdowns)>=2:
            stressed=sum(1 for x in drawdowns.values() if x<=-0.10)
            out["CROSS_MARKET_STRESS_COUNT_10PCT"]=float(stressed)
            out["CROSS_MARKET_STRESS_SHARE_10PCT"]=float(stressed)/len(drawdowns)
        if {"US","CN","HK"}<=set(drawdowns):
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

        curve3m=cls._fred_value(fred.get("YIELD_CURVE_10Y3M",[]),d)
        if curve3m is not None:
            out["YIELD_CURVE_10Y3M_INVERSION"]=-curve3m

        dgs2=cls._fred_value(fred.get("TREASURY_2Y",[]),d)
        dgs10=cls._fred_value(fred.get("TREASURY_10Y",[]),d)
        dgs30=cls._fred_value(fred.get("TREASURY_30Y",[]),d)
        real10=cls._fred_value(fred.get("REAL_YIELD_10Y",[]),d)
        policy=cls._fred_value(fred.get("FED_FUNDS_DAILY",[]),d)
        walcl=cls._fred_value(fred.get("FED_BALANCE_SHEET",[]),d)

        if dgs2 is not None:
            out["US_TREASURY_2Y_LEVEL"]=dgs2
        if dgs10 is not None:
            out["US_TREASURY_10Y_LEVEL"]=dgs10
        if dgs30 is not None:
            out["US_TREASURY_30Y_LEVEL"]=dgs30
        if real10 is not None:
            out["US_REAL_YIELD_10Y_LEVEL"]=real10
        if policy is not None:
            out["FED_POLICY_RATE_LEVEL"]=policy

        for factor,rows in (
            ("US_TREASURY_2Y",fred.get("TREASURY_2Y",[])),
            ("US_TREASURY_10Y",fred.get("TREASURY_10Y",[])),
            ("US_REAL_YIELD_10Y",fred.get("REAL_YIELD_10Y",[])),
        ):
            change=cls._fred_change(rows,d,90)
            if change is not None:
                out[f"{factor}_RISE_90D"]=change

        policy_change=cls._fred_change(fred.get("FED_FUNDS_DAILY",[]),d,90)
        if policy_change is not None:
            out["FED_POLICY_TIGHTENING_90D"]=policy_change
            out["FED_POLICY_EASING_90D"]=-policy_change

        repricing=cls._fred_change(fred.get("TREASURY_2Y",[]),d,30)
        if repricing is not None:
            out["US_POLICY_REPRICING_PROXY_2Y_ABS_30D"]=abs(repricing)

        for name,rows in (
            ("10Y2Y",fred.get("YIELD_CURVE_10Y2Y",[])),
            ("10Y3M",fred.get("YIELD_CURVE_10Y3M",[])),
        ):
            current=cls._fred_value(rows,d)
            window_min=cls._fred_window_min(rows,d,180)
            if current is not None and window_min is not None:
                out[f"YIELD_CURVE_{name}_RESTEEPENING_180D"]=(
                    float(current)-float(window_min)
                    if float(window_min)<0.0
                    else 0.0
                )

        walcl_change=cls._fred_pct_change(fred.get("FED_BALANCE_SHEET",[]),d,180)
        if walcl is not None:
            out["FED_BALANCE_SHEET_LEVEL"]=walcl
        if walcl_change is not None:
            out["FED_BALANCE_SHEET_CONTRACTION_180D"]=-walcl_change
            out["FED_BALANCE_SHEET_EXPANSION_180D"]=walcl_change

        move=cls._fred_value(fred.get("MOVE_INDEX",[]),d)
        if move is not None:
            out["MOVE_LEVEL"]=move
            move30=cls._fred_change(fred.get("MOVE_INDEX",[]),d,30)
            move90=cls._fred_change(fred.get("MOVE_INDEX",[]),d,90)
            if move30 is not None:
                out["MOVE_RISE_30D"]=move30
            if move90 is not None:
                out["MOVE_RISE_90D"]=move90

        zq=cls._fred_value(fred.get("FED_FUNDS_FUTURE",[]),d)
        if zq is not None:
            out["FED_FUNDS_FUTURES_IMPLIED_RATE"]=zq
            zq30=cls._fred_change(fred.get("FED_FUNDS_FUTURE",[]),d,30)
            zq90=cls._fred_change(fred.get("FED_FUNDS_FUTURE",[]),d,90)
            if zq30 is not None:
                out["FED_FUNDS_FUTURES_REPRICING_ABS_30D"]=abs(zq30)
                out["FED_FUNDS_FUTURES_TIGHTENING_30D"]=zq30
            if zq90 is not None:
                out["FED_FUNDS_FUTURES_REPRICING_ABS_90D"]=abs(zq90)

        sr1=cls._fred_value(fred.get("SOFR_1M_FUTURE",[]),d)
        if sr1 is not None:
            out["SOFR_FUTURES_IMPLIED_RATE"]=sr1
            sr30=cls._fred_change(fred.get("SOFR_1M_FUTURE",[]),d,30)
            if sr30 is not None:
                out["SOFR_FUTURES_REPRICING_ABS_30D"]=abs(sr30)
                out["SOFR_FUTURES_TIGHTENING_30D"]=sr30

        if zq is not None and policy is not None:
            out["FED_FUNDS_FUTURES_GAP_VS_DFF"]=abs(float(zq)-float(policy))
        if zq is not None and sr1 is not None:
            out["SOFR_FED_FUNDS_FUTURES_BASIS_ABS"]=abs(float(sr1)-float(zq))

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
        registered_indexes=market_indexes()
        registered_primary=primary_indexes()
        for market,specs in registered_indexes.items():
            rows={}
            for label,symbol in specs.items():
                try:
                    rows[label]=LongCycleHypothesisExperiment._fetch_market_full(symbol,timeout=30)
                except Exception as exc:
                    errors[f"{market}:{label}"]=f"{type(exc).__name__}:{exc}"
            indexes[market]=rows

        primary={}
        for market,label in registered_primary.items():
            row=(indexes.get(market) or {}).get(label)
            if row is None:
                errors[f"{market}:{label}:PRIMARY"]="primary_index_unavailable"
                continue
            primary[market]=row
        if len(primary)<2:
            raise RuntimeError(f"insufficient_primary_markets:{sorted(primary)}:{errors}")

        dates,prices=self._aligned(primary)
        if len(dates)<2000:
            raise RuntimeError(f"insufficient_common_history:{len(dates)}")

        as_of=dates[-1].isoformat()
        if (
            previous
            and previous.get("as_of")==as_of
            and previous.get("version")==self.version
            and previous.get("data_source_revision")==DATA_SOURCE_REVISION
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
            "YIELD_CURVE_10Y3M":"T10Y3M",
            "TREASURY_2Y":"DGS2",
            "TREASURY_10Y":"DGS10",
            "TREASURY_30Y":"DGS30",
            "REAL_YIELD_10Y":"DFII10",
            "FED_FUNDS_DAILY":"DFF",
            "FED_BALANCE_SHEET":"WALCL",
        }.items():
            try:
                fred[name]=self._fred_with_retry(series_id)
            except Exception as exc:
                errors[f"FRED:{name}"]=f"{type(exc).__name__}:{exc}"

        for name,symbol in MARKET_EXPECTATION_YAHOO.items():
            try:
                transform="imm_implied_rate" if name in {"FED_FUNDS_FUTURE","SOFR_1M_FUTURE"} else "identity"
                fred[name]=self._yahoo_observations(symbol,transform)
            except Exception as exc:
                errors[f"YAHOO:{name}"]=f"{type(exc).__name__}:{exc}"

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
                "composites":self._composite_signals(pct),
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
                    "composites":self._composite_signals(percentiles),
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
                event_flags=[p>=FACTOR_ALERT_PERCENTILE for p in event_pct]
                control_pct=[
                    row["percentiles"].get(factor)
                    for row in control_evaluations
                    if row["percentiles"].get(factor) is not None
                ]
                control_flags=[float(p)>=FACTOR_ALERT_PERCENTILE for p in control_pct]
                event_hits=sum(1 for x in event_flags if x)
                control_hits=sum(1 for x in control_flags if x)
                hit=event_hits/len(event_flags)
                fpr_exact=control_hits/len(control_flags) if control_flags else float(fpr)
                lift=hit-fpr_exact
                fisher=self._fisher_right(
                    event_hits,len(event_flags)-event_hits,
                    control_hits,len(control_flags)-control_hits,
                )
                row={
                    "factor":factor,
                    "lead_trading_days":lead,
                    "event_samples":len(event_pct),
                    "control_samples":len(control_flags),
                    "event_hits":event_hits,
                    "control_hits":control_hits,
                    "event_hit_rate":hit,
                    "control_false_positive_rate":fpr_exact,
                    "hit_rate_lift":lift,
                    "mean_event_percentile":mean(event_pct),
                    "leave_one_event_out_min_hit_rate":self._leave_one_out_min_hit_rate(event_flags),
                    "fisher_p_value":fisher,
                    "candidate":(
                        len(event_pct)>=3
                        and hit>=0.50
                        and fpr_exact<=0.30
                        and lift>=0.20
                    ),
                }
                candidates.append(row)
                by_factor.setdefault(factor,[]).append(row)

        self._bh_adjust(candidates)
        for row in candidates:
            q=row.get("bh_q_value")
            row["statistically_supported"]=bool(
                row.get("candidate")
                and q is not None
                and float(q)<=0.10
                and (row.get("leave_one_event_out_min_hit_rate") or 0.0)>=0.50
            )

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
                "statistically_supported_leads":[
                    r["lead_trading_days"] for r in rows if r.get("statistically_supported")
                ],
            })
        factor_summary.sort(
            key=lambda r:(r["long_lead_candidate"],r["passes_any_lead"],r["best_lift"],r["mean_event_percentile"]),
            reverse=True,
        )


        composite_lead_results=[]
        for lead in LEADS:
            for name,spec in COMPOSITE_RULES.items():
                event_rows=[]
                for event in event_evaluations:
                    comp=((event.get("leads") or {}).get(str(lead)) or {}).get("composites",{}).get(name)
                    if comp and comp.get("available"):
                        event_rows.append(bool(comp.get("triggered")))
                control_rows=[]
                for row in control_evaluations:
                    comp=(row.get("composites") or {}).get(name)
                    if comp and comp.get("available"):
                        control_rows.append(bool(comp.get("triggered")))
                if not event_rows or not control_rows:
                    continue
                event_hits=sum(1 for x in event_rows if x)
                control_hits=sum(1 for x in control_rows if x)
                hit=event_hits/len(event_rows)
                fpr=control_hits/len(control_rows)
                lift=hit-fpr
                fisher=self._fisher_right(
                    event_hits,len(event_rows)-event_hits,
                    control_hits,len(control_rows)-control_hits,
                )
                composite_lead_results.append({
                    "composite":name,
                    "lead_trading_days":lead,
                    "event_samples":len(event_rows),
                    "control_samples":len(control_rows),
                    "event_hits":event_hits,
                    "control_hits":control_hits,
                    "event_hit_rate":hit,
                    "control_false_positive_rate":fpr,
                    "hit_rate_lift":lift,
                    "leave_one_event_out_min_hit_rate":self._leave_one_out_min_hit_rate(event_rows),
                    "fisher_p_value":fisher,
                    "candidate":(
                        len(event_rows)>=3
                        and hit>=0.50
                        and fpr<=0.30
                        and lift>=0.20
                    ),
                    "rule":{
                        "factors":list(spec["factors"]),
                        "min_hits":int(spec["min_hits"]),
                        "factor_alert_percentile":FACTOR_ALERT_PERCENTILE,
                    },
                })
        self._bh_adjust(composite_lead_results)
        for row in composite_lead_results:
            q=row.get("bh_q_value")
            row["statistically_supported"]=bool(
                row.get("candidate")
                and q is not None
                and float(q)<=0.10
                and (row.get("leave_one_event_out_min_hit_rate") or 0.0)>=0.50
            )

        composite_summary=[]
        for name in COMPOSITE_RULES:
            rows=[r for r in composite_lead_results if r["composite"]==name]
            if not rows:
                continue
            selected=[r for r in rows if r["candidate"]]
            long_selected=[r for r in selected if r["lead_trading_days"]>=120]
            best=max(rows,key=lambda r:r["hit_rate_lift"])
            composite_summary.append({
                "composite":name,
                "candidate_leads":[r["lead_trading_days"] for r in selected],
                "long_lead_candidate":bool(long_selected),
                "passes_any_lead":bool(selected),
                "best_lift":best["hit_rate_lift"],
                "best_lead_trading_days":best["lead_trading_days"],
                "rule":best["rule"],
                "statistically_supported_leads":[
                    r["lead_trading_days"] for r in rows if r.get("statistically_supported")
                ],
            })
        composite_summary.sort(
            key=lambda r:(r["long_lead_candidate"],r["passes_any_lead"],r["best_lift"]),
            reverse=True,
        )

        current_i=len(dates)-1
        current_features=features_at(current_i)
        current_percentiles={}
        for factor,value in current_features.items():
            hist=[
                feature_cache[row["index"]].get(factor)
                for row in control_evaluations
                if row["index"]<current_i
                and feature_cache.get(row["index"],{}).get(factor) is not None
            ]
            current_percentiles[factor]=self._percentile(hist,value)
        current_composites=self._composite_signals(current_percentiles)
        supported_rules={
            row["composite"]:row
            for row in composite_lead_results
            if row.get("statistically_supported")
        }
        for name,state in current_composites.items():
            state["historically_statistically_supported"]=name in supported_rules
            state["historical_support_rows"]=[
                row for row in composite_lead_results
                if row.get("composite")==name and row.get("statistically_supported")
            ]
        supported_triggered=[
            name for name,state in current_composites.items()
            if state.get("triggered") and state.get("historically_statistically_supported")
        ]
        current_state={
            "as_of":dates[current_i].isoformat(),
            "features":current_features,
            "point_in_time_percentiles":current_percentiles,
            "composites":current_composites,
            "statistically_supported_composites_triggered":supported_triggered,
            "supported_trigger_count":len(supported_triggered),
            "state_label":(
                "SUPPORTED_HAZARD_ACTIVE"
                if supported_triggered
                else "NO_SUPPORTED_HAZARD_TRIGGER"
            ),
            "production_action":"NONE",
            "shadow_only":True,
        }

        payload={
            "version":self.version,
            "protocol_version":self.protocol_version,
            "data_source_revision":DATA_SOURCE_REVISION,
            "as_of":as_of,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "shadow_only":True,
            "applied_to_weights":False,
            "production_action":"NONE",
            "markets":sorted(primary),
            "lead_trading_days":list(LEADS),
            "alert_percentile":FACTOR_ALERT_PERCENTILE,
            "crash_definition":"Automatic 20% peak-to-current drawdown clusters across all available registered primary markets; event anchors use the first 20% breach in each <=180-day cluster.",
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
            "composite_lead_results":composite_lead_results,
            "composite_summary":composite_summary,
            "top_long_lead_composites":[x for x in composite_summary if x["long_lead_candidate"]][:10],
            "top_any_lead_composites":[x for x in composite_summary if x["passes_any_lead"]][:10],
            "statistically_supported_factor_rows":[x for x in candidates if x.get("statistically_supported")],
            "statistically_supported_composite_rows":[x for x in composite_lead_results if x.get("statistically_supported")],
            "current_state":current_state,
            "data_completeness":{
                "primary_markets":len(primary),
                "primary_market_ids":sorted(primary),
                "unavailable_primary_market_ids":sorted(set(registered_primary)-set(primary)),
                "common_sessions":len(dates),
                "fred_and_market_expectation_series":len(fred),
                "fred_series_requested":10,
                "market_expectation_series_requested":len(MARKET_EXPECTATION_YAHOO),
                "errors":errors,
            },
            "rates_policy_layer":{
                "enabled":True,
                "series":[
                    "DGS2","DGS10","DGS30","DFII10","T10Y2Y","T10Y3M","DFF","WALCL",
                    "^MOVE","ZQ=F","SR1=F"
                ],
                "policy_repricing_proxy":"Uses both 2Y Treasury repricing and nearby continuous 30-Day Fed Funds / 1-Month SOFR futures implied rates. Futures are research proxies from continuous Yahoo series, not a full contract-by-contract OIS curve.",
                "key_derived_factors":[
                    "US_REAL_YIELD_10Y_RISE_90D",
                    "US_POLICY_REPRICING_PROXY_2Y_ABS_30D",
                    "YIELD_CURVE_10Y2Y_RESTEEPENING_180D",
                    "YIELD_CURVE_10Y3M_RESTEEPENING_180D",
                    "FED_POLICY_TIGHTENING_90D",
                    "FED_POLICY_EASING_90D",
                    "FED_BALANCE_SHEET_CONTRACTION_180D"
                ],
            },
            "statistical_guard":{
                "fisher_exact":"One-sided Fisher exact test compares crash-cutoff alert frequency with ordinary-control alert frequency.",
                "multiple_testing":"Benjamini-Hochberg q-values are computed separately across single-factor lead tests and composite lead tests.",
                "support_threshold":"q <= 0.10 plus leave-one-event-out minimum hit rate >= 0.50.",
                "small_sample_warning":"Only a small number of historical crash clusters have complete long-history inputs. Statistical support is screening evidence, not a calibrated crash probability.",
            },
            "interpretation_guard":"Historical recurrence identifies candidate latent hazards, not causal proof or calibrated crash probability. Rates, policy, MOVE and nearby futures variables are evaluated point-in-time and may change sign by regime. Continuous futures can contain roll effects and are treated as research proxies. Composite signals require multiple independent stress dimensions and remain shadow-only.",
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
