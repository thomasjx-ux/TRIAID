from __future__ import annotations

from copy import deepcopy

from triaid_fin.contracts import MarketSnapshot, StrategyGroup, StrategyState, TriaidDecision
from triaid_fin.prospective_experiment import ProspectiveExperimentProtocol


class MemoryStore:
    def __init__(self) -> None:
        self.data={}

    def load_json(self,name,default=None):
        if name not in self.data:
            return deepcopy({} if default is None else default)
        return deepcopy(self.data[name])

    def save_json(self,name,payload):
        self.data[name]=deepcopy(payload)


def state(sid:str,idx:int,shift:float=0.0)->StrategyState:
    recent=[
        0.0002*(idx+1)+0.00005*((j%5)-2)
        for j in range(30)
    ]
    return StrategyState(
        strategy_id=sid,
        eligible=True,
        lifecycle="active",
        expected_net_return=-0.20+0.01*idx+shift,
        risk=0.25-0.01*idx,
        uncertainty=0.04+0.002*idx,
        recent_returns=recent,
    )


store=MemoryStore()
protocol=ProspectiveExperimentProtocol(store)
ids=[f"P{i:02d}_TEST" for i in range(10)]
states=[state(sid,i) for i,sid in enumerate(ids)]
previous=[state(sid,i,shift=-0.01+0.001*i) for i,sid in enumerate(ids)]
weights={sid:0.1 for sid in ids}
weights["P28_CASH"]=0.0
triaid={sid:0.045 for sid in ids}
triaid["P28_CASH"]=0.55

market=MarketSnapshot(
    market_id="CN",
    as_of="2026-09-21",
    snapshot_id="SMOKE:CN:PROSPECTIVE",
    regime="risk_off",
    metadata={"experiment_mode":"CN_WORST_POOL_RESCUE"},
)
group=StrategyGroup(
    group_version="smoke",
    config_version="smoke",
    market_id="CN",
    members=ids+["P28_CASH"],
    weights=weights,
    reasons={},
    diagnostics={"experiment_available":True},
)
decision=TriaidDecision(
    core_version="smoke",
    weights_before=weights,
    weights_after=triaid,
    reasons={},
    diagnostics={},
)

registered=protocol.register(
    run_id="CN-live-prospective-smoke",
    market=market,
    group=group,
    states=states,
    decision=decision,
    horizons=(3,5,10),
    previous_states=previous,
)
assert registered["status"]=="OPEN"
assert registered["design"]["horizons_trading_days"]==[3,5,10]
assert registered["design"]["no_future_information"] is True
assert registered["design"]["no_post_result_retuning"] is True
assert registered["triaid_components"]["transition_component_available"] is True
assert set(registered["control_rankings"])=={
    "CURRENT_EXPECTED_RETURN","MOMENTUM_20","LOW_RISK","TRIAID_STATE_TRANSITION"
}

# Incomplete periods never count toward a horizon.
partial={sid:0.001 for sid in ids[:-1]}
incomplete=protocol.observe_period("2026-09-21",partial)
assert registered["experiment_id"] in incomplete["updated_experiment_ids"]
after_incomplete=protocol.get(registered["experiment_id"])
assert len(after_incomplete["outcomes"])==0
assert after_incomplete["incomplete_observations"]

dates=[
    "2026-09-21","2026-09-22","2026-09-23","2026-09-24","2026-09-25",
    "2026-09-28","2026-09-29","2026-09-30","2026-10-01","2026-10-02",
]
for day_index,day in enumerate(dates):
    realized={
        sid:0.0004*(rank+1)+0.00001*day_index
        for rank,sid in enumerate(ids)
    }
    result=protocol.observe_period(day,realized)
    assert registered["experiment_id"] in result["updated_experiment_ids"]

done=protocol.get(registered["experiment_id"])
assert done["status"]=="COMPLETE"
assert len(done["outcomes"])==10
assert set(done["evaluations"])=={"3","5","10"}
for horizon in ("3","5","10"):
    result=done["evaluations"][horizon]
    assert len(result["actual_ranking"])==10
    assert set(result["ranking_controls"])=={
        "CURRENT_EXPECTED_RETURN","MOMENTUM_20","LOW_RISK","TRIAID_STATE_TRANSITION"
    }
    assert result["portfolio_controls"]["CASH_DEFENSE"]==0.0
    assert "TRIAID_STATIC_MINUS_HOLD_EQUAL" in result["portfolio_controls"]
    triaid_eval=result["ranking_controls"]["TRIAID_STATE_TRANSITION"]
    assert triaid_eval["pairwise_ordering"]["comparable_pairs"]>=0

# Duplicate period cannot overwrite prospective evidence.
before=deepcopy(done)
duplicate=protocol.observe_period(dates[-1],{sid:0.99 for sid in ids})
assert duplicate["updated_experiment_ids"]==[]
after=protocol.get(registered["experiment_id"])
assert after["outcomes"]==before["outcomes"]
assert after["evaluations"]==before["evaluations"]

post_complete=protocol.observe_period("2026-10-05",{sid:0.50 for sid in ids})
assert post_complete["updated_experiment_ids"]==[]
post_complete_state=protocol.get(registered["experiment_id"])
assert post_complete_state["outcomes"]==before["outcomes"]
assert post_complete_state["evaluations"]==before["evaluations"]

# Registration is immutable/idempotent for the same source run.
again=protocol.register(
    run_id="CN-live-prospective-smoke",
    market=market,
    group=group,
    states=states,
    decision=decision,
    horizons=(1,),
    previous_states=[],
)
assert again["design"]["horizons_trading_days"]==[3,5,10]

print("TRIAID_PROSPECTIVE_EXPERIMENT_SMOKE_PASS")
print({
    "protocol":protocol.version,
    "experiment_id":registered["experiment_id"],
    "horizons":registered["design"]["horizons_trading_days"],
    "controls":sorted(registered["control_rankings"]),
})
