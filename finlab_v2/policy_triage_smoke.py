from triaid_fin.contracts import BilingualText, StrategyGroup, StrategyState
from triaid_fin.policy_triage import PolicyTriageModule, VERSION


def state(strategy_id, expected, risk=0.1, uncertainty=0.02, recent=None):
    return StrategyState(
        strategy_id=strategy_id,
        lifecycle="active",
        expected_net_return=expected,
        risk=risk,
        uncertainty=uncertainty,
        recent_returns=list(recent or []),
        selection_reason=BilingualText(zh=strategy_id,en=strategy_id),
    )


triage=PolicyTriageModule()
states=[
    state("P01_VOL10",0.05),
    state("P06_DUAL_TREND",0.04),
    state("P24_DEFENSIVE_ROT",0.035),
    state("P22_LOWVOL63",0.03),
    state("P27_BREADTH_ROT",0.025),
]
group=StrategyGroup(
    group_version="g",
    config_version="c",
    market_id="CN",
    members=["P01_VOL10","P06_DUAL_TREND"],
    weights={"P01_VOL10":0.5,"P06_DUAL_TREND":0.5},
    reasons={
        "P01_VOL10":BilingualText(zh="x",en="x"),
        "P06_DUAL_TREND":BilingualText(zh="x",en="x"),
    },
)
snap=triage.snapshot("CN","risk_off",states,group)
assert snap["version"]==VERSION
assert snap["mode"]=="SHADOW_ONLY_NO_WEIGHT_EFFECT"
assert snap["no_hindsight_contamination"] is True
network=snap["policy_network"]
assert network["selected_count"]==2
assert any(x["strategy_id"]=="P24_DEFENSIVE_ROT" for x in network["challengers"])
assert any(x["strategy_id"]=="P27_BREADTH_ROT" for x in network["challengers"])
assert any(x["strategy_id"]=="P22_LOWVOL63" for x in network["redundant"])
assert snap["policy_chain"]["stages"]==["STATE","TRIAGE","SELECTION","TRANSITION","INTERVENTION","OUTCOME"]
assert snap["promotion_discipline"]["same_day_challenger_promotion_allowed"] is False

out=triage.evaluate_outcome(
    snap,
    {
        "P01_VOL10":-0.01,
        "P06_DUAL_TREND":-0.005,
        "P24_DEFENSIVE_ROT":0.002,
        "P22_LOWVOL63":0.001,
        "P27_BREADTH_ROT":0.003,
    },
    baseline_return=-0.008,
)
assert out["challengers_evaluated"]>=2
assert out["challengers_beating_baseline"]>=2
assert out["challengers_beating_selected_mean"]>=2
assert out["mode"]=="POSTERIOR_DIAGNOSTIC_ONLY"
print("TRIAID_POLICY_TRIAGE_SMOKE_PASS")
