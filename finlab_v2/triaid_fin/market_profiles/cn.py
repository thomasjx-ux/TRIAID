from __future__ import annotations

from ..market_contracts import (
    COMMON_RESERVED,
    MarketInterfaceProfile,
    ProductCapabilitySpec,
    RouteProjectionSpec,
)


CN_ROUTE=RouteProjectionSpec(
    daily_fields=(
        ("recovery_wave","recovery_wave"),
        ("prospective_experiment","prospective_experiment"),
        ("prospective_experiment_status","prospective_experiment_status"),
    ),
    payload_mode="MAPPING",
    primary_output_key="recovery_wave",
    source="cn_return_max_projection",
    missing_reason="NO_CURRENT_CN_RECOVERY_WAVE_DECISION",
    posterior_kind="RUN_EVALUATION",
)


def build_profile()->MarketInterfaceProfile:
    return MarketInterfaceProfile(
        market_id="CN",
        provider_chains={
            "DAILY":("tencent_equity_primary","yahoo_bars"),
            "INTRADAY":("tencent_equity_primary","yahoo_bars"),
            "REALTIME":("tencent_equity_primary","yahoo_bars"),
            "PREOPEN":("tushare_cn_auction",),
        },
        route=CN_ROUTE,
        instrument_labels={
            "510300.SS":"沪深300ETF · 510300",
            "510500.SS":"中证500ETF · 510500",
            "159915.SZ":"创业板ETF · 159915",
            "512100.SS":"中证1000ETF · 512100",
            "511010.SS":"国债ETF · 511010",
        },
        min_aligned_points={"PREOPEN":1},
        auction_shadow_provider_name="tencent_equity_primary",
        product_capabilities={
            "BAR_DAILY":ProductCapabilitySpec(route_mode="DAILY",grade="research"),
            "BAR_INTRADAY":ProductCapabilitySpec(route_mode="INTRADAY",grade="research"),
            "QUOTE_L1":ProductCapabilitySpec(
                grade="unavailable",
                unavailable_grade="unavailable",
                applicable=False,
            ),
            "PREOPEN_EXTENDED":ProductCapabilitySpec(
                grade="not_applicable",
                unavailable_grade="not_applicable",
                applicable=False,
            ),
            "PREOPEN_AUCTION":ProductCapabilitySpec(
                route_mode="PREOPEN",
                grade="research_auction_final",
                unavailable_grade="credentials_required",
                note="Official opening-auction final snapshot after 09:25 when the configured provider entitlement is available.",
            ),
            "STOCK_BARS":ProductCapabilitySpec(
                route_mode="DAILY",
                grade="research_on_demand",
                note="Research bars use the market route chain and are not execution-grade.",
            ),
            **COMMON_RESERVED,
        },
        runtime_jobs=("CN_PREOPEN_AUCTION_SHADOW",),
    )
