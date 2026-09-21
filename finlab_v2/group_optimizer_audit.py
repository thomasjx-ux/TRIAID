import json
import math
import os
import shutil
import tempfile

tmp=tempfile.mkdtemp(prefix="triaid-group-audit-")
os.environ["TRIAID_DATA_DIR"]=tmp

from triaid_fin.market_lab import build_strategy_states, fetch_panel, policy_return_history
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
    if remaining>1e-12:
        weights["P28_CASH"]=remaining
    return weights


def turnover(a,b):
    return sum(abs(a.get(k,0.0)-b.get(k,0.0)) for k in set(a)|set(b))


def max_drawdown(returns):
    eq=1.0;peak=1.0;worst=0.0
    for r in returns:
        eq*=1.0+r;peak=max(peak,eq);worst=min(worst,eq/peak-1.0)
    return worst


def annualized(returns):
    if not returns:return 0.0
    eq=1.0
    for r in returns:eq*=1.0+r
    return eq**(252.0/len(returns))-1.0


def duplicate_pairs(group,states,selector,threshold):
    sm={s.strategy_id:s for s in states}
    ids=[x for x in group if x!="P28_CASH" and x in sm]
    count=0
    for i in range(len(ids)):
        for j in range(i+1,len(ids)):
            if abs(selector._corr(sm[ids[i]].recent_returns,sm[ids[j]].recent_returns))>=threshold:
                count+=1
    return count


def audit_market(market_id):
    panel=fetch_panel(market_id)
    history=policy_return_history(panel)
    selector=StrategyPopulationModule()
    cfg=selector.config_for(market_id)
    n=len(panel.ts)
    start=max(300,n-504)
    new_returns=[];legacy_returns=[]
    new_turn=[];legacy_turn=[]
    new_sizes=[];legacy_sizes=[]
    new_dupes=[];legacy_dupes=[]
    previous_group=None
    previous_legacy={}
    evaluated=0

    for t in range(start,n-1):
        cut={pid:rs[:t+1] for pid,rs in history.items()}
        states=build_strategy_states(panel,cut)
        next_realized={pid:history[pid][t+1] for pid in history}

        group=selector.select(
            market_id,
            states,
            12,
            previous_group=previous_group,
            base_cost_bps=panel.spec.base_cost_bps,
        )
        lw=legacy_weights(states,12,cfg.max_weight)

        nt=turnover(previous_group.weights if previous_group else {},group.weights)
        lt=turnover(previous_legacy,lw)
        nr=sum(w*next_realized.get(k,0.0) for k,w in group.weights.items())-nt*panel.spec.base_cost_bps/10000.0
        lr=sum(w*next_realized.get(k,0.0) for k,w in lw.items())-lt*panel.spec.base_cost_bps/10000.0

        new_returns.append(nr);legacy_returns.append(lr)
        new_turn.append(nt);legacy_turn.append(lt)
        new_sizes.append(len([k for k,v in group.weights.items() if k!="P28_CASH" and v>0]))
        legacy_sizes.append(len([k for k,v in lw.items() if k!="P28_CASH" and v>0]))
        new_dupes.append(duplicate_pairs(group.weights,states,selector,cfg.near_duplicate_corr))
        legacy_dupes.append(duplicate_pairs(lw,states,selector,cfg.near_duplicate_corr))
        previous_group=group;previous_legacy=lw;evaluated+=1

    result={
        "market":market_id,
        "days":evaluated,
        "legacy":{
            "annualized_return":annualized(legacy_returns),
            "max_drawdown":max_drawdown(legacy_returns),
            "mean_daily_return":sum(legacy_returns)/max(1,len(legacy_returns)),
            "average_turnover":sum(legacy_turn)/max(1,len(legacy_turn)),
            "average_risky_group_size":sum(legacy_sizes)/max(1,len(legacy_sizes)),
            "average_near_duplicate_pairs":sum(legacy_dupes)/max(1,len(legacy_dupes)),
        },
        "new":{
            "annualized_return":annualized(new_returns),
            "max_drawdown":max_drawdown(new_returns),
            "mean_daily_return":sum(new_returns)/max(1,len(new_returns)),
            "average_turnover":sum(new_turn)/max(1,len(new_turn)),
            "average_risky_group_size":sum(new_sizes)/max(1,len(new_sizes)),
            "average_near_duplicate_pairs":sum(new_dupes)/max(1,len(new_dupes)),
        },
    }
    result["delta"]={
        "annualized_return":result["new"]["annualized_return"]-result["legacy"]["annualized_return"],
        "max_drawdown":result["new"]["max_drawdown"]-result["legacy"]["max_drawdown"],
        "average_turnover":result["new"]["average_turnover"]-result["legacy"]["average_turnover"],
        "average_near_duplicate_pairs":result["new"]["average_near_duplicate_pairs"]-result["legacy"]["average_near_duplicate_pairs"],
    }
    return result


try:
    results=[audit_market("US"),audit_market("CN")]
    print("TRIAID_GROUP_OPTIMIZER_AUDIT_PASS")
    for row in results:
        print("TRIAID_GROUP_OPTIMIZER_AUDIT",json.dumps(row,sort_keys=True))
finally:
    shutil.rmtree(tmp,ignore_errors=True)
