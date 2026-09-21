from __future__ import annotations

import math

from triaid_fin.cn_incubator import CN_SHADOW_IDS, positions as cn_shadow_positions
from triaid_fin.market_lab import MarketPanel, MarketSpec, policy_positions
from triaid_fin.strategy_registry import POLICY_IDS, build_definitions, strategy_ids_for_market


def make_panel(market_id:str)->MarketPanel:
    if market_id=="US":
        spec=MarketSpec(
            "US","SPY",
            ("SPY","QQQ","IWM","TLT","GLD"),
            ("SPY","QQQ","IWM"),
            ("TLT","GLD"),
            "USD",10_000_000.0,1.5,45.0,0.03,
        )
    else:
        spec=MarketSpec(
            "CN","510300.SS",
            ("510300.SS","510500.SS","159915.SZ","512100.SS","511010.SS"),
            ("510300.SS","510500.SS","159915.SZ","512100.SS"),
            ("511010.SS",),
            "CNY",50_000_000.0,2.5,60.0,0.02,
        )
    n=320
    ts=list(range(1_700_000_000,1_700_000_000+n*86400,86400))
    close={}
    volume={}
    for j,symbol in enumerate(spec.assets):
        px=100.0+3*j
        xs=[];vs=[]
        for i in range(n):
            drift=0.0005+0.00015*j
            cyc=0.0015*math.sin((i+4*j)/17.0)
            px=max(1.0,px*(1.0+drift+cyc))
            xs.append(px)
            vs.append(1_000_000.0+10_000*j+2_000*math.cos((i+j)/11.0))
        close[symbol]=xs
        volume[symbol]=vs
    return MarketPanel(
        spec=spec,ts=ts,close=close,volume=volume,
        data_mode="DAILY",provider="strategy-contract-smoke",quality="research",
    )


defs=build_definitions()
assert len(defs)==33
assert len({x.strategy_id for x in defs})==33
assert len(POLICY_IDS)==29
assert len(CN_SHADOW_IDS)==4
assert strategy_ids_for_market("US")==POLICY_IDS
assert set(strategy_ids_for_market("CN"))==set(POLICY_IDS)|set(CN_SHADOW_IDS)

for market in ("US","CN"):
    panel=make_panel(market)
    i=len(panel.ts)-1
    positions=policy_positions(panel,i)
    assert set(positions)==set(POLICY_IDS)
    if market=="CN":
        shadow=cn_shadow_positions(panel,i)
        assert set(shadow)==set(CN_SHADOW_IDS)
        positions={**positions,**shadow}
    expected=set(strategy_ids_for_market(market))
    assert set(positions)==expected
    for sid,vec in positions.items():
        assert len(vec)==len(panel.assets),sid
        assert all(math.isfinite(float(x)) and float(x)>=0 for x in vec),sid
        assert sum(float(x) for x in vec)<=1.0000001,sid

    assert abs(sum(positions["P00_BUY_HOLD"])-1.0)<1e-12
    assert abs(sum(positions["P28_CASH"]))<1e-12
    assert max(positions["P26_INVOL_BAL"])<=0.5000001

    if market=="US":
        balanced=positions["P25_BALANCED"]
        assert abs(balanced[panel.assets.index("SPY")]-0.60)<1e-12
        assert abs(balanced[panel.assets.index("TLT")]-0.20)<1e-12
        assert abs(balanced[panel.assets.index("GLD")]-0.20)<1e-12
    else:
        balanced=positions["P25_BALANCED"]
        assert abs(balanced[panel.assets.index("510300.SS")]-0.70)<1e-12
        assert abs(balanced[panel.assets.index("511010.SS")]-0.30)<1e-12

by_id={x.strategy_id:x for x in defs}
assert "-1.5%" in by_id["P16_REV5"].summary.en
assert "-4%" in by_id["P17_REV20"].summary.en
assert "50%" in by_id["P26_INVOL_BAL"].summary.en
assert "60%" in by_id["P27_BREADTH_ROT"].summary.en
assert "2%" in by_id["C32_VOL_BREAKOUT20"].summary.en
assert "5%" in by_id["C32_VOL_BREAKOUT20"].summary.en

print("TRIAID_STRATEGY_CONTRACT_SMOKE_PASS")
print({"US":len(strategy_ids_for_market("US")),"CN":len(strategy_ids_for_market("CN"))})
