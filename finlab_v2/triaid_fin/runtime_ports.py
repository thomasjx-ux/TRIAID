from __future__ import annotations

from typing import Any


class RuntimeJournal:
    """Narrow persistence port for runtime coordination state.

    Runtime modules receive this interface rather than reaching through the
    engine to its concrete storage backend. The backing store can therefore be
    replaced without changing scheduler or automation control flow.
    """

    version="runtime-journal@1.0.0"

    def __init__(self,store)->None:
        self._store=store

    def load_json(self,name:str,default=None):
        return self._store.load_json(name,default=default)

    def save_json(self,name:str,payload)->None:
        self._store.save_json(name,payload)

    def append_jsonl(self,name:str,payload)->None:
        self._store.append_jsonl(name,payload)

    def read_jsonl(self,name:str,limit:int|None=None)->list[dict]:
        return self._store.read_jsonl(name,limit=limit)


class RuntimeServices:
    """Stable application port used by runtime orchestration.

    This facade intentionally exposes only orchestration-safe operations.
    Domain modules remain behind EvolutionLabEngine; callers do not depend on
    its internal attributes or concrete storage implementation.
    """

    version="runtime-services@1.0.0"

    def __init__(self,engine)->None:
        self._engine=engine
        self.journal=RuntimeJournal(engine.store)

    @property
    def architecture_version(self)->str:
        return str(getattr(self._engine,"architecture_version",""))

    def market_data_capabilities(self,market_id:str)->dict:
        return self._engine.market_data_capabilities(market_id)

    def refresh_market_data(self,market_id:str,mode:str)->dict:
        return self._engine.refresh_market_data(market_id,mode)

    def market_data_snapshot(self,market_id:str,mode:str,refresh:bool=False)->dict:
        return self._engine.market_data_snapshot(market_id,mode,refresh)

    def record_market_observation(self,snapshot:dict)->dict:
        return self._engine.record_market_observation(snapshot)

    def market_observations(
        self,market_id:str|None=None,mode:str|None=None,limit:int=500
    )->list[dict]:
        return self._engine.market_observations(market_id,mode,limit)

    def market_transitions(
        self,market_id:str|None=None,mode:str|None=None,limit:int=500
    )->list[dict]:
        return self._engine.market_transitions(market_id,mode,limit)

    def market_data_status(self)->dict:
        return self._engine.market_data_status()

    def market_data_auction_shadow_probe(self,market_id:str)->dict:
        return self._engine.market_data_auction_shadow_probe(market_id)

    def refresh_volatility_forecast(self,market_id:str)->dict:
        return self._engine.refresh_volatility_forecast(market_id)

    def latest_decision_run(self,market_id:str):
        return self._engine.latest_decision_run(market_id)

    def run_live_research(self,market_id:str):
        return self._engine.run_live_research(market_id)

    def recompute_transition_research(
        self,market_id:str,transition:dict,mode:str
    )->dict:
        return self._engine.recompute_transition_research(
            market_id,transition,mode
        )

    def get_run(self,run_id:str):
        return self._engine.get_run(run_id)

    def snapshot_signature(self,snapshot:dict)->str:
        return self._engine.observations.snapshot_signature(snapshot)

    def long_cycle_hypothesis_run(self,force:bool=False)->dict:
        return self._engine.long_cycle_hypothesis_run(force)

    def cross_market_crash_run(self,force:bool=False)->dict:
        return self._engine.cross_market_crash_run(force)

    def latent_hazard_run(self,force:bool=False)->dict:
        return self._engine.latent_hazard_run(force)

    def policy_curve_run(self,force:bool=False)->dict:
        return self._engine.policy_curve_run(force)

    def hazard_prospective_freeze(
        self,latent:dict,policy:dict|None
    )->dict:
        return self._engine.hazard_prospective_freeze(latent,policy)

    def hazard_prospective_resolve(self)->dict:
        return self._engine.hazard_prospective_resolve()

    def risk_warning_run(self,force:bool=False)->dict:
        return self._engine.risk_warning_run(force)

    def risk_control_run(self,force:bool=False)->dict:
        return self._engine.risk_control_run(force)
