from __future__ import annotations

from statistics import mean, pstdev

from .contracts import BilingualText, StrategyDefinition

CN_SHADOW_IDS=(
    "C29_SIZE_REL20",
    "C30_SIZE_REL63",
    "C32_VOL_BREAKOUT20",
    "C36_BREADTH_ACCEL",
)

def definitions()->list[StrategyDefinition]:
    rows=[
        ("C29_SIZE_REL20","20日大小盘相对强弱","20-Day Size Relative Strength",
         "比较沪深300与中小盘/成长指数的20日相对强弱，持有更强且为正动量的一侧。",
         "Compares 20-day relative strength between large-cap and smaller/growth indices and holds the stronger positive-momentum side.",
         "风格快速轮动、大小盘分化明显时。","When style leadership rotates and large/small-cap dispersion is meaningful.",
         "风格切换过快时会发生追涨与反复换仓。","Fast style reversals can create chasing and turnover."),
        ("C30_SIZE_REL63","63日大小盘相对强弱","63-Day Size Relative Strength",
         "用63日窗口判断大小盘中期相对强弱，持有相对趋势更强的一侧。",
         "Uses a 63-day window to rotate toward the stronger medium-term size/style segment.",
         "中期风格趋势较持续时。","When medium-term style leadership persists.",
         "风格拐点附近反应较慢。","Can react slowly near style turning points."),
        ("C32_VOL_BREAKOUT20","成交量确认突破","Volume-Confirmed Breakout",
         "寻找20日正向突破且5日成交量高于20日均值的风险资产。",
         "Selects risk assets with positive 20-day breakout and expanding short-term volume.",
         "趋势启动伴随成交活跃扩张时。","When trend initiation is confirmed by expanding activity.",
         "事件驱动放量但趋势不持续时可能产生假突破。","Event-driven volume spikes can create false breakouts."),
        ("C36_BREADTH_ACCEL","市场宽度加速","Breadth Acceleration",
         "比较当前与5日前站上20日均线的风险资产比例，宽度改善时持有最强的两个资产。",
         "Compares the share of risk assets above their 20-day average with five days earlier and buys the top leaders when breadth improves.",
         "下跌后市场内部参与度开始同步修复时。","When participation broadens during a market recovery.",
         "少数指数成分主导或宽度短暂反弹时可能误判。","Can misread short-lived breadth rebounds or cap-weighted leadership."),
    ]
    out=[]
    for pid,zh,en,zh_logic,en_logic,zh_best,en_best,zh_risk,en_risk in rows:
        out.append(StrategyDefinition(
            strategy_id=pid,
            version="cn-incubator@0.1.0",
            market_support=["CN"],
            name=BilingualText(zh=zh,en=en),
            summary=BilingualText(zh=zh_logic,en=en_logic),
            logic=BilingualText(zh=zh_logic,en=en_logic),
            best_conditions=BilingualText(zh=zh_best,en=en_best),
            main_risks=BilingualText(zh=zh_risk,en=en_risk),
        ))
    return out

def _mom(xs,i,h):
    if i<h or xs[i-h]<=0:return None
    return xs[i]/xs[i-h]-1.0

def _sma(xs,h,i):
    if i+1<h:return None
    return sum(xs[i+1-h:i+1])/h

def _one(assets,asset,w=1.0):
    return [float(w) if a==asset else 0.0 for a in assets]

def _vec(assets,weights):
    return [max(0.0,float(weights.get(a,0.0))) for a in assets]

def _avg_volume(panel,asset,i,h):
    if asset not in panel.volume or i+1<h:return None
    xs=panel.volume[asset][i+1-h:i+1]
    return mean(xs) if xs else None

def positions(panel,i):
    assets=panel.assets
    risk=[a for a in panel.spec.risk_assets if a in panel.close]
    benchmark=panel.spec.benchmark
    out={}

    large=benchmark
    small=[a for a in risk if a!=large]
    for pid,h in (("C29_SIZE_REL20",20),("C30_SIZE_REL63",63)):
        lm=_mom(panel.close[large],i,h)
        ranked=[(_mom(panel.close[a],i,h),a) for a in small]
        ranked=[x for x in ranked if x[0] is not None]
        sm,sa=max(ranked) if ranked else (None,None)
        if sm is not None and sm>0 and (lm is None or sm>lm+0.01):
            out[pid]=_one(assets,sa)
        elif lm is not None and lm>0:
            out[pid]=_one(assets,large)
        else:
            out[pid]=[0.0]*len(assets)

    breakout=[]
    for a in risk:
        m20=_mom(panel.close[a],i,20)
        v5=_avg_volume(panel,a,i,5);v20=_avg_volume(panel,a,i,20)
        ratio=(v5/v20) if v5 is not None and v20 and v20>0 else 1.0
        if m20 is not None and m20>0.02 and ratio>1.05:
            breakout.append((m20*ratio,a))
    out["C32_VOL_BREAKOUT20"]=_one(assets,max(breakout)[1]) if breakout else [0.0]*len(assets)

    def breadth(at):
        if at<25:return 0.0
        ok=0;total=0
        for a in risk:
            s=_sma(panel.close[a],20,at)
            if s is not None:
                total+=1
                ok+=panel.close[a][at]>=s
        return ok/max(1,total)

    now=breadth(i);old=breadth(i-5)
    if now>=0.50 and now>old:
        scores=sorted([((_mom(panel.close[a],i,20) or -999),a) for a in risk],reverse=True)
        winners=[a for m,a in scores[:2] if m>0]
        out["C36_BREADTH_ACCEL"]=_vec(assets,{a:1/len(winners) for a in winners}) if winners else [0.0]*len(assets)
    else:
        out["C36_BREADTH_ACCEL"]=[0.0]*len(assets)

    return out
