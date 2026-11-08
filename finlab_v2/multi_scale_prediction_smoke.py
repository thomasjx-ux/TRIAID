from __future__ import annotations

from triaid_fin.contracts import StrategyState
from triaid_fin.multi_scale_prediction import (
    DEFAULT_HORIZONS,
    build_multiscale_prediction,
)


def _state(
    strategy_id: str,
    *,
    expected: float,
    recent: list[float],
    risk: float = 0.02,
    uncertainty: float = 0.01,
) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        expected_net_return=expected,
        estimated_cost=0.001,
        risk=risk,
        uncertainty=uncertainty,
        recent_returns=recent,
        metrics={},
    )


def main() -> None:
    current = [
        _state("P00", expected=0.018, recent=[-0.020, -0.010, 0.005, 0.012, 0.020]),
        _state("P01", expected=0.014, recent=[-0.015, -0.004, 0.003, 0.010, 0.016]),
        _state("P02", expected=0.010, recent=[-0.008, -0.002, 0.002, 0.007, 0.011]),
    ]
    previous = [
        _state("P00", expected=0.010, recent=[-0.030, -0.025, -0.020, -0.015, -0.010]),
        _state("P01", expected=0.008, recent=[-0.020, -0.018, -0.015, -0.010, -0.008]),
        _state("P02", expected=0.006, recent=[-0.015, -0.012, -0.010, -0.007, -0.004]),
    ]

    prior = build_multiscale_prediction("US", previous)
    bundle = build_multiscale_prediction("US", current, previous=prior)

    assert tuple(bundle.horizons) == DEFAULT_HORIZONS
    assert [row.horizon_days for row in bundle.states] == [1, 5, 20]
    assert all(row.strategy_count == 3 for row in bundle.states)
    assert all(0.0 <= row.confidence <= 1.0 for row in bundle.states)
    assert all(0.0 <= row.support_ratio <= 1.0 for row in bundle.states)

    abstractions = [row.abstraction_ratio for row in bundle.states]
    assert abstractions == sorted(abstractions)
    recent_weights = [row.recent_weight for row in bundle.states]
    assert recent_weights == sorted(recent_weights, reverse=True)

    assert all(row.status == "OBSERVED" for row in bundle.transitions)
    assert any(row.direction == "IMPROVING" for row in bundle.transitions)
    assert 0.0 <= bundle.certification.score <= 1.0
    assert 0.0 <= bundle.certification.timescale_separation_score <= 1.0
    assert 1 <= bundle.certification.active_layers <= 3

    payload = bundle.to_dict()
    round_trip = build_multiscale_prediction("US", current, previous=payload)
    assert all(row.status == "OBSERVED" for row in round_trip.transitions)

    try:
        build_multiscale_prediction("US", current, horizons=(5, 1))
    except ValueError:
        pass
    else:
        raise AssertionError("unsorted horizons must fail closed")

    try:
        build_multiscale_prediction("US", current, horizons=(1, 1, 5))
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate horizons must fail closed")

    empty = build_multiscale_prediction("US", [])
    assert not empty.certification.passed
    assert all(row.state_label == "UNOBSERVED" for row in empty.states)

    print("MULTI_SCALE_PREDICTION_SMOKE_OK")


if __name__ == "__main__":
    main()
