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
    version="market-interface-registry@2.0.0"

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

    def status(self) -> dict:
        profiles={}
        for market,profile in self._profiles.items():
            route=profile.route
            contract=route.contract
            profiles[market]={
                "provider_chains":{
                    mode:list(chain)
                    for mode,chain in profile.provider_chains.items()
                },
                "route":{
                    "payload_mode":route.payload_mode,
                    "primary_output_key":route.primary_output_key,
                    "daily_fields":[list(row) for row in route.daily_fields],
                    "source":route.source,
                    "missing_reason":route.missing_reason,
                    "posterior_kind":route.posterior_kind,
                    "contract":{
                        "require_decision_id":contract.require_decision_id,
                        "require_frozen_at":contract.require_frozen_at,
                        "strategy_weights_field":contract.strategy_weights_field,
                        "extra_weight_fields":list(contract.extra_weight_fields),
                        "min_asset_weights":contract.min_asset_weights,
                        "decision_numeric_fields":list(contract.decision_numeric_fields),
                        "capital_numeric_fields":list(contract.capital_numeric_fields),
                        "capital_sleeves":contract.capital_sleeves,
                        "sleeve_numeric_fields":list(contract.sleeve_numeric_fields),
                        "posterior_sleeve_numeric_fields":list(contract.posterior_sleeve_numeric_fields),
                        "posterior_path_numeric_fields":list(contract.posterior_path_numeric_fields),
                    },
                },
                "instrument_labels":dict(profile.instrument_labels),
                "optional_sparse_symbols":{
                    mode:list(symbols)
                    for mode,symbols in profile.optional_sparse_symbols.items()
                },
                "min_aligned_points":dict(profile.min_aligned_points),
                "partial_symbol_policy":dict(profile.partial_symbol_policy),
                "auction_shadow_provider_name":profile.auction_shadow_provider_name,
                "runtime_jobs":list(profile.runtime_jobs),
            }
        return {
            "version":self.version,
            "markets":profiles,
        }


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
