from __future__ import annotations

from triaid_fin.contracts import BilingualText, MarketSnapshot, StrategyGroup, StrategyState
from triaid_fin.core import TriaidCoreModule
from triaid_fin.evolution import CoreParameters
from triaid_fin.hk_return_max import HKReturnMaxRoute
from triaid_fin.opportunity_state import opportunity_profile
from triaid_fin.prediction_adjustment import build_market_prediction_adjustment
from triaid_fin.recovery_core import RecoveryWaveCore
from triaid_fin.us_return_max import USReturnMaxRoute


def _state(strategy_id: str, expected: float, returns: list[float]) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        lifecycle="active",
        expected_net_return=expected,
        recent_returns=returns,
    )


def _group() -> StrategyGroup:
    reason = BilingualText(zh="test", en="test")
    return StrategyGroup(
        group_version="test-group",
        config_version="test-config",
        market_id="US",
        members=["A", "B", "P28_CASH"],
        weights={"A": 0.50, "B": 0.50},
        reasons={"A": reason, "B": reason, "P28_CASH": reason},
        diagnostics={"max_strategy_weight_constraint": 1.0},
    )


def main() -> None:
    # Primary routes must point at the upgraded implementations, not merely the
    # common helper layer.
    assert USReturnMaxRoute.version == "us-return-max-route@0.7.0"
    assert RecoveryWaveCore.version == "recovery-wave-core@0.5.0"
    assert HKReturnMaxRoute.version == "hk-return-max-route@0.3.0"

    strong = _state("A", 0.30, [0.004] * 20)
    weak = _state("B", -0.10, [-0.001] * 20)
    cash = _state("P28_CASH", 0.0, [0.0] * 20)
    rows = [
        {"strategy_id": "A", "meta_switch_cost_fraction": 0.0},
        {"strategy_id": "B", "meta_switch_cost_fraction": 0.0},
        {"strategy_id": "P28_CASH", "meta_switch_cost_fraction": 0.0},
    ]

    results = {}
    for market_id in ("US", "CN", "HK"):
        result = build_market_prediction_adjustment(
            market_id,
            [strong, weak, cash],
            rows,
            1.0,
            input_phase="POSTCLOSE",
        )
        results[market_id] = result
        assert result["market_id"] == market_id
        assert result["market_opportunity_state"]["market_id"] == market_id
        assert result["market_profile"]["profile_version"] == opportunity_profile(market_id)["profile_version"]
        assert result["horizon_isolation"]["H20"].startswith("PRIMARY_PROMOTION")
        assert result["horizon_isolation"]["H1"].startswith("DIAGNOSTIC_ONLY")
        assert result["selected_strategy_id"] == "A", result

    # The three markets intentionally use different execution-aware gate profiles.
    assert results["US"]["market_profile"]["thresholds"] != results["CN"]["market_profile"]["thresholds"]
    assert results["CN"]["market_profile"]["thresholds"] != results["HK"]["market_profile"]["thresholds"]
    assert results["US"]["target_strategy_weights"]["A"] > results["HK"]["target_strategy_weights"]["A"]
    assert results["HK"]["target_strategy_weights"]["A"] > results["CN"]["target_strategy_weights"]["A"]

    # Core integration must be active for US, CN and HK when 20 completed observations exist.
    core = TriaidCoreModule(CoreParameters(version="test-core", intervention_strength=1.0))
    for market_id in ("US", "CN", "HK"):
        group = _group().model_copy(update={"market_id": market_id})
        market = MarketSnapshot(
            market_id=market_id,
            as_of="2026-10-10",
            snapshot_id=f"{market_id}:test",
            regime="mixed",
            metadata={"session_phase": "POSTCLOSE", "daily_bar_complete": True},
        )
        decision = core.decide(market, group, [strong, weak, cash])
        diag = decision.diagnostics
        assert diag["selection_source"] == "MARKET_PREDICTION_ADJUSTMENT", diag
        assert diag["prediction_adjustment_ready"] is True, diag
        assert str(diag["prediction_adjustment_version"]).startswith(market_id.lower()), diag
        assert diag["market_opportunity_state"]["market_id"] == market_id, diag

    # No Trade is a first-class decision on every market.
    isolated = _state("A", 0.01, [0.0001] * 20)
    neg1 = _state("B", -0.10, [-0.001] * 20)
    neg2 = _state("C", -0.08, [-0.0015] * 20)
    cash2 = _state("P28_CASH", 0.0, [0.0] * 20)
    weak_rows = [
        {"strategy_id": "A", "meta_switch_cost_fraction": 0.0},
        {"strategy_id": "B", "meta_switch_cost_fraction": 0.0},
        {"strategy_id": "C", "meta_switch_cost_fraction": 0.0},
        {"strategy_id": "P28_CASH", "meta_switch_cost_fraction": 0.0},
    ]
    for market_id in ("US", "CN", "HK"):
        result = build_market_prediction_adjustment(
            market_id,
            [isolated, neg1, neg2, cash2],
            weak_rows,
            1.0,
            input_phase="POSTCLOSE",
        )
        assert result["decision_state"] == "NO_TRADE", result
        assert result["target_strategy_weights"] == {"P28_CASH": 1.0}, result

    print("all_market_prediction_gate_smoke: PASS")


if __name__ == "__main__":
    main()
