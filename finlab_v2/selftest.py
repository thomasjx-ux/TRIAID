from triaid_fin.contracts import MarketSnapshot, OutcomeRequest, RunRequest, StrategyState
from triaid_fin.engine import EvolutionLabEngine


engine = EvolutionLabEngine()
request = RunRequest(
    market=MarketSnapshot(
        market_id="SELFTEST",
        as_of="2026-09-20T00:00:00Z",
        snapshot_id="selftest-snapshot-001",
        regime="test",
    ),
    strategy_states=[
        StrategyState(strategy_id="strategy-a", expected_net_return=0.02, risk=0.01),
        StrategyState(strategy_id="strategy-b", expected_net_return=0.01, risk=0.01),
    ],
    max_group_size=2,
)
run = engine.create_run(request)
engine.execute(run.run_id, request)
assert engine.get_run(run.run_id).status == "DECISION_READY_AWAITING_OUTCOME"

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
