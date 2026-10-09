from __future__ import annotations

"""Risk-aware cloud entrypoint for TRIAID FIN.

This module injects the shared RiskAwareEvolutionLabEngine before importing the
existing FastAPI application. It avoids forking the large UI/API surface while
letting cloud and standalone runtimes use the same FIN kernel overlay.
"""

from triaid_fin import engine as engine_module
from triaid_fin.risk_aware_engine import RiskAwareEvolutionLabEngine

engine_module.EvolutionLabEngine = RiskAwareEvolutionLabEngine

from app import app  # noqa: E402,F401
