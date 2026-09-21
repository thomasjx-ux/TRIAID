from __future__ import annotations

from typing import Iterable

from .contracts import BilingualText, MarketSnapshot, StrategyGroup, StrategyState, TriaidDecision
from .evolution import CoreParameters


def _normalize_capped(raw: dict[str, float], cap: float = 0.28) -> dict[str, float]:
    if not raw:
        return {}
    weights={k:0.0 for k in raw}
    positive={k:max(0.0,float(v)) for k,v in raw.items()}
    active={k for k,v in positive.items() if v>0 and k!="P28_CASH"}
    remaining=1.0
    while active and remaining>1e-12:
        total=sum(positive[k] for k in active)
        if total<=0:
            break
        tentative={k:remaining*positive[k]/total for k in active}
        capped=[k for k,w in tentative.items() if w>cap]
        if not capped:
            for k,w in tentative.items():
                weights[k]=w
            remaining=0.0
            break
        for k in capped:
            weights[k]=cap
            remaining-=cap
            active.remove(k)
    if "P28_CASH" in raw:
        weights["P28_CASH"]=max(0.0,remaining)
        remaining=0.0
    elif remaining>1e-12 and weights:
        # No explicit cash strategy was supplied. Keep exposure below 100%;
        # the unallocated residual is treated as cash by the evaluation layer.
        pass
    return {k:v for k,v in weights.items() if v>1e-12}


class TriaidCoreModule:
    """Evolvable TRIAID Core boundary.

    Strategy Population decides which strategies are admissible.
    Core decides whether and how strongly to alter the group's baseline weights.
    Parameters are versioned by EvolutionModule and can be replaced without changing
    Market Data, Strategy Population, Evaluation, Review or Audit.
    """

    interface_version = "triaid-core-contract@1"

    def __init__(self, params: CoreParameters) -> None:
        self.params=params
        self.version=params.version

    def decide(
        self,
        market: MarketSnapshot,
        group: StrategyGroup,
        states: Iterable[StrategyState],
    ) -> TriaidDecision:
        before=dict(group.weights)
        state_map={s.strategy_id:s for s in states}
        regime=(market.regime or "").lower()
        risk_off=any(x in regime for x in ("risk_off","stress","bear","shock","high_vol"))
        raw={}
        for strategy_id in group.members:
            s=state_map.get(strategy_id)
            if not s:
                raw[strategy_id]=max(0.0,before.get(strategy_id,0.0))
                continue
            signal=max(
                0.0,
                s.expected_net_return
                - self.params.risk_penalty*max(0.0,s.risk)
                - self.params.uncertainty_penalty*max(0.0,s.uncertainty),
            )
            raw[strategy_id]=signal

        if "P28_CASH" in group.members and "P28_CASH" not in raw:
            raw["P28_CASH"]=0.0

        target=_normalize_capped(raw,0.28)
        if risk_off:
            scale=max(0.0,min(1.0,self.params.risk_off_multiplier))
            for k in list(target):
                if k!="P28_CASH":
                    target[k]*=scale
            if "P28_CASH" in group.members:
                target["P28_CASH"]=max(0.0,1.0-sum(v for k,v in target.items() if k!="P28_CASH"))

        strength=max(0.0,min(1.0,self.params.intervention_strength))
        keys=set(before)|set(target)
        after={k:(1-strength)*before.get(k,0.0)+strength*target.get(k,0.0) for k in keys}
        after={k:max(0.0,v) for k,v in after.items() if v>1e-12}

        reasons={}
        for strategy_id in keys:
            b=before.get(strategy_id,0.0)
            a=after.get(strategy_id,0.0)
            delta=a-b
            if abs(delta)<1e-7:
                zh="当前证据不足以支持改变该策略权重，因此保持基本不变。"
                en="Current evidence does not justify a material weight change, so the allocation is left essentially unchanged."
            elif delta>0:
                zh="在当前策略群内，该策略的多周期年化状态收益估计在扣除风险与不确定性惩罚后形成的相对信号更高，因此 TRIAID 增配。"
                en="Within the current group, the strategy has a stronger relative signal after penalizing its multi-window annualized state-return estimate for risk and uncertainty, so TRIAID increases the allocation."
            else:
                zh="该策略的多周期年化状态收益估计经风险与不确定性惩罚后的相对信号较弱，因此 TRIAID 降低配置。"
                en="The strategy has a weaker relative signal after risk and uncertainty penalties are applied to its multi-window annualized state-return estimate, so TRIAID reduces the allocation."
            reasons[strategy_id]=BilingualText(zh=zh,en=en)

        return TriaidDecision(
            core_version=self.version,
            weights_before=before,
            weights_after=after,
            reasons=reasons,
            diagnostics={
                "interface_version":self.interface_version,
                "risk_penalty":self.params.risk_penalty,
                "uncertainty_penalty":self.params.uncertainty_penalty,
                "intervention_strength":self.params.intervention_strength,
                "risk_off_multiplier":self.params.risk_off_multiplier,
                "risk_off_detected":risk_off,
            },
        )
