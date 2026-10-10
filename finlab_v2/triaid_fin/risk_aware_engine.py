from __future__ import annotations

from .engine import EvolutionLabEngine
from .risk_aware_us import RiskAwareUSReturnMaxRoute


class RiskAwareEvolutionLabEngine(EvolutionLabEngine):
    """EvolutionLabEngine with the canonical internal US prediction/risk route.

    The subclass keeps one shared route for standalone and validation execution so
    H1/H5/H20 isolation, prediction calibration, state-break braking, and exposure
    guards cannot drift into separate implementations.
    """

    risk_overlay_version = "fin-risk-aware-engine@0.2.0"

    def __init__(self) -> None:
        super().__init__()
        self.us_return_max = RiskAwareUSReturnMaxRoute()
