from __future__ import annotations

from triaid_fin.engine import EvolutionLabEngine

engine=EvolutionLabEngine()
run=engine.create_pending_live_run("HK","MANUAL_PREVIEW")
engine.execute_live(run.run_id,"HK","MANUAL_PREVIEW")
done=engine.get_run(run.run_id)

assert done.status=="PREVIEW_READY",done.diagnostic_summary
assert done.market.market_id=="HK"
assert done.market.metadata.get("experiment_mode")=="HK_RETURN_MAX_CAPACITY"
assert done.market.metadata.get("market_route")=="HK_RETURN_MAXIMIZATION"
assert done.market.metadata.get("broker_execution_enabled") is False
assert done.strategy_group is not None
assert done.strategy_group.market_id=="HK"
assert done.triaid_decision is not None
assert len(done.strategy_states)==29
assert done.market.metadata.get("hk_tradable_universe")==["2800.HK","2828.HK","3033.HK","2819.HK"]
assert sum(done.strategy_group.weights.values())<=1.0000001
assert sum(done.triaid_decision.weights_after.values())<=1.0000001

ranked=sorted(
    done.triaid_decision.weights_after.items(),
    key=lambda x:(-float(x[1]),x[0]),
)
print("TRIAID_HK_STRATEGY_LIVE_PASS",{
    "run_id":done.run_id,
    "as_of":done.market.as_of,
    "regime":done.market.regime,
    "strategy_rules":done.market.metadata.get("strategy_rules_version"),
    "strategy_states":len(done.strategy_states),
    "group_members":len(done.strategy_group.members),
    "top_weights":ranked[:5],
    "experiment_mode":done.market.metadata.get("experiment_mode"),
})
