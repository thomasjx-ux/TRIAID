from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .market_registry import normalize_market_id


@dataclass(frozen=True)
class RouteContract:
    require_decision_id: bool = False
    require_frozen_at: bool = False
    strategy_weights_field: str | None = None
    extra_weight_fields: tuple[str, ...] = ()
    min_asset_weights: int = 0
    decision_numeric_fields: tuple[str, ...] = ()
    capital_numeric_fields: tuple[str, ...] = ()
    capital_sleeves: int | None = None
    sleeve_numeric_fields: tuple[str, ...] = ()
    posterior_sleeve_numeric_fields: tuple[str, ...] = ()
    posterior_path_numeric_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteProjectionSpec:
    daily_fields: tuple[tuple[str, str], ...]
    payload_mode: str
    primary_output_key: str
    source: str
    missing_reason: str
    posterior_kind: str
    contract: RouteContract = field(default_factory=RouteContract)

    def __post_init__(self) -> None:
        if self.payload_mode not in {"SINGLE", "MAPPING"}:
            raise ValueError("payload_mode must be SINGLE or MAPPING")
        if self.posterior_kind not in {"ROUTE_REVIEW", "RUN_EVALUATION"}:
            raise ValueError("unsupported posterior_kind")
        if not self.daily_fields:
            raise ValueError("at least one daily field is required")

    def build_payload(self, daily: dict) -> Any:
        mapped={out_key:daily.get(daily_key) for out_key,daily_key in self.daily_fields}
        if self.payload_mode=="SINGLE":
            return mapped[self.primary_output_key]
        return mapped

    def primary_payload(self, payload: Any) -> dict:
        if self.payload_mode=="SINGLE":
            return payload if isinstance(payload,dict) else {}
        if not isinstance(payload,dict):
            return {}
        row=payload.get(self.primary_output_key)
        return row if isinstance(row,dict) else {}


@dataclass(frozen=True)
class ProductCapabilitySpec:
    route_mode: str | None = None
    provider_name: str | None = None
    grade: str = "interface_reserved"
    unavailable_grade: str = "not_connected"
    note: str | None = None
    applicable: bool = True


@dataclass(frozen=True)
class MarketInterfaceProfile:
    market_id: str
    provider_chains: Mapping[str, tuple[str, ...]]
    route: RouteProjectionSpec
    instrument_labels: Mapping[str, str] = field(default_factory=dict)
    optional_sparse_symbols: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    min_aligned_points: Mapping[str, int] = field(default_factory=dict)
    partial_symbol_policy: Mapping[str, str] = field(default_factory=dict)
    auction_shadow_provider_name: str | None = None
    product_capabilities: Mapping[str, ProductCapabilitySpec] = field(default_factory=dict)
    runtime_jobs: tuple[str, ...] = ()

    def chain(self, mode: str) -> tuple[str, ...]:
        return tuple(self.provider_chains.get(str(mode).upper(), ()))

    def optional_symbols(self, mode: str) -> tuple[str, ...]:
        return tuple(self.optional_sparse_symbols.get(str(mode).upper(), ()))

    def aligned_minimum(self, mode: str, default: int) -> int:
        value=self.min_aligned_points.get(str(mode).upper())
        return int(value) if value is not None else int(default)

    def partial_policy(self, mode: str) -> str:
        return str(
            self.partial_symbol_policy.get(
                str(mode).upper(),
                "STRICT_COMPLETE_PANEL",
            )
        )


class MarketInterfaceRegistry:
    version="market-interface-registry@1.0.0"

    def __init__(self) -> None:
        self._profiles: dict[str, MarketInterfaceProfile] = {}

    def register(self, profile: MarketInterfaceProfile, *, replace: bool=False) -> MarketInterfaceProfile:
        market=str(profile.market_id).strip().upper()
        if not market:
            raise ValueError("market_id is required")
        if market in self._profiles and not replace:
            raise ValueError(f"market interface already registered:{market}")
        normalized=MarketInterfaceProfile(
            market_id=market,
            provider_chains=MappingProxyType({
                str(mode).upper():tuple(chain)
                for mode,chain in dict(profile.provider_chains).items()
            }),
            route=profile.route,
            instrument_labels=MappingProxyType(dict(profile.instrument_labels)),
            optional_sparse_symbols=MappingProxyType({
                str(mode).upper():tuple(symbols)
                for mode,symbols in dict(profile.optional_sparse_symbols).items()
            }),
            min_aligned_points=MappingProxyType({
                str(mode).upper():int(value)
                for mode,value in dict(profile.min_aligned_points).items()
            }),
            partial_symbol_policy=MappingProxyType({
                str(mode).upper():str(value)
                for mode,value in dict(profile.partial_symbol_policy).items()
            }),
            auction_shadow_provider_name=profile.auction_shadow_provider_name,
            product_capabilities=MappingProxyType(dict(profile.product_capabilities)),
            runtime_jobs=tuple(profile.runtime_jobs),
        )
        self._profiles[market]=normalized
        return normalized

    def get(self, market_id: str) -> MarketInterfaceProfile:
        market=normalize_market_id(market_id)
        profile=self._profiles.get(market)
        if profile is None:
            raise KeyError(f"market_interface_not_registered:{market}")
        return profile

    def ids(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def mapping(self) -> Mapping[str, MarketInterfaceProfile]:
        return MappingProxyType(self._profiles)


def _reserved(note: str | None=None) -> ProductCapabilitySpec:
    return ProductCapabilitySpec(note=note)


COMMON_RESERVED={
    "ORDERBOOK_L2":_reserved("No L2 order-book provider is connected."),
    "SECTOR_BARS":_reserved("Sector/industry universe provider is not connected yet."),
    "DERIVATIVES_CHAIN":_reserved("Options/futures chain provider is not connected yet."),
    "BROKER_FILLS":ProductCapabilitySpec(
        grade="unavailable",
        unavailable_grade="unavailable",
        note="No broker execution/fill connector is attached.",
        applicable=False,
    ),
}


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


MARKET_INTERFACE_REGISTRY=MarketInterfaceRegistry()

MARKET_INTERFACE_REGISTRY.register(MarketInterfaceProfile(
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
            grade="not_applicable",unavailable_grade="not_applicable",applicable=False
        ),
        "STOCK_BARS":ProductCapabilitySpec(
            route_mode="DAILY",grade="research_on_demand",
            note="Research bars use the market route chain and are not execution-grade.",
        ),
        **COMMON_RESERVED,
    },
    runtime_jobs=("US_POSTCLOSE_RESEARCH",),
))

MARKET_INTERFACE_REGISTRY.register(MarketInterfaceProfile(
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
            grade="unavailable",unavailable_grade="unavailable",applicable=False
        ),
        "PREOPEN_EXTENDED":ProductCapabilitySpec(
            grade="not_applicable",unavailable_grade="not_applicable",applicable=False
        ),
        "PREOPEN_AUCTION":ProductCapabilitySpec(
            route_mode="PREOPEN",
            grade="research_auction_final",
            unavailable_grade="credentials_required",
            note="Official opening-auction final snapshot after 09:25 when the configured provider entitlement is available.",
        ),
        "STOCK_BARS":ProductCapabilitySpec(
            route_mode="DAILY",grade="research_on_demand",
            note="Research bars use the market route chain and are not execution-grade.",
        ),
        **COMMON_RESERVED,
    },
    runtime_jobs=("CN_PREOPEN_AUCTION_SHADOW",),
))

MARKET_INTERFACE_REGISTRY.register(MarketInterfaceProfile(
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
            grade="unavailable",unavailable_grade="unavailable",applicable=False
        ),
        "PREOPEN_EXTENDED":ProductCapabilitySpec(
            grade="not_connected",unavailable_grade="not_connected",applicable=False
        ),
        "PREOPEN_AUCTION":ProductCapabilitySpec(
            grade="interface_reserved",unavailable_grade="interface_reserved",applicable=False
        ),
        "STOCK_BARS":ProductCapabilitySpec(
            route_mode="DAILY",grade="research_on_demand",
            note="Research bars use the market route chain and are not execution-grade.",
        ),
        **COMMON_RESERVED,
    },
))


def market_interface(market_id: str) -> MarketInterfaceProfile:
    return MARKET_INTERFACE_REGISTRY.get(market_id)
