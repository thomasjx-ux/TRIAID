from __future__ import annotations

from triaid_fin.contracts import StrategyState
from triaid_fin.prediction_adjustment import build_market_horizon_state, build_us_prediction_adjustment


def _state(strategy_id: str, expected: float, returns: list[float]) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        lifecycle="active",
        expected_net_return=expected,
        recent_returns=returns,
    )


def main() -> None:
    a = _state("A", 0.20, [0.001] * 19 + [-0.08])
    b = _state("B", 0.10, [0.004] * 20)
    result = build_us_prediction_adjustment(
        [a, b],
        [
            {"strategy_id": "A", "meta_switch_cost_fraction": 0.0},
            {"strategy_id": "B", "meta_switch_cost_fraction": 0.0},
        ],
        1.0,
    )
    assert result["selected_strategy_id"] == "B", result
    assert result["rows"][0]["strategy_id"] == "B", result["rows"]
    assert result["rows"][0]["h1_used_for_selection"] is False

    c = _state("C", 0.25, [0.003] * 20)
    d = _state("D", 0.22, [0.003] * 20)
    cost_result = build_us_prediction_adjustment(
        [c, d],
        [
            {"strategy_id": "C", "meta_switch_cost_fraction": 0.02},
            {"strategy_id": "D", "meta_switch_cost_fraction": 0.0},
        ],
        1.0,
    )
    assert cost_result["selected_strategy_id"] == "D", cost_result

    e = _state("E", 3.0, [0.001] * 19 + [0.50])
    cap_result = build_us_prediction_adjustment(
        [e],
        [{"strategy_id": "E", "meta_switch_cost_fraction": 0.0}],
        1.0,
    )
    h20 = cap_result["rows"][0]["horizons"]["20"]
    assert abs(h20["calibrated_horizon_return"]) <= h20["amplitude_cap_abs_return"] + 1e-12

    class Spec:
        benchmark = "SPY"
        risk_assets = ("SPY", "QQQ")

    class Panel:
        spec = Spec()
        close = {
            "SPY": [100.0 + i * 0.2 for i in range(25)],
            "QQQ": [100.0 + i * 0.25 for i in range(25)],
        }

    horizon_state = build_market_horizon_state(Panel())
    assert horizon_state["horizons"]["H1"]["state"] == "POSITIVE"
    assert horizon_state["horizons"]["H5"]["state"] == "POSITIVE"
    assert horizon_state["horizons"]["H20"]["state"] == "POSITIVE"
    assert all(
        row["independent_from_other_horizons"]
        for row in horizon_state["horizons"].values()
    )

    print("prediction_adjustment_smoke: PASS")


if __name__ == "__main__":
    main()
