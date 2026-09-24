from __future__ import annotations

from ..market_contracts import (
    COMMON_RESERVED,
    MarketInterfaceProfile,
    ProductCapabilitySpec,
    RouteContract,
    RouteProjectionSpec,
)


US_ROUTE=RouteProjectionSpec(
    daily_fields=(("report","us_return_max"),),
    payload_mode="SINGLE",
    primary_output_key="report",
    source="us_return_max_ledger",
    missing_reason="NO_FROZEN_US_RETURN_MAX_DECISION",
    posterior_kind="ROUTE_REVIEW",
    contract=RouteContract(
        require_decision_id=True,
        require_frozen_at=True,
        strategy_weights_field="target_strategy_weights",
        extra_weight_fields=("generic_core_control_weights",),
        min_asset_weights=5,
        decision_numeric_fields=(
            "projected_annualized_expected_net_return",
            "generic_core_projected_annualized_expected_net_return",
            "buy_hold_projected_annualized_expected_net_return",
            "cash_residual_weight",
        ),
        capital_numeric_fields=(
            "max_participation_adv",
            "base_cost_bps",
            "impact_coefficient_bps",
        ),
        capital_sleeves=4,
        sleeve_numeric_fields=(
            "starting_capital_usd",
            "target_invested_notional_usd",
            "max_one_day_participation_adv",
            "minimum_execution_days",
            "estimated_round_trip_cost_proxy_usd",
        ),
        posterior_sleeve_numeric_fields=(
            "starting_capital_usd",
            "fill_ratio",
            "current_equity_usd",
            "current_net_pnl_usd",
            "current_net_return",
            "total_execution_cost_usd",
        ),
        posterior_path_numeric_fields=(
            "return_max_cumulative_return",
            "generic_core_cumulative_return",
            "spy_buy_hold_cumulative_return",
        ),
    ),
)


def build_profile()->MarketInterfaceProfile:
    return MarketInterfaceProfile(
        market_id="US",
        provider_chains={
            "DAILY":("sina_us_primary","yahoo_bars"),
            "INTRADAY":("sina_us_primary","yahoo_bars"),
            "PREOPEN":("yahoo_bars",),
            "REALTIME":("yahoo_bars",),
            "QUOTE_L1":("us_l1_quotes",),
        },
        route=US_ROUTE,
        instrument_labels={
            "SPY":"S&P 500 · SPY",
            "QQQ":"Nasdaq 100 · QQQ",
            "IWM":"Russell 2000 · IWM",
            "TLT":"US Treasury · TLT",
            "GLD":"Gold · GLD",
        },
        product_capabilities={
            "BAR_DAILY":ProductCapabilitySpec(route_mode="DAILY",grade="research"),
            "BAR_INTRADAY":ProductCapabilitySpec(route_mode="INTRADAY",grade="research"),
            "QUOTE_L1":ProductCapabilitySpec(
                route_mode="QUOTE_L1",
                grade="provider_entitlement_dependent",
                unavailable_grade="credentials_required",
                note="Best bid/ask requires provider credentials. This capability does not enable broker execution.",
            ),
            "PREOPEN_EXTENDED":ProductCapabilitySpec(route_mode="PREOPEN",grade="indicative"),
            "PREOPEN_AUCTION":ProductCapabilitySpec(
                grade="not_applicable",
                unavailable_grade="not_applicable",
                applicable=False,
            ),
            "STOCK_BARS":ProductCapabilitySpec(
                route_mode="DAILY",
                grade="research_on_demand",
                note="Research bars use the market route chain and are not execution-grade.",
            ),
            **COMMON_RESERVED,
        },
        runtime_jobs=(
            "LONG_CYCLE_POSTCLOSE",
            "CROSS_MARKET_POSTCLOSE",
            "HAZARD_RESEARCH_POSTCLOSE",
        ),
    )
