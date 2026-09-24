from __future__ import annotations


class UiReadServices:
    """Stable read-only application port for UI projections.

    UI projection modules consume this facade instead of EvolutionLabEngine
    internals. The adapter is the only UI-side component allowed to know the
    current engine object graph.
    """

    version="ui-read-services@1.0.0"

    def __init__(self,engine)->None:
        self._engine=engine

    @property
    def core_version(self)->str:
        return str(self._engine.core.version)

    @property
    def architecture_version(self)->str:
        return str(self._engine.architecture_version)

    def daily_summary(self,market_id:str,compact:bool=True)->dict:
        return self._engine.daily_summary(market_id,compact=compact)

    def strategy_cards(self,lang:str,market_id:str)->list[dict]:
        return self._engine.strategy_population.strategy_cards(lang,market_id)

    def get_run(self,run_id:str):
        return self._engine.get_run(run_id)

    def latest_decision_run(self,market_id:str):
        return self._engine.latest_decision_run(market_id)

    def all_runs(self):
        return self._engine.all_runs()

    def evidence_eligible_run(self,run)->bool:
        return bool(self._engine._evidence_eligible_run(run))

    def curves(self,market_id:str)->list[dict]:
        return self._engine.curves(market_id)

    def evolution_status(self)->dict:
        return self._engine.evolution_status()

    def risk_warning_latest(self)->dict|None:
        return self._engine.risk_warning_latest()

    def risk_control_latest(self)->dict|None:
        return self._engine.risk_control_latest()
