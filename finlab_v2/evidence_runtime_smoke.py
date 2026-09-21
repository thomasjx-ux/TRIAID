from triaid_fin.engine import EvolutionLabEngine
from triaid_fin.market_runtime import MarketDataAutomation

engine=EvolutionLabEngine()
auto=MarketDataAutomation(engine)
plan=auto.refresh_plan_for_phase("CN","PREOPEN")
assert "PREOPEN" in plan
assert "REALTIME" in plan
status=engine.alpha_evidence_status()
assert status["version"]=="alpha-evidence-ledger@0.1.0"
assert "US" in status and "CN" in status
print("TRIAID_EVIDENCE_RUNTIME_SMOKE_PASS")
