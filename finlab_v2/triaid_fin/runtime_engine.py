from __future__ import annotations

from .engine import EvolutionLabEngine
from .us_route_guard import StateAwareUSReturnMaxRoute


VERSION = "state-aware-runtime-engine@0.1.0"


class StateAwareEvolutionLabEngine(EvolutionLabEngine):
    """Runtime wiring for the canonical FIN engine plus guarded US primary route.

    The common EvolutionLabEngine remains the single source for market data,
    strategy population, core decisions, audit, review, persistence and reports.
    This subclass only swaps the US market-specific realizability adapter after
    base initialization, so cloud and standalone can share the same optimized
    route without forking the common core.
    """

    runtime_engine_version = VERSION

    def __init__(self) -> None:
        super().__init__()
        self.us_return_max = StateAwareUSReturnMaxRoute()
