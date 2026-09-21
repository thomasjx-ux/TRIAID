from __future__ import annotations

from copy import deepcopy

from .contracts import StrategyState
from .store import RunStore
from .strategy_population import StrategyPopulationModule
from .cn_incubator import CN_SHADOW_IDS
from .strategy_registry import POLICY_IDS


class PopulationStateTracker:
    version="population-state@0.4.0"
    min_shadow_days=20

    def __init__(self,store:RunStore,population:StrategyPopulationModule) -> None:
        self.store=store
        self.population=population
        raw=store.load_json("population_state.json",default={})
        self.state=raw if raw else {"markets":{}}
        self.state.setdefault("markets",{})
        self.state.setdefault("last_observation_keys",{})
        prior_version=str(self.state.get("version") or "")
        if prior_version!=self.version:
            # Preserve incumbent lifecycle labels but reset counters that may have
            # advanced on provisional/incomplete daily bars under the legacy protocol.
            for market_rows in self.state["markets"].values():
                for row in market_rows.values():
                    row["positive_streak"]=0
                    row["negative_streak"]=0
                    row["cooldown_remaining"]=0
                    row["shadow_observations"]=0
                    row["shadow_cumulative_return"]=0.0
            self.state["last_observation_keys"]={}
            self.state["evidence_reset_from_version"]=prior_version or "legacy"
            self.state["version"]=self.version
            self.state["migration_pending_persist"]=True

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
                    "shadow_observations":0,
                    "shadow_cumulative_return":0.0,
                }
                for pid in POLICY_IDS
            }
            if key=="CN":
                for pid in CN_SHADOW_IDS:
                    self.state["markets"][key][pid]={
                        "lifecycle":"shadow",
                        "positive_streak":0,
                        "negative_streak":0,
                        "cooldown_remaining":0,
                        "observations":0,
                        "last_expected_net_return":None,
                        "shadow_observations":0,
                    "shadow_cumulative_return":0.0,
                    }
        return self.state["markets"][key]

    def apply(
        self,
        market_id:str,
        raw_states:list[StrategyState],
        observation_key:str|None=None,
        advance_observation:bool=True,
    ) -> list[StrategyState]:
        market_key=market_id.upper()
        cfg=self.population.config_for(market_id)
        memory=self._market(market_id)
        for row in memory.values():
            row.setdefault("shadow_observations",0)
            row.setdefault("shadow_cumulative_return",0.0)
        last_key=self.state["last_observation_keys"].get(market_key)
        advance=bool(advance_observation) and (not observation_key or observation_key!=last_key)
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
                    "shadow_observations":0,
                    "shadow_cumulative_return":0.0,
                },
            )
            lifecycle_at_start=str(m.get("lifecycle") or "candidate")
            if advance:
                m["observations"]+=1
                if lifecycle_at_start=="shadow":
                    m["shadow_observations"]=int(m.get("shadow_observations",0))+1
                    m["shadow_cumulative_return"]=(
                        (1.0+float(m.get("shadow_cumulative_return",0.0)))
                        *(1.0+float(state.metrics.get("latest_return",0.0)))-1.0
                    )
            positive=(
                state.eligible
                and not state.hard_failure
                and state.expected_net_return>0
                and state.risk_ok
                and state.capacity_ok
                and state.liquidity_ok
            )
            if advance:
                if positive:
                    m["positive_streak"]+=1
                    m["negative_streak"]=0
                else:
                    m["negative_streak"]+=1
                    m["positive_streak"]=0

                if m["cooldown_remaining"]>0:
                    m["cooldown_remaining"]-=1

            lifecycle=m["lifecycle"]
            if advance:
                if state.hard_failure:
                    lifecycle="frozen"
                    m["cooldown_remaining"]=cfg.cooldown_days
                elif lifecycle=="candidate" and m["positive_streak"]>=cfg.entry_confirm_days:
                    lifecycle="shadow"
                    m["shadow_observations"]=0
                    m["shadow_cumulative_return"]=0.0
                elif lifecycle=="shadow":
                    if (
                        int(m.get("shadow_observations",0))>=self.min_shadow_days
                        and float(m.get("shadow_cumulative_return",0.0))>0
                        and positive
                    ):
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
            shadow_obs=int(m.get("shadow_observations",0))
            shadow_cum=float(m.get("shadow_cumulative_return",0.0))
            s.oos_marginal_value=shadow_cum if shadow_obs>0 else None
            s.shadow_evidence_pass=(
                lifecycle in {"active","reduced"}
                or (
                    shadow_obs>=self.min_shadow_days
                    and shadow_cum>0
                    and positive
                )
            )
            s.metrics["shadow_live_days"]=float(shadow_obs)
            s.metrics["shadow_cumulative_return"]=shadow_cum
            out.append(s)

        if advance and observation_key:
            self.state["last_observation_keys"][market_key]=observation_key
        self.state.pop("migration_pending_persist",None)
        self.store.save_json("population_state.json",self.state)
        return out

    def preview(
        self,
        market_id:str,
        raw_states:list[StrategyState],
    ) -> list[StrategyState]:
        market_key=market_id.upper()
        existing=deepcopy(self.state.get("markets",{}).get(market_key) or {})
        out=[]
        for state in raw_states:
            row=existing.get(state.strategy_id) or {}
            lifecycle=str(row.get("lifecycle") or state.lifecycle or "candidate")
            observations=int(row.get("observations",0))
            shadow_obs=int(row.get("shadow_observations",0))
            shadow_cum=float(row.get("shadow_cumulative_return",0.0))
            s=state.model_copy(deep=True)
            s.lifecycle=lifecycle
            s.evidence_days=max(s.evidence_days,observations)
            s.independent_decisions=max(s.independent_decisions,observations)
            cfg=self.population.config_for(market_id)
            s.horizon_multiples=max(
                s.horizon_multiples,
                observations/max(1,cfg.review_windows[0]),
            )
            s.oos_marginal_value=shadow_cum if shadow_obs>0 else None
            positive=(
                state.eligible
                and not state.hard_failure
                and state.expected_net_return>0
                and state.risk_ok
                and state.capacity_ok
                and state.liquidity_ok
            )
            s.shadow_evidence_pass=(
                lifecycle in {"active","reduced"}
                or (
                    shadow_obs>=self.min_shadow_days
                    and shadow_cum>0
                    and positive
                )
            )
            s.metrics["shadow_live_days"]=float(shadow_obs)
            s.metrics["shadow_cumulative_return"]=shadow_cum
            out.append(s)
        return out

    def status(self,market_id:str|None=None) -> dict:
        if market_id:
            return deepcopy(self._market(market_id))
        return deepcopy(self.state)
