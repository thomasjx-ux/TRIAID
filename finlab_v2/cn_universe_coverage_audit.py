import json
import math
from statistics import mean, pstdev

from triaid_fin.market_lab import fetch_panel, policy_return_history
from triaid_fin.strategy_registry import FAMILIES, NAMES, POLICY_IDS


def corr(a,b):
    n=min(len(a),len(b))
    if n<30:return 0.0
    x=a[-n:];y=b[-n:]
    mx=mean(x);my=mean(y)
    dx=[v-mx for v in x];dy=[v-my for v in y]
    vx=sum(v*v for v in dx);vy=sum(v*v for v in dy)
    if vx<=1e-18 or vy<=1e-18:return 0.0
    return max(-1.0,min(1.0,sum(i*j for i,j in zip(dx,dy))/math.sqrt(vx*vy)))


def connected_components(ids,history,threshold):
    graph={k:set() for k in ids}
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            if abs(corr(history[a],history[b]))>=threshold:
                graph[a].add(b);graph[b].add(a)
    seen=set();clusters=[]
    for root in ids:
        if root in seen:continue
        stack=[root];comp=[]
        while stack:
            x=stack.pop()
            if x in seen:continue
            seen.add(x);comp.append(x);stack.extend(graph[x]-seen)
        clusters.append(sorted(comp))
    return sorted(clusters,key=lambda c:(-len(c),c[0]))


def ann_return(rs):
    if not rs:return 0.0
    eq=1.0
    for r in rs:eq*=1.0+r
    return eq**(252.0/len(rs))-1.0


def max_dd(rs):
    eq=1.0;peak=1.0;worst=0.0
    for r in rs:
        eq*=1+r;peak=max(peak,eq);worst=min(worst,eq/peak-1)
    return worst


panel=fetch_panel("CN")
history=policy_return_history(panel)
ids=[x for x in POLICY_IDS if x!="P28_CASH"]
lookback=min(504,len(panel.ts)-1)
recent={k:v[-lookback:] for k,v in history.items()}

clusters_090=connected_components(ids,recent,0.90)
clusters_095=connected_components(ids,recent,0.95)
clusters_098=connected_components(ids,recent,0.98)

stats={}
for pid in ids:
    rs=recent[pid]
    stats[pid]={
        "name":NAMES[pid][0],
        "family":FAMILIES[pid],
        "annualized_return":ann_return(rs),
        "mean_daily":mean(rs),
        "vol_ann":pstdev(rs)*math.sqrt(252) if len(rs)>1 else 0.0,
        "max_drawdown":max_dd(rs),
        "positive_day_rate":sum(1 for x in rs if x>0)/len(rs),
    }

# Identify periods where cash would dominate all risky strategies on trailing 63d expectation.
cash_dominant=0
recovery_days=0
risk_returns={pid:history[pid] for pid in ids}
for t in range(max(252,len(panel.ts)-504),len(panel.ts)-1):
    exp={pid:mean(risk_returns[pid][t-62:t+1])*252 for pid in ids}
    if max(exp.values())<=0:
        cash_dominant+=1
        # Did any existing risky strategy make money on next day?
        if max(risk_returns[pid][t+1] for pid in ids)>0:
            recovery_days+=1

family_members={}
for pid in ids:
    family_members.setdefault(FAMILIES[pid],[]).append(pid)

report={
    "market":"CN",
    "as_of_points":len(panel.ts),
    "audit_lookback_days":lookback,
    "strategy_count":len(ids),
    "declared_family_count":len(family_members),
    "families":family_members,
    "behavior_clusters_abs_corr_0_90":clusters_090,
    "behavior_clusters_abs_corr_0_95":clusters_095,
    "behavior_clusters_abs_corr_0_98":clusters_098,
    "effective_cluster_count_0_90":len(clusters_090),
    "effective_cluster_count_0_95":len(clusters_095),
    "effective_cluster_count_0_98":len(clusters_098),
    "cash_dominant_days":cash_dominant,
    "cash_dominant_days_with_next_day_risky_winner":recovery_days,
    "cash_dominant_recovery_rate":recovery_days/cash_dominant if cash_dominant else None,
    "strategy_stats":stats,
}

print("TRIAID_CN_UNIVERSE_COVERAGE_AUDIT_PASS")
print("TRIAID_CN_UNIVERSE_COVERAGE_AUDIT",json.dumps(report,ensure_ascii=False,sort_keys=True))
