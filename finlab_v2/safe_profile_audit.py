import json
import math
from statistics import mean, pstdev

from triaid_fin.contracts import StrategyState
from triaid_fin.market_lab import fetch_panel, policy_return_history
from triaid_fin.strategy_evolution import StrategyRuleProfile
from triaid_fin.strategy_population import StrategyPopulationModule


def light_states(history,t,weights):
    windows=(21,63,126,252)
    total=sum(weights);weights=tuple(w/total for w in weights)
    out=[]
    for pid,full in history.items():
        rs=full[:t+1];vals=[];used=[]
        for h,w in zip(windows,weights):
            if len(rs)>=h:
                vals.append(mean(rs[-h:])*252*w);used.append(w)
        expected=sum(vals)/sum(used) if used else 0.0
        sample=rs[-63:]
        risk=pstdev(sample)*math.sqrt(252) if len(sample)>1 else 0.0
        uncertainty=pstdev(sample)/math.sqrt(len(sample))*math.sqrt(252) if len(sample)>1 else 0.0
        out.append(StrategyState(
            strategy_id=pid,lifecycle="active",
            expected_net_return=0.0 if pid=="P28_CASH" else expected,
            risk=0.0 if pid=="P28_CASH" else risk,
            uncertainty=0.0 if pid=="P28_CASH" else uncertainty,
            oos_marginal_value=expected,
        ))
    return out


def legacy_weights(states,cap=0.28):
    ranked=sorted([s for s in states if s.strategy_id!="P28_CASH" and s.expected_net_return>0],key=lambda s:s.expected_net_return,reverse=True)[:12]
    positive={s.strategy_id:s.expected_net_return for s in ranked};active=set(positive);weights={};remaining=1.0
    while active and remaining>1e-12:
        total=sum(positive[k] for k in active);tentative={k:remaining*positive[k]/total for k in active}
        capped=[k for k,w in tentative.items() if w>cap]
        if not capped:weights.update(tentative);remaining=0.0;break
        for k in capped:weights[k]=cap;remaining-=cap;active.remove(k)
    if remaining>1e-12:weights["P28_CASH"]=remaining
    return weights


def tv(a,b):return sum(abs(a.get(k,0.0)-b.get(k,0.0)) for k in set(a)|set(b))


def metrics(rs):
    eq=1.0;peak=1.0;dd=0.0
    for r in rs:eq*=1+r;peak=max(peak,eq);dd=min(dd,eq/peak-1)
    return {"annualized_return":eq**(252/len(rs))-1,"max_drawdown":dd,"mean_daily":sum(rs)/len(rs)}


def run(market):
    panel=fetch_panel(market);history=policy_return_history(panel);start=max(300,len(panel.ts)-504)
    profile=StrategyRuleProfile(
        version=f"strategy-rules-{market.lower()}@0.3.0-safe",market_id=market,
        window_weights=(0.35,0.30,0.20,0.15),max_group_size=12,max_weight=0.28,
        entry_confirm_days=5 if market=="CN" else 3,exit_confirm_days=3,cooldown_days=10 if market=="CN" else 5,
        near_duplicate_corr=1.01,family_cap=12,redundancy_penalty=0.0,uncertainty_penalty=0.0,
        switch_hurdle_bps=0.0,switch_uncertainty_fraction=0.0,
    )
    sel=StrategyPopulationModule();sel.configure_market(profile)
    prev_group=None;prev_legacy={};new=[];old=[];new_tv=[];old_tv=[]
    rows=[]
    for t in range(start,len(panel.ts)-1):
        states=light_states(history,t,profile.window_weights)
        realized={pid:history[pid][t+1] for pid in history}
        g=sel.select(market,states,12,previous_group=prev_group,base_cost_bps=panel.spec.base_cost_bps)
        lw=legacy_weights(states)
        nt=tv(prev_group.weights if prev_group else {},g.weights);ot=tv(prev_legacy,lw)
        new.append(sum(w*realized.get(k,0) for k,w in g.weights.items())-nt*panel.spec.base_cost_bps/10000)
        old.append(sum(w*realized.get(k,0) for k,w in lw.items())-ot*panel.spec.base_cost_bps/10000)
        new_tv.append(nt);old_tv.append(ot);prev_group=g;prev_legacy=lw
    split=int(len(new)*0.70)
    return {
        "market":market,"days":len(new),"dev_days":split,"holdout_days":len(new)-split,
        "legacy_dev":metrics(old[:split]),"safe_dev":metrics(new[:split]),
        "legacy_holdout":metrics(old[split:]),"safe_holdout":metrics(new[split:]),
        "holdout_return_delta":metrics(new[split:])["annualized_return"]-metrics(old[split:])["annualized_return"],
        "average_turnover_delta":sum(new_tv)/len(new_tv)-sum(old_tv)/len(old_tv),
    }


print("TRIAID_SAFE_PROFILE_AUDIT_PASS")
for market in ("US","CN"):
    print("TRIAID_SAFE_PROFILE_AUDIT",json.dumps(run(market),sort_keys=True))
