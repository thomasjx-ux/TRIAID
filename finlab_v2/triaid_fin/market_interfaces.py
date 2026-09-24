from __future__ import annotations

from .market_contracts import (
    COMMON_RESERVED,
    MarketInterfaceProfile,
    MarketInterfaceRegistry,
    ProductCapabilitySpec,
    RouteContract,
    RouteProjectionSpec,
)
from .market_profiles import builtin_profiles


MARKET_INTERFACE_REGISTRY=MarketInterfaceRegistry()

for _profile in builtin_profiles():
    MARKET_INTERFACE_REGISTRY.register(_profile)


def market_interface(market_id: str) -> MarketInterfaceProfile:
    return MARKET_INTERFACE_REGISTRY.get(market_id)


__all__=[
    "COMMON_RESERVED",
    "MarketInterfaceProfile",
    "MarketInterfaceRegistry",
    "ProductCapabilitySpec",
    "RouteContract",
    "RouteProjectionSpec",
    "MARKET_INTERFACE_REGISTRY",
    "market_interface",
]
