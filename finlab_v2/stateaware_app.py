from __future__ import annotations

# app.py constructs EvolutionLabEngine at import time. Replace that runtime factory
# before importing app so Railway and the single-machine runtime share the same
# guarded US route while all common engine modules remain unchanged.
import triaid_fin.engine as engine_module
from triaid_fin.runtime_engine import StateAwareEvolutionLabEngine

engine_module.EvolutionLabEngine = StateAwareEvolutionLabEngine

from app import app  # noqa: E402,F401
