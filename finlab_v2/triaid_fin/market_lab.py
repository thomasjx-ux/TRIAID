from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, pstdev

from .contracts import BilingualText, MarketSnapshot, StrategyState
from .market_data import MarketDataError, get_market_data_hub, session_phase
from .cn_incubator import CN_SHADOW_IDS, positions as cn_shadow_positions
from .strategy_registry import POLICY_IDS, strategy_ids_for_market


@dataclass(frozen=True)
class MarketSpec:
    market_id:str
    benchmark:str
    assets:tuple[str,...]
    risk_assets:tuple[str,...]
    defensive_assets:tuple[str,...]
    currency:str
    reference_capital:float
    base_cost_bps:float
    impact_coefficient_bps:float
    max_participation_adv:float


MARKETS={
    "US":MarketSpec(
        "US","SPY",("SPY","QQQ","IWM","TLT","GLD"),("SPY","QQQ","IWM"),("TLT","GLD"),
        "USD",10_000_000.0,1.5,45.0,0.03,
    ),
    "CN":MarketSpec(
        "CN","510300.SS",("510300.SS","510500.SS","159915.SZ","512100.SS","511010.SS"),
        ("510300.SS","510500.SS","159915.SZ","512100.SS"),("511010.SS",),
        "CNY",50_000_000.0,2.5,60.0,0.02,
    ),
}


@dataclass
class Series:
    symbol:str
    ts:list[int]
    close:list[float]
    volume:list[float]


@dataclass
class MarketPanel:
    spec:MarketSpec
    ts:list[int]
    close:dict[str,list[float]]
    volume:dict[str,list[float]]
    data_mode:str="DAILY"
    provider:str="unknown"
    quality:str="unknown"

    @property
    def assets(self)->list[str]:
        return [a for a in self.spec.assets if a in self.close]


def fetch_yahoo(symbol:str,range_:str="10y",interval:str="1d",timeout:int=20)->Series:
    """Compatibility wrapper. New code should use MarketDataHub."""
    hub=get_market_data_hub()
    provider=hub.provider
    include_prepost=interval!="1d"
    min_points=300 if interval=="1d" else 2
    s=provider.fetch_series(
        symbol,
        range_=range_,
        interval=interval,
        include_prepost=include_prepost,
        min_points=min_points,
        timeout=timeout,
    )
    return Series(s.symbol,s.ts,s.close,s.volume)


def fetch_panel(market_id:str,mode:str="DAILY",force:bool=False)->MarketPanel:
    key=market_id.upper()
    if key not in MARKETS:
        raise MarketDataError(f"unsupported_market:{market_id}")
    spec=MARKETS[key]
    raw=get_market_data_hub().refresh_panel(
        key,
        spec.assets,
        spec.benchmark,
        mode,
        force=force,
    )
    return MarketPanel(
        spec=spec,
        ts=list(raw.ts),
        close={k:list(v) for k,v in raw.close.items()},
        volume={k:list(v) for k,v in raw.volume.items()},
        data_mode=raw.mode,
        provider=raw.provider,
        quality=raw.quality,
    )


def refresh_market_data(market_id:str,mode:str)->dict:
    panel=fetch_panel(market_id,mode,force=True)
    raw=get_market_data_hub().cached_panel(market_id,mode)
    return raw.metadata() if raw else {
        "market_id":market_id.upper(),
        "mode":mode.upper(),
        "points":len(panel.ts),
        "symbols":panel.assets,
    }


def market_data_snapshot(market_id:str,mode:str,refresh:bool=False)->dict:
    key=market_id.upper();mode=mode.upper()
    if refresh:
        refresh_market_data(key,mode)
    raw=get_market_data_hub().cached_panel(key,mode)
    if raw is None:
        refresh_market_data(key,mode)
        raw=get_market_data_hub().cached_panel(key,mode)
    if raw is None:
        raise MarketDataError(f"snapshot_unavailable:{key}:{mode}")
    latest={
        symbol:{
            "close":raw.close[symbol][-1],
            "volume":raw.volume[symbol][-1],
        }
        for symbol in sorted(raw.close)
    }
    return {
        **raw.metadata(),
        "session_phase":session_phase(key),
        "latest":latest,
    }


def market_data_status()->dict:
    return get_market_data_hub().status()


def market_data_capabilities(market_id:str|None=None)->dict:
    return get_market_data_hub().capabilities(market_id)


def market_data_product_capabilities(market_id:str|None=None)->dict:
    return get_market_data_hub().product_capabilities(market_id)


def market_data_provider_status()->dict:
    return get_market_data_hub().provider_status()


def market_data_latest_quotes(market_id:str,symbols:list[str]|tuple[str,...])->dict:
    return get_market_data_hub().latest_quotes(market_id,symbols)


def market_data_instrument_series(market_id:str,symbol:str,mode:str="DAILY")->dict:
    return get_market_data_hub().instrument_series(market_id,symbol,mode)



def _ret(xs:list[float])->list[float]:
    out=[0.0]*len(xs)
    for i in range(1,len(xs)):
        out[i]=xs[i]/xs[i-1]-1.0 if xs[i-1] else 0.0
    return out


def _sma(xs:list[float],h:int,i:int):
    if i+1<h:return None
    return sum(xs[i+1-h:i+1])/h


def _mom(xs:list[float],i:int,h:int):
    if i<h or xs[i-h]<=0:return None
    return xs[i]/xs[i-h]-1.0


def _vol(rs:list[float],i:int,h:int,annual:int=252):
    if i+1<h:return None
    sample=rs[i+1-h:i+1]
    return pstdev(sample)*math.sqrt(annual) if len(sample)>1 else 0.0


def _drawdown(xs:list[float],i:int,h:int=252):
    lo=max(0,i+1-h)
    peak=max(xs[lo:i+1])
    return xs[i]/peak-1.0 if peak else 0.0


def _clip(x:float,a:float=0.0,b:float=1.0)->float:
    return max(a,min(b,x))


def _one(assets:list[str],asset:str|None,w:float=1.0)->list[float]:
    return [float(w) if asset is not None and a==asset else 0.0 for a in assets]


def _vec(assets:list[str],weights:dict[str,float])->list[float]:
    return [max(0.0,float(weights.get(a,0.0))) for a in assets]


def _best_momentum(panel:MarketPanel,candidates:list[str],i:int,h:int):
    vals=[]
    for asset in candidates:
        if asset not in panel.close:continue
        score=_mom(panel.close[asset],i,h)
        if score is not None:vals.append((score,asset))
    return max(vals) if vals else (None,None)


def policy_positions(panel:MarketPanel,i:int)->dict[str,list[float]]:
    spec=panel.spec
    assets=panel.assets
    b=spec.benchmark
    risk=[a for a in spec.risk_assets if a in panel.close] or [b]
    defensive=[a for a in spec.defensive_assets if a in panel.close]
    returns={a:_ret(panel.close[a]) for a in assets}
    p=panel.close[b][i]
    br=returns[b]
    v20=_vol(br,i,20) or 0.0
    dd=_drawdown(panel.close[b],i,252)
    s20=_sma(panel.close[b],20,i);s50=_sma(panel.close[b],50,i)
    s100=_sma(panel.close[b],100,i);s200=_sma(panel.close[b],200,i)
    mom5=_mom(panel.close[b],i,5) or 0.0
    mom20=_mom(panel.close[b],i,20) or 0.0
    mom63=_mom(panel.close[b],i,63) or 0.0
    mom126=_mom(panel.close[b],i,126) or 0.0
    mom252=_mom(panel.close[b],i,252) or 0.0
    shock=br[i] if i>=1 else 0.0
    out={}
    bench=lambda w:_one(assets,b,_clip(w))
    out["P00_BUY_HOLD"]=bench(1.0)
    out["P01_VOL10"]=bench(_clip(0.10/v20) if v20>1e-9 else 1.0)
    out["P02_VOL15"]=bench(_clip(0.15/v20) if v20>1e-9 else 1.0)
    out["P03_DD_GUARD"]=bench(0.20 if dd<-0.12 else (0.55 if dd<-0.06 else 1.0))
    out["P04_TREND50"]=bench(1.0 if s50 is None or p>=s50 else 0.25)
    out["P05_TREND200"]=bench(1.0 if s200 is None or p>=s200 else 0.0)
    out["P06_DUAL_TREND"]=bench(1.0 if s20 is None or s100 is None or s20>=s100 else 0.20)
    out["P07_MOM63"]=bench(1.0 if mom63>=0 else 0.15)
    stress=int(v20>0.28)+int(dd<-0.08)+int(s50 is not None and p<s50)
    out["P08_STRESS_BLEND"]=bench({0:1.0,1:0.65,2:0.30,3:0.0}[stress])
    out["P09_SHOCK_GUARD"]=bench(0.15 if shock<-0.03 else (0.55 if v20>0.24 else 1.0))
    out["P10_VOL20"]=bench(_clip(0.20/v20) if v20>1e-9 else 1.0)
    out["P11_TREND20"]=bench(1.0 if s20 is None or p>=s20 else 0.20)
    out["P12_TREND100"]=bench(1.0 if s100 is None or p>=s100 else 0.15)
    out["P13_MOM20"]=bench(1.0 if mom20>=0 else 0.20)
    out["P14_MOM126"]=bench(1.0 if mom126>=0 else 0.10)
    out["P15_MOM252"]=bench(1.0 if mom252>=0 else 0.05)
    long_ok=(s200 is None or p>=s200)
    out["P16_REV5"]=bench(1.0 if long_ok and mom5<-0.015 else (0.35 if long_ok else 0.0))
    out["P17_REV20"]=bench(1.0 if long_ok and mom20<-0.04 else (0.35 if long_ok else 0.0))

    for pid,h in (("P18_XMOM20",20),("P19_XMOM63",63),("P20_XMOM126",126),("P21_XMOM252",252)):
        score,asset=_best_momentum(panel,risk,i,h)
        if asset and score is not None and score>0:
            out[pid]=_one(assets,asset)
        else:
            _,d=_best_momentum(panel,defensive,i,min(h,63))
            out[pid]=_one(assets,d) if d else [0.0]*len(assets)

    low=[]
    for asset in risk:
        s=_sma(panel.close[asset],100,i)
        va=_vol(returns[asset],i,63)
        if s is not None and panel.close[asset][i]>=s and va is not None:
            low.append((va,asset))
    if low:
        _,asset=min(low);out["P22_LOWVOL63"]=_one(assets,asset)
    else:
        _,d=_best_momentum(panel,defensive,i,63);out["P22_LOWVOL63"]=_one(assets,d) if d else [0.0]*len(assets)

    trend=[]
    for asset in risk:
        s=_sma(panel.close[asset],100,i)
        m63=_mom(panel.close[asset],i,63)
        m126=_mom(panel.close[asset],i,126)
        if s is not None and m63 is not None and panel.close[asset][i]>=s:
            trend.append((m63+0.5*(m126 or 0.0),asset))
    if trend and max(trend)[0]>0:
        _,asset=max(trend);out["P23_TREND_ROT"]=_one(assets,asset)
    else:
        _,d=_best_momentum(panel,defensive,i,63);out["P23_TREND_ROT"]=_one(assets,d) if d else [0.0]*len(assets)

    if (s100 is None or p>=s100) and mom63>=0:
        out["P24_DEFENSIVE_ROT"]=bench(1.0)
    else:
        _,d=_best_momentum(panel,defensive,i,63);out["P24_DEFENSIVE_ROT"]=_one(assets,d) if d else [0.0]*len(assets)

    balanced=[0.0]*len(assets)
    if b in assets:
        balanced[assets.index(b)]=0.70 if spec.market_id=="CN" else 0.60
    if defensive:
        remaining=1.0-sum(balanced)
        for asset in defensive:
            balanced[assets.index(asset)]+=remaining/len(defensive)
    out["P25_BALANCED"]=balanced

    inv=[]
    for asset in assets:
        va=_vol(returns[asset],i,63)
        if va is not None and va>1e-6:
            inv.append((asset,1.0/va))
    if inv:
        den=sum(v for _,v in inv)
        vals={a:v/den for a,v in inv}
        capped={a:min(0.50,w) for a,w in vals.items()}
        total=sum(capped.values())
        if total>1.0:
            capped={a:w/total for a,w in capped.items()}
        out["P26_INVOL_BAL"]=_vec(assets,capped)
    else:
        out["P26_INVOL_BAL"]=[0.0]*len(assets)

    healthy=[]
    for asset in risk:
        s=_sma(panel.close[asset],50,i)
        if s is not None and panel.close[asset][i]>=s:
            healthy.append(asset)
    breadth=len(healthy)/max(1,len(risk))
    if breadth>=0.60:
        scored=[]
        for asset in healthy:
            m=_mom(panel.close[asset],i,63)
            if m is not None:scored.append((m,asset))
        winners=[a for _,a in sorted(scored,reverse=True)[:2]]
        out["P27_BREADTH_ROT"]=_vec(assets,{a:1.0/len(winners) for a in winners}) if winners else bench(0.5)
    else:
        _,d=_best_momentum(panel,defensive,i,63);out["P27_BREADTH_ROT"]=_one(assets,d) if d else [0.0]*len(assets)
    out["P28_CASH"]=[0.0]*len(assets)
    return out


def _adv(panel:MarketPanel,asset:str,i:int,lookback:int=20)->float:
    lo=max(0,i+1-lookback)
    vals=[p*v for p,v in zip(panel.close[asset][lo:i+1],panel.volume.get(asset,[0.0]*len(panel.ts))[lo:i+1]) if p>0 and v>0]
    return mean(vals) if vals else 0.0


def _trade_cost(prev:list[float],new:list[float],panel:MarketPanel,i:int)->tuple[float,float]:
    spec=panel.spec
    cost=0.0;turn=0.0
    for asset,old,target in zip(panel.assets,prev,new):
        d=abs(target-old);turn+=d
        if d<=1e-15:continue
        adv=_adv(panel,asset,i)
        participation=(spec.reference_capital*d/adv) if adv>0 else 0.0
        impact=spec.impact_coefficient_bps*math.sqrt(max(0.0,participation))
        cost+=d*(spec.base_cost_bps+impact)/10000.0
    return cost,turn


def policy_return_history(panel:MarketPanel)->dict[str,list[float]]:
    n=len(panel.ts);assets=panel.assets
    asset_returns={a:_ret(panel.close[a]) for a in assets}
    ids=strategy_ids_for_market(panel.spec.market_id)
    out={pid:[0.0]*n for pid in ids}
    prev={pid:[0.0]*len(assets) for pid in ids}
    for i in range(n-1):
        positions=policy_positions(panel,i)
        if panel.spec.market_id=="CN":
            positions.update(cn_shadow_positions(panel,i))
        next_returns=[asset_returns[a][i+1] for a in assets]
        for pid in ids:
            pos=positions[pid]
            gross=sum(w*r for w,r in zip(pos,next_returns))
            cost,_=_trade_cost(prev[pid],pos,panel,i)
            out[pid][i+1]=gross-cost
            prev[pid]=pos
    return out


def _max_drawdown(returns:list[float])->float:
    equity=1.0;peak=1.0;worst=0.0
    for r in returns:
        equity*=1.0+r
        peak=max(peak,equity)
        worst=min(worst,equity/peak-1.0)
    return worst


def _annualized_mean(xs:list[float])->float:
    return mean(xs)*252 if xs else 0.0


def build_strategy_states(
    panel:MarketPanel,
    history:dict[str,list[float]],
    window_weights:tuple[float,float,float,float]|None=None,
)->list[StrategyState]:
    windows=(21,63,126,252)
    weights=window_weights or (0.35,0.30,0.20,0.15)
    total=sum(float(x) for x in weights)
    if total<=0:
        weights=(0.35,0.30,0.20,0.15)
    else:
        weights=tuple(float(x)/total for x in weights)
    states=[]
    for pid,rs in history.items():
        weighted=[];used=[]
        metrics={}
        for h,w in zip(windows,weights):
            if len(rs)>=h:
                sample=rs[-h:]
                ann=_annualized_mean(sample)
                metrics[f"return_{h}d_ann"]=ann
                weighted.append(ann*w);used.append(w)
        expected=sum(weighted)/sum(used) if used else 0.0
        risk_sample=rs[-63:] if len(rs)>=63 else rs
        risk=pstdev(risk_sample)*math.sqrt(252) if len(risk_sample)>1 else 0.0
        uncertainty=(pstdev(risk_sample)/math.sqrt(len(risk_sample))*math.sqrt(252)) if len(risk_sample)>1 else 0.0
        metrics["max_drawdown_252d"]=_max_drawdown(rs[-252:])
        metrics["latest_return"]=rs[-1] if rs else 0.0
        metrics["risk_ann"]=risk
        metrics["uncertainty_ann"]=uncertainty
        states.append(
            StrategyState(
                strategy_id=pid,
                lifecycle="active",
                expected_net_return=0.0 if pid=="P28_CASH" else expected,
                risk=0.0 if pid=="P28_CASH" else risk,
                uncertainty=0.0 if pid=="P28_CASH" else uncertainty,
                estimated_cost=0.0,
                oos_marginal_value=expected,
                metrics=metrics,
                recent_returns=[float(x) for x in rs[-252:]],
                selection_reason=BilingualText(
                    zh=f"根据21/63/126/252日真实净收益轨迹，当前年化预期净回报估计为 {expected:.2%}。",
                    en=f"Based on 21/63/126/252-day realized net-return history, current annualized expected net return is estimated at {expected:.2%}.",
                ),
            )
        )
    return states


def infer_regime(panel:MarketPanel)->str:
    b=panel.spec.benchmark
    i=len(panel.ts)-1
    rs=_ret(panel.close[b])
    vol=_vol(rs,i,20) or 0.0
    dd=_drawdown(panel.close[b],i,252)
    mom63=_mom(panel.close[b],i,63) or 0.0
    s100=_sma(panel.close[b],100,i)
    if vol>0.30 or dd<-0.15:
        return "stress_high_vol"
    if mom63<0 and s100 is not None and panel.close[b][i]<s100:
        return "risk_off"
    if mom63>0 and (s100 is None or panel.close[b][i]>=s100):
        return "risk_on_trend"
    return "mixed"


def prepare_live_market(
    market_id:str,
    window_weights:tuple[float,float,float,float]|None=None,
)->dict:
    panel=fetch_panel(market_id,"DAILY",force=True)
    history=policy_return_history(panel)
    states=build_strategy_states(panel,history,window_weights)
    latest_ts=panel.ts[-1]
    previous_ts=panel.ts[-2]
    as_of=datetime.fromtimestamp(latest_ts,tz=timezone.utc).date().isoformat()
    previous_as_of=datetime.fromtimestamp(previous_ts,tz=timezone.utc).date().isoformat()
    snapshot=MarketSnapshot(
        market_id=panel.spec.market_id,
        as_of=as_of,
        snapshot_id=f"{panel.spec.market_id}:{latest_ts}",
        regime=infer_regime(panel),
        metadata={
            "benchmark":panel.spec.benchmark,
            "assets":panel.assets,
            "source":panel.provider,
            "data_mode":panel.data_mode,
            "data_quality":panel.quality,
            "source_latest_ts":latest_ts,
            "currency":panel.spec.currency,
            "reference_capital":panel.spec.reference_capital,
            "base_cost_bps":panel.spec.base_cost_bps,
            "impact_coefficient_bps":panel.spec.impact_coefficient_bps,
            "max_participation_adv":panel.spec.max_participation_adv,
        },
    )
    realized={pid:history[pid][-1] for pid in history}
    product_realized={
        asset:(panel.close[asset][-1]/panel.close[asset][-2]-1.0)
        for asset in panel.assets
        if len(panel.close.get(asset,[]))>=2 and panel.close[asset][-2]
    }
    product_turnover={
        asset:(float(panel.close[asset][-1])*float(panel.volume.get(asset,[0.0])[-1]))
        for asset in panel.assets
        if panel.close.get(asset) and panel.volume.get(asset)
        and float(panel.close[asset][-1])>0 and float(panel.volume[asset][-1])>0
    }
    snapshot.metadata["session_phase"]=session_phase(panel.spec.market_id)
    snapshot.metadata["recovery_wave_input_scope"]="TRACKED_PRODUCT_PRICE_VOLUME_ONLY"
    return {
        "snapshot":snapshot,
        "strategy_states":states,
        "realized_returns_from_previous_period":realized,
        "product_realized_returns_from_previous_period":product_realized,
        "product_turnover_notional_from_previous_period":product_turnover,
        "previous_as_of":previous_as_of,
        "latest_as_of":as_of,
        "panel":panel,
    }
