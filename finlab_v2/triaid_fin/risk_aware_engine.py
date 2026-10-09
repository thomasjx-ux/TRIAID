from __future__ import annotations

from .engine import EvolutionLabEngine
from .risk_aware_us import RiskAwareUSReturnMaxRoute


class RiskAwareEvolutionLabEngine(EvolutionLabEngine):
    """EvolutionLabEngine with the canonical risk-aware US route enabled.

    This subclass exists so cloud validation and standalone execution can share
    one risk overlay without forking the base engine implementation.
    """

    risk_overlay_version = "fin-risk-aware-engine@0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self.us_return_max = RiskAwareUSReturnMaxRoute()
