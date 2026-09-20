from __future__ import annotations

from copy import deepcopy

from .contracts import StrategyState
from .store import RunStore
from .strategy_population import StrategyPopulationModule
from .strategy_registry import POLICY_IDS


class PopulationStateTracker:
    version="population-state@0.1.0"

    def __init__(self,store:RunStore,population:StrategyPopulationModule) -> None:
        self.store=store
        self.population=population
        raw=store.load_json("population_state.json",default={})
        self.state=raw if raw else {"markets":{}}

    def _market(self,market_id:str) -> dict:
        key=market_id.upper()
        if key not in self.state["markets"]:
            # The audited 007 bank is migrated as the established incumbent universe.
            self.state["markets"][key]={
                pid:{
                    "lifecycle":"active",
                    "positive_streak":0,
                    "negative_streak":0,
                    "cooldown_remaining":0,
                    "observations":0,
                    "last_expected_net_return":None,
                }
                for pid in POLICY_IDS
            }
        return self.state["markets"][key]

    def apply(self,market_id:str,raw_states:list[StrategyState]) -> list[StrategyState]:
        cfg=self.population.config_for(market_id)
        memory=self._market(market_id)
        out=[]
        for state in raw_states:
            m=memory.setdefault(
                state.strategy_id,
                {
                    "lifecycle":"candidate",
                    "positive_streak":0,
                    "negative_streak":0,
                    "cooldown_remaining":0,
                    "observations":0,
                    "last_expected_net_return":None,
                },
            )
            m["observations"]+=1
            positive=(
                state.eligible
                and not state.hard_failure
                and state.expected_net_return>0
                and state.risk_ok
                and state.capacity_ok
                and state.liquidity_ok
            )
            if positive:
                m["positive_streak"]+=1
                m["negative_streak"]=0
            else:
                m["negative_streak"]+=1
                m["positive_streak"]=0

            if m["cooldown_remaining"]>0:
                m["cooldown_remaining"]-=1

            lifecycle=m["lifecycle"]
            if state.hard_failure:
                lifecycle="frozen"
                m["cooldown_remaining"]=cfg.cooldown_days
            elif lifecycle=="candidate" and m["positive_streak"]>=cfg.entry_confirm_days:
                lifecycle="shadow"
            elif lifecycle=="shadow" and m["positive_streak"]>=cfg.entry_confirm_days*2:
                lifecycle="active"
            elif lifecycle=="active" and m["negative_streak"]>=cfg.exit_confirm_days:
                lifecycle="reduced"
            elif lifecycle=="reduced" and m["negative_streak"]>=cfg.exit_confirm_days*2:
                lifecycle="frozen"
                m["cooldown_remaining"]=cfg.cooldown_days
            elif lifecycle=="reduced" and m["positive_streak"]>=cfg.entry_confirm_days:
                lifecycle="active"
            elif lifecycle=="frozen" and m["cooldown_remaining"]<=0 and m["positive_streak"]>=cfg.entry_confirm_days:
                lifecycle="candidate"
            elif lifecycle=="retired" and state.new_evidence_pass and positive:
                lifecycle="candidate"

            m["lifecycle"]=lifecycle
            m["last_expected_net_return"]=state.expected_net_return
            s=state.model_copy(deep=True)
            s.lifecycle=lifecycle
            s.evidence_days=max(s.evidence_days,m["observations"])
            s.independent_decisions=max(s.independent_decisions,m["observations"])
            s.horizon_multiples=max(s.horizon_multiples,m["observations"]/max(1,cfg.review_windows[0]))
            s.oos_marginal_value=s.expected_net_return
            s.shadow_evidence_pass=(lifecycle in {"active","reduced"} or m["positive_streak"]>=cfg.entry_confirm_days*2)
            out.append(s)

        self.store.save_json("population_state.json",self.state)
        return out

    def status(self,market_id:str|None=None) -> dict:
        if market_id:
            return deepcopy(self._market(market_id))
        return deepcopy(self.state)
