from __future__ import annotations

from triaid_fin.product_execution_bridge import build_product_execution_plan


def _capacity() -> dict:
    return {
        "sleeves": [
            {
                "sleeve_id": "USD_100000",
                "starting_capital_usd": 100000.0,
                "capacity_status": "ONE_DAY_WITHIN_PARTICIPATION_CAP",
                "products": [
                    {
                        "symbol": "SPY",
                        "target_notional_usd": 60000.0,
                        "adv20_notional_usd": 1_000_000_000.0,
                        "planned_participation_adv": 0.00006,
                        "minimum_execution_days": 1,
                        "all_in_bps_per_side": 1.8,
                        "estimated_entry_cost_usd": 10.8,
                    },
                    {
                        "symbol": "QQQ",
                        "target_notional_usd": 30000.0,
                        "adv20_notional_usd": 800_000_000.0,
                        "planned_participation_adv": 0.0000375,
                        "minimum_execution_days": 1,
                        "all_in_bps_per_side": 1.7,
                        "estimated_entry_cost_usd": 5.1,
                    },
                ],
            }
        ]
    }


def main() -> None:
    weights = {"SPY": 0.60, "QQQ": 0.30}

    missing = build_product_execution_plan(weights, _capacity(), "OPEN")
    assert missing["status"] == "BLOCKED_QUOTE_VERIFICATION", missing
    assert missing["all_required_quotes_verified"] is False
    assert missing["broker_execution_enabled"] is False
    assert missing["real_order_authorized"] is False

    stale = build_product_execution_plan(
        weights,
        _capacity(),
        "OPEN",
        {
            "SPY": {"price": 700.0, "age_seconds": 901, "source": "test"},
            "QQQ": {"price": 600.0, "age_seconds": 10, "source": "test"},
        },
    )
    assert stale["status"] == "BLOCKED_QUOTE_VERIFICATION", stale
    spy = next(row for row in stale["products"] if row["symbol"] == "SPY")
    assert spy["quote_verification"]["status"] == "STALE"

    fresh = build_product_execution_plan(
        weights,
        _capacity(),
        "OPEN",
        {
            "SPY": {"price": 700.0, "age_seconds": 30, "source": "test"},
            "QQQ": {"price": 600.0, "age_seconds": 20, "source": "test"},
        },
    )
    assert fresh["status"] == "READY_FOR_SIMULATED_EXECUTION", fresh
    assert fresh["all_required_quotes_verified"] is True
    assert fresh["all_capacity_verified"] is True
    assert all(row["product_state"] == "READY" for row in fresh["products"])

    outside = build_product_execution_plan(
        weights,
        _capacity(),
        "POSTCLOSE",
        {
            "SPY": {"price": 700.0, "age_seconds": 30, "source": "test"},
            "QQQ": {"price": 600.0, "age_seconds": 20, "source": "test"},
        },
    )
    assert outside["status"] == "REVIEW_ONLY_OUTSIDE_EXECUTION_SESSION", outside

    cash = build_product_execution_plan({}, _capacity(), "OPEN")
    assert cash["status"] == "NO_RISK_POSITION", cash
    assert cash["products"] == []

    print("product_execution_bridge_smoke: PASS")


if __name__ == "__main__":
    main()
