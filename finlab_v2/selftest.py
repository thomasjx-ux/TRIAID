from triaid_fin.contracts import MarketSnapshot, OutcomeRequest, RunRequest, StrategyState
from triaid_fin.engine import EvolutionLabEngine


engine = EvolutionLabEngine()
request = RunRequest(
    market=MarketSnapshot(
        market_id="US",
        as_of="2026-09-20T00:00:00Z",
        snapshot_id="selftest-snapshot-002",
        regime="test",
    ),
    strategy_states=[
        StrategyState(strategy_id="strategy-a", lifecycle="active", expected_net_return=0.02, risk=0.01),
        StrategyState(strategy_id="strategy-b", lifecycle="active", expected_net_return=0.01, risk=0.01),
        StrategyState(strategy_id="strategy-shadow", lifecycle="shadow", expected_net_return=0.50, risk=0.01),
    ],
    max_group_size=3,
)

run = engine.create_run(request)
engine.execute(run.run_id, request)
decision_ready = engine.get_run(run.run_id)
assert decision_ready.status == "DECISION_READY_AWAITING_OUTCOME"
assert decision_ready.strategy_group
assert "strategy-shadow" not in decision_ready.strategy_group.members
assert max(decision_ready.strategy_group.weights.values()) <= 0.2800001

hard_failure = StrategyState(
    strategy_id="strategy-fail",
    lifecycle="active",
    expected_net_return=0.20,
    hard_failure=True,
)
assert engine.strategy_population.recommend_lifecycle(hard_failure, "US") == "frozen"

rules = engine.strategy_population.rules("US")
assert rules["entry_confirm_days"] == 3
assert rules["exit_confirm_days"] == 3
assert rules["cooldown_days"] == 5
assert rules["max_weight"] == 0.28

verified = engine.submit_outcome(
    run.run_id,
    OutcomeRequest(
        realized_returns={"strategy-a": 0.01, "strategy-b": -0.005},
        trading_cost=0.0,
    ),
)
assert verified.status == "VERIFIED"
assert verified.audit and verified.audit.passed
assert verified.evaluation and verified.evaluation.status == "EVALUATED"
assert abs((verified.evaluation.excess_return or 0.0)) < 1e-12
assert len(engine.curves()) == 1
assert engine.daily_summary()["evaluated_runs"] == 1

print("TRIAID_FIN_V2_SELFTEST_PASS")
print(engine.module_manifest)
print(engine.strategy_population.rules("US"))
