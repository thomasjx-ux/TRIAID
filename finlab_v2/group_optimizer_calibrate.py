import json
import math
from dataclasses import asdict

from triaid_fin.market_lab import build_strategy_states, fetch_panel, policy_return_history
from triaid_fin.strategy_evolution import StrategyRuleProfile
from triaid_fin.strategy_population import StrategyPopulationModule


def legacy_weights(states,max_members=12,cap=0.28):
    ranked=sorted(
        [s for s in states if s.strategy_id!="P28_CASH" and s.expected_net_return>0],
        key=lambda s:s.expected_net_return,
        reverse=True,
    )[:max_members]
    positive={s.strategy_id:max(0.0,s.expected_net_return) for s in ranked}
    active=set(positive);weights={};remaining=1.0
    while active and remaining>1e-12:
        total=sum(positive[k] for k in active)
        if total<=0:break
        tentative={k:remaining*positive[k]/total for k in active}
        capped=[k for k,w in tentative.items() if w>cap]
        if not capped:
            weights.update(tentative);remaining=0.0;break
        for k in capped:
            weights[k]=cap;remaining-=cap;active.remove(k)
    if remaining>1e-12:weights["P28_CASH"]=remaining
    return weights


def turnover(a,b):
    return sum(abs(a.get(k,0.0)-b.get(k,0.0)) for k in set(a)|set(b))


def stats(rs):
    if not rs:return {"annualized_return":0.0,"max_drawdown":0.0,"mean_daily_return":0.0}
    eq=1.0;peak=1.0;dd=0.0
    for r in rs:
        eq*=1.0+r;peak=max(peak,eq);dd=min(dd,eq/peak-1.0)
    return {
        "annualized_return":eq**(252.0/len(rs))-1.0,
        "max_drawdown":dd,
        "mean_daily_return":sum(rs)/len(rs),
    }


def make_profiles(market):
    if market=="CN":
        entry,exit_,cooldown=5,3,10
    else:
        entry,exit_,cooldown=3,3,5
    weight_sets=[
        ("default",(0.35,0.30,0.20,0.15)),
        ("short",(0.45,0.30,0.15,0.10)),
        ("balanced",(0.25,0.25,0.25,0.25)),
        ("long",(0.20,0.25,0.25,0.30)),
    ]
    profiles=[]
    for label,weights in weight_sets:
        for size in (8,10,12):
            for uncertainty in (0.0,0.25):
                profiles.append(StrategyRuleProfile(
                    version=f"CAL-{market}-{label}-{size}-u{uncertainty}",
                    market_id=market,
                    window_weights=weights,
                    max_group_size=size,
                    max_weight=0.28,
                    entry_confirm_days=entry,
                    exit_confirm_days=exit_,
                    cooldown_days=cooldown,
                    near_duplicate_corr=1.01,
                    family_cap=12,
                    redundancy_penalty=0.0,
                    uncertainty_penalty=uncertainty,
                    switch_hurdle_bps=0.0,
                    switch_uncertainty_fraction=0.0,
                ))
    return profiles


def simulate_profile(panel,history,profile,start):
    selector=StrategyPopulationModule();selector.configure_market(profile)
    prev=None;returns=[];turns=[];sizes=[]
    for t in range(start,len(panel.ts)-1):
        cut={pid:rs[:t+1] for pid,rs in history.items()}
        states=build_strategy_states(panel,cut,profile.window_weights)
        next_realized={pid:history[pid][t+1] for pid in history}
        group=selector.select(
            profile.market_id,states,profile.max_group_size,
            previous_group=prev,base_cost_bps=panel.spec.base_cost_bps,
        )
        tv=turnover(prev.weights if prev else {},group.weights)
        gross=sum(w*next_realized.get(k,0.0) for k,w in group.weights.items())
        returns.append(gross-tv*panel.spec.base_cost_bps/10000.0)
        turns.append(tv)
        sizes.append(len([k for k,v in group.weights.items() if k!="P28_CASH" and v>0]))
        prev=group
    return returns,turns,sizes


def simulate_legacy(panel,history,start):
    prev={};returns=[];turns=[];sizes=[]
    for t in range(start,len(panel.ts)-1):
        cut={pid:rs[:t+1] for pid,rs in history.items()}
        states=build_strategy_states(panel,cut,(0.35,0.30,0.20,0.15))
        next_realized={pid:history[pid][t+1] for pid in history}
        w=legacy_weights(states)
        tv=turnover(prev,w)
        gross=sum(x*next_realized.get(k,0.0) for k,x in w.items())
        returns.append(gross-tv*panel.spec.base_cost_bps/10000.0)
        turns.append(tv);sizes.append(len([k for k,v in w.items() if k!="P28_CASH" and v>0]))
        prev=w
    return returns,turns,sizes


def calibrate(market):
    panel=fetch_panel(market);history=policy_return_history(panel)
    start=max(300,len(panel.ts)-504)
    legacy_rs,legacy_turn,legacy_size=simulate_legacy(panel,history,start)
    split=max(1,int(len(legacy_rs)*0.70))
    legacy_dev=stats(legacy_rs[:split]);legacy_hold=stats(legacy_rs[split:])

    scored=[]
    for profile in make_profiles(market):
        rs,turns,sizes=simulate_profile(panel,history,profile,start)
        dev=stats(rs[:split]);hold=stats(rs[split:])
        scored.append({
            "profile":profile,
            "dev":dev,
            "holdout":hold,
            "average_turnover":sum(turns)/len(turns),
            "average_group_size":sum(sizes)/len(sizes),
        })
    best=max(scored,key=lambda x:x["dev"]["annualized_return"])
    holdout_pass=best["holdout"]["annualized_return"]>=legacy_hold["annualized_return"]
    recommendation=best["profile"] if holdout_pass else StrategyRuleProfile(
        version=f"strategy-rules-{market.lower()}@0.3.0-safe",
        market_id=market,
        window_weights=(0.35,0.30,0.20,0.15),
        max_group_size=12,max_weight=0.28,
        entry_confirm_days=5 if market=="CN" else 3,
        exit_confirm_days=3,cooldown_days=10 if market=="CN" else 5,
        near_duplicate_corr=1.01,family_cap=12,redundancy_penalty=0.0,
        uncertainty_penalty=0.0,switch_hurdle_bps=0.0,switch_uncertainty_fraction=0.0,
    )
    return {
        "market":market,
        "days":len(legacy_rs),
        "development_days":split,
        "holdout_days":len(legacy_rs)-split,
        "legacy":{"development":legacy_dev,"holdout":legacy_hold,"average_turnover":sum(legacy_turn)/len(legacy_turn),"average_group_size":sum(legacy_size)/len(legacy_size)},
        "best_development_candidate":{
            "profile":asdict(best["profile"]),
            "development":best["dev"],
            "holdout":best["holdout"],
            "average_turnover":best["average_turnover"],
            "average_group_size":best["average_group_size"],
        },
        "holdout_pass":holdout_pass,
        "recommended_profile":asdict(recommendation),
    }


results=[calibrate("US"),calibrate("CN")]
print("TRIAID_GROUP_CALIBRATION_PASS")
for row in results:
    print("TRIAID_GROUP_CALIBRATION",json.dumps(row,sort_keys=True))
