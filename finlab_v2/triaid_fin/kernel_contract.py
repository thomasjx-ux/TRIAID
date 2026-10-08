from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from .contracts import MarketSnapshot, StrategyGroup, StrategyState, TriaidDecision


CORE_KERNEL_CONTRACT_VERSION = "triaid-fin-kernel-contract@1.0.0"


@dataclass
class CoreParameters:
    """Stable parameter object crossing the kernel boundary.

    This type deliberately has no persistence, provider, runtime, market-route,
    UI, or desktop dependency. Evolution may create and persist instances, but
    the decision kernel only consumes this value object.
    """

    version: str
    risk_penalty: float = 0.25
    uncertainty_penalty: float = 1.0
    intervention_strength: float = 0.55
    risk_off_multiplier: float = 0.55
    status: str = "active"
    parent_version: str | None = None
    hypothesis: str | None = None


@runtime_checkable
class CoreDecisionPort(Protocol):
    """Minimal stable interface exposed by the inner decision kernel."""

    interface_version: str
    version: str
    params: CoreParameters

    def decide(
        self,
        market: MarketSnapshot,
        group: StrategyGroup,
        states: Iterable[StrategyState],
    ) -> TriaidDecision:
        ...
