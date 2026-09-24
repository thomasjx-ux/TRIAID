from __future__ import annotations

from ..market_contracts import (
    COMMON_RESERVED,
    MarketInterfaceProfile,
    ProductCapabilitySpec,
    RouteContract,
    RouteProjectionSpec,
)


HK_ROUTE=RouteProjectionSpec(
    daily_fields=(("report","hk_return_max"),),
    payload_mode="SINGLE",
    primary_output_key="report",
    source="hk_return_max_ledger",
    missing_reason="NO_FROZEN_HK_RETURN_MAX_DECISION",
    posterior_kind="ROUTE_REVIEW",
    contract=RouteContract(
        require_decision_id=True,
        strategy_weights_field="target_strategy_weights",
        min_asset_weights=4,
        capital_sleeves=4,
    ),
)


def build_profile()->MarketInterfaceProfile:
    return MarketInterfaceProfile(
        market_id="HK",
        provider_chains={
            "DAILY":("tencent_equity_primary","yahoo_bars"),
            "INTRADAY":("tencent_equity_primary","yahoo_bars"),
            "REALTIME":("tencent_equity_primary","yahoo_bars"),
        },
        route=HK_ROUTE,
        instrument_labels={
            "2800.HK":"盈富基金 · 2800.HK",
            "2828.HK":"恒生国企ETF · 2828.HK",
            "3033.HK":"恒生科技ETF · 3033.HK",
            "2819.HK":"香港债券ETF · 2819.HK",
        },
        optional_sparse_symbols={
            "INTRADAY":("2819.HK",),
            "REALTIME":("2819.HK",),
        },
        partial_symbol_policy={
            "INTRADAY":"OPTIONAL_SPARSE_SYMBOL_NO_INTERPOLATION",
            "REALTIME":"OPTIONAL_SPARSE_SYMBOL_NO_INTERPOLATION",
        },
        product_capabilities={
            "BAR_DAILY":ProductCapabilitySpec(route_mode="DAILY",grade="research"),
            "BAR_INTRADAY":ProductCapabilitySpec(route_mode="INTRADAY",grade="research"),
            "QUOTE_L1":ProductCapabilitySpec(
                grade="unavailable",
                unavailable_grade="unavailable",
                applicable=False,
            ),
            "PREOPEN_EXTENDED":ProductCapabilitySpec(
                grade="not_connected",
                unavailable_grade="not_connected",
                applicable=False,
            ),
            "PREOPEN_AUCTION":ProductCapabilitySpec(
                grade="interface_reserved",
                unavailable_grade="interface_reserved",
                applicable=False,
            ),
            "STOCK_BARS":ProductCapabilitySpec(
                route_mode="DAILY",
                grade="research_on_demand",
                note="Research bars use the market route chain and are not execution-grade.",
            ),
            **COMMON_RESERVED,
        },
    )
