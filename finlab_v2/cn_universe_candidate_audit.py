import json
import math
from statistics import mean, pstdev

from triaid_fin.contracts import StrategyState
from triaid_fin.market_lab import (
    _drawdown,_mom,_ret,_sma,_trade_cost,_vol,
    fetch_panel, policy_return_history,
)
from triaid_fin.strategy_evolution import StrategyRuleProfile
from triaid_fin.strategy_population import StrategyPopulationModule


CANDIDATES=(
    "C29_SIZE_REL20",
    "C30_SIZE_REL63",
    "C31_XREV5",
    "C32_VOL_BREAKOUT20",
    "C33_VOLUME_REVERSAL5",
    "C34_DRAWDOWN_RECOVERY",
    "C35_DISPERSION_ROT",
    "C36_BREADTH_ACCEL",
)


def one(assets,asset,w=1.0):
    return [w if a==asset else 0.0 for a in assets]


def vec(assets,weights):
    return [max(0.0,float(weights.get(a,0.0))) for a in assets]


def avg_volume(panel,asset,i,h):
    if asset not in panel.volume or i+1<h:return None
    xs=panel.volume[asset][i+1-h:i+1]
    return mean(xs) if xs else None


def candidate_positions(panel,i):
    assets=panel.assets
    risk=[a for a in panel.spec.risk_assets if a in panel.close]
    benchmark=panel.spec.benchmark
    rs={a:_ret(panel.close[a]) for a in assets}
    out={}

    # 1/2. Large-small relative-strength rotation.
    large=benchmark
    small=[a for a in risk if a!=large]
    for pid,h in (("C29_SIZE_REL20",20),("C30_SIZE_REL63",63)):
        lm=_mom(panel.close[large],i,h)
        ranked=[(_mom(panel.close[a],i,h),a) for a in small]
        ranked=[x for x in ranked if x[0] is not None]
        sm,sa=max(ranked) if ranked else (None,None)
        if sm is not None and sm>0 and (lm is None or sm>lm+0.01):
            out[pid]=one(assets,sa)
        elif lm is not None and lm>0:
            out[pid]=one(assets,large)
        else:
            out[pid]=[0.0]*len(assets)

    # 3. Cross-sectional 5-day reversal, but only inside positive 63-day trends.
    rev=[]
    for a in risk:
        m5=_mom(panel.close[a],i,5)
        m63=_mom(panel.close[a],i,63)
        if m5 is not None and m63 is not None and m63>0:
            rev.append((m5,a))
    if rev and min(rev)[0]<-0.015:
        out["C31_XREV5"]=one(assets,min(rev)[1])
    else:
        out["C31_XREV5"]=[0.0]*len(assets)

    # 4. 20-day breakout confirmed by expanding volume.
    breakout=[]
    for a in risk:
        m20=_mom(panel.close[a],i,20)
        v5=avg_volume(panel,a,i,5);v20=avg_volume(panel,a,i,20)
        ratio=(v5/v20) if v5 is not None and v20 and v20>0 else 1.0
        if m20 is not None and m20>0.02 and ratio>1.05:
            breakout.append((m20*ratio,a))
    out["C32_VOL_BREAKOUT20"]=one(assets,max(breakout)[1]) if breakout else [0.0]*len(assets)

    # 5. Volume-supported short-term capitulation reversal.
    cap=[]
    for a in risk:
        m5=_mom(panel.close[a],i,5);m63=_mom(panel.close[a],i,63)
        v5=avg_volume(panel,a,i,5);v20=avg_volume(panel,a,i,20)
        ratio=(v5/v20) if v5 is not None and v20 and v20>0 else 1.0
        if m5 is not None and m63 is not None and m5<-0.025 and m63>-0.08 and ratio>1.15:
            cap.append((-m5*ratio,a))
    out["C33_VOLUME_REVERSAL5"]=one(assets,max(cap)[1]) if cap else [0.0]*len(assets)

    # 6. Re-entry after material drawdown once both 5d and 20d momentum turn positive.
    dd=_drawdown(panel.close[benchmark],i,252)
    m5=_mom(panel.close[benchmark],i,5) or 0.0
    m20=_mom(panel.close[benchmark],i,20) or 0.0
    if dd<-0.06 and m5>0 and m20>0:
        scored=[((_mom(panel.close[a],i,20) or -999),a) for a in risk]
        out["C34_DRAWDOWN_RECOVERY"]=one(assets,max(scored)[1])
    else:
        out["C34_DRAWDOWN_RECOVERY"]=[0.0]*len(assets)

    # 7. Cross-sectional dispersion: ride the leader only when leadership is meaningful.
    m20s=[((_mom(panel.close[a],i,20)),a) for a in risk]
    m20s=[x for x in m20s if x[0] is not None]
    if len(m20s)>=3:
        vals=[x[0] for x in m20s]
        dispersion=pstdev(vals)
        best=max(m20s)
        if dispersion>0.035 and best[0]>0:
            out["C35_DISPERSION_ROT"]=one(assets,best[1])
        elif (_mom(panel.close[benchmark],i,63) or 0)>0:
            out["C35_DISPERSION_ROT"]=one(assets,benchmark,0.60)
        else:
            out["C35_DISPERSION_ROT"]=[0.0]*len(assets)
    else:
        out["C35_DISPERSION_ROT"]=[0.0]*len(assets)

    # 8. Breadth acceleration: enter only when the share of assets above 20d MA is improving.
    def breadth(at):
        if at<25:return 0.0
        ok=0;total=0
        for a in risk:
            s=_sma(panel.close[a],20,at)
            if s is not None:
                total+=1;ok+=panel.close[a][at]>=s
        return ok/max(1,total)
    now=breadth(i);old=breadth(i-5)
    if now>=0.50 and now>old:
        scores=sorted([((_mom(panel.close[a],i,20) or -999),a) for a in risk],reverse=True)
        winners=[a for m,a in scores[:2] if m>0]
        out["C36_BREADTH_ACCEL"]=vec(assets,{a:1/len(winners) for a in winners}) if winners else [0.0]*len(assets)
    else:
        out["C36_BREADTH_ACCEL"]=[0.0]*len(assets)
    return out


def candidate_history(panel):
    n=len(panel.ts);assets=panel.assets
    asset_ret={a:_ret(panel.close[a]) for a in assets}
    out={pid:[0.0]*n for pid in CANDIDATES}
    prev={pid:[0.0]*len(assets) for pid in CANDIDATES}
    for i in range(n-1):
        pos=candidate_positions(panel,i)
        nxt=[asset_ret[a][i+1] for a in assets]
        for pid in CANDIDATES:
            p=pos[pid]
            gross=sum(w*r for w,r in zip(p,nxt))
            cost,_=_trade_cost(prev[pid],p,panel,i)
            out[pid][i+1]=gross-cost
            prev[pid]=p
    return out


def corr(a,b):
    n=min(len(a),len(b))
    if n<30:return 0.0
    x=a[-n:];y=b[-n:]
    mx=mean(x);my=mean(y)
    dx=[v-mx for v in x];dy=[v-my for v in y]
    vx=sum(v*v for v in dx);vy=sum(v*v for v in dy)
    if vx<=1e-18 or vy<=1e-18:return 0.0
    return max(-1,min(1,sum(i*j for i,j in zip(dx,dy))/math.sqrt(vx*vy)))


def states_at(history,t):
    windows=(21,63,126,252);weights=(0.35,0.30,0.20,0.15)
    states=[]
    for pid,full in history.items():
        rs=full[:t+1];vals=[];used=[]
        for h,w in zip(windows,weights):
            if len(rs)>=h:
                vals.append(mean(rs[-h:])*252*w);used.append(w)
        expected=sum(vals)/sum(used) if used else 0.0
        sample=rs[-63:]
        risk=pstdev(sample)*math.sqrt(252) if len(sample)>1 else 0.0
        uncertainty=pstdev(sample)/math.sqrt(len(sample))*math.sqrt(252) if len(sample)>1 else 0.0
        states.append(StrategyState(
            strategy_id=pid,lifecycle="active",expected_net_return=0.0 if pid=="P28_CASH" else expected,
            risk=0.0 if pid=="P28_CASH" else risk,uncertainty=0.0 if pid=="P28_CASH" else uncertainty,
            oos_marginal_value=expected,recent_returns=[float(x) for x in rs[-252:]],
        ))
    return states


def make_profile():
    return StrategyRuleProfile(
        version="CN-UNIVERSE-AUDIT",market_id="CN",
        window_weights=(0.35,0.30,0.20,0.15),max_group_size=12,max_weight=0.28,
        entry_confirm_days=5,exit_confirm_days=3,cooldown_days=10,
        near_duplicate_corr=1.01,family_cap=99,redundancy_penalty=0.0,uncertainty_penalty=0.0,
        switch_hurdle_bps=0.0,switch_uncertainty_fraction=0.0,switch_guard_enabled=True,
    )


def simulate(panel,history,start,end):
    selector=StrategyPopulationModule();selector.configure_market(make_profile())
    prev=None;returns=[];cash_days=0
    for t in range(start,end):
        states=states_at(history,t)
        group=selector.select("CN",states,12,previous_group=prev,base_cost_bps=panel.spec.base_cost_bps)
        realized={pid:history[pid][t+1] for pid in history}
        turnover=sum(abs(group.weights.get(k,0)-((prev.weights if prev else {}).get(k,0))) for k in set(group.weights)|set(prev.weights if prev else {}))
        gross=sum(w*realized.get(k,0) for k,w in group.weights.items())
        returns.append(gross-turnover*panel.spec.base_cost_bps/10000)
        if set(group.members)=={"P28_CASH"}:cash_days+=1
        prev=group
    return returns,cash_days


def metrics(rs):
    if not rs:return {"annualized_return":0.0,"max_drawdown":0.0,"mean_daily":0.0}
    eq=1;peak=1;dd=0
    for r in rs:eq*=1+r;peak=max(peak,eq);dd=min(dd,eq/peak-1)
    return {"annualized_return":eq**(252/len(rs))-1,"max_drawdown":dd,"mean_daily":mean(rs)}


panel=fetch_panel("CN")
base=policy_return_history(panel)
cand=candidate_history(panel)
start=max(300,len(panel.ts)-504)
end=len(panel.ts)-1
split=start+int((end-start)*0.70)

# Candidate standalone / novelty diagnostics are computed only on development data.
candidate_diag={}
for pid in CANDIDATES:
    dev=cand[pid][start:split+1]
    max_corr=max(abs(corr(dev,base[k][start:split+1])) for k in base if k!="P28_CASH")
    candidate_diag[pid]={
        "development":metrics(dev),
        "max_abs_corr_existing":max_corr,
    }

# Greedy universe expansion using development only.
selected=[]
current=dict(base)
current_dev,_=simulate(panel,current,start,split)
current_score=metrics(current_dev)["annualized_return"]
steps=[]
remaining=list(CANDIDATES)
while remaining:
    trials=[]
    for pid in remaining:
        h=dict(current);h[pid]=cand[pid]
        rs,_=simulate(panel,h,start,split)
        trials.append((metrics(rs)["annualized_return"],pid))
    best_score,best_pid=max(trials)
    improvement=best_score-current_score
    if improvement<=0.0025:
        break
    selected.append(best_pid);remaining.remove(best_pid)
    current[best_pid]=cand[best_pid]
    steps.append({"strategy":best_pid,"development_ann_return":best_score,"improvement":improvement})
    current_score=best_score

# Freeze selection, then touch holdout once.
base_dev,base_dev_cash=simulate(panel,base,start,split)
expanded_dev,expanded_dev_cash=simulate(panel,current,start,split)
base_hold,base_hold_cash=simulate(panel,base,split,end)
expanded_hold,expanded_hold_cash=simulate(panel,current,split,end)

report={
    "market":"CN",
    "candidate_count":len(CANDIDATES),
    "candidates":list(CANDIDATES),
    "development_days":split-start,
    "holdout_days":end-split,
    "candidate_development_diagnostics":candidate_diag,
    "development_selection_steps":steps,
    "selected_candidates":selected,
    "base_development":metrics(base_dev),
    "expanded_development":metrics(expanded_dev),
    "base_holdout":metrics(base_hold),
    "expanded_holdout":metrics(expanded_hold),
    "holdout_ann_return_delta":metrics(expanded_hold)["annualized_return"]-metrics(base_hold)["annualized_return"],
    "holdout_max_drawdown_delta":metrics(expanded_hold)["max_drawdown"]-metrics(base_hold)["max_drawdown"],
    "base_holdout_cash_days":base_hold_cash,
    "expanded_holdout_cash_days":expanded_hold_cash,
    "promotion_pass":(
        len(selected)>0
        and metrics(expanded_hold)["annualized_return"]>metrics(base_hold)["annualized_return"]
        and metrics(expanded_hold)["max_drawdown"]>=metrics(base_hold)["max_drawdown"]-0.03
    ),
}

print("TRIAID_CN_UNIVERSE_CANDIDATE_AUDIT_PASS")
print("TRIAID_CN_UNIVERSE_CANDIDATE_AUDIT",json.dumps(report,sort_keys=True))
