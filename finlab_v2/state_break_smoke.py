from __future__ import annotations

from triaid_fin.contracts import BilingualText, MarketSnapshot, StrategyGroup, StrategyState
from triaid_fin.core import TriaidCoreModule
from triaid_fin.evolution import CoreParameters
from triaid_fin.state_break import assess_state_break


def state(strategy_id: str, returns: list[float], expected: float, risk: float = 0.16) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        lifecycle="active",
        expected_net_return=expected,
        risk=risk,
        uncertainty=0.02,
        recent_returns=returns,
    )


def main() -> None:
    stable = [0.0008, -0.0004, 0.0006, 0.0002, -0.0001] * 16
    shock = stable[:-3] + [-0.012, -0.018, -0.026]
    states = [
        state("P00_BUY_HOLD", shock, 0.16),
        state("P20_XMOM126", shock, 0.30, 0.20),
        state("P21_XMOM252", shock, 0.24, 0.18),
        state("P27_BREADTH_ROT", shock, 0.18, 0.15),
        state("P28_CASH", [0.0] * len(shock), 0.0, 0.0),
    ]
    assessment = assess_state_break(states, "risk_on_trend")
    assert assessment.brake_factor < 1.0, assessment
    assert assessment.permission == "RISK_REDUCTION_ONLY", assessment

    group = StrategyGroup(
        group_version="smoke",
        config_version="smoke",
        market_id="US",
        members=["P00_BUY_HOLD", "P20_XMOM126", "P21_XMOM252", "P27_BREADTH_ROT", "P28_CASH"],
        weights={
            "P00_BUY_HOLD": 0.18,
            "P20_XMOM126": 0.28,
            "P21_XMOM252": 0.26,
            "P27_BREADTH_ROT": 0.18,
            "P28_CASH": 0.10,
        },
        reasons={
            sid: BilingualText(zh="smoke", en="smoke")
            for sid in ["P00_BUY_HOLD", "P20_XMOM126", "P21_XMOM252", "P27_BREADTH_ROT", "P28_CASH"]
        },
        diagnostics={"max_strategy_weight_constraint": 0.28},
    )
    market = MarketSnapshot(
        market_id="US",
        as_of="2026-10-08",
        snapshot_id="US:smoke",
        regime="risk_on_trend",
        metadata={"account_risk_budget": 1.0},
    )
    decision = TriaidCoreModule(CoreParameters(version="smoke-core", intervention_strength=1.0)).decide(
        market,
        group,
        states,
    )
    risky = sum(weight for sid, weight in decision.weights_after.items() if sid != "P28_CASH")
    assert risky <= assessment.brake_factor + 1e-9, (risky, assessment)
    assert decision.diagnostics["fast_brake_applied"] is True
    assert decision.diagnostics["state_break"]["permission"] == "RISK_REDUCTION_ONLY"

    calm_states = [
        state("P00_BUY_HOLD", stable, 0.16),
        state("P20_XMOM126", stable, 0.30, 0.20),
        state("P21_XMOM252", stable, 0.24, 0.18),
        state("P27_BREADTH_ROT", stable, 0.18, 0.15),
    ]
    calm = assess_state_break(calm_states, "risk_on_trend")
    assert calm.brake_factor == 1.0, calm
    print("STATE_BREAK_SMOKE_OK", assessment.to_dict(), calm.to_dict())


if __name__ == "__main__":
    main()
