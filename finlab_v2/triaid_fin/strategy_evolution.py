from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Iterable

from .contracts import RunRecord
from .store import RunStore
from .strategy_population import StrategyPopulationModule


@dataclass
class StrategyRuleProfile:
    version: str
    market_id: str
    window_weights: tuple[float, float, float, float]
    max_group_size: int
    max_weight: float
    entry_confirm_days: int
    exit_confirm_days: int
    cooldown_days: int
    near_duplicate_corr: float = 0.995
    family_cap: int = 3
    redundancy_penalty: float = 0.35
    uncertainty_penalty: float = 0.50
    switch_hurdle_bps: float = 5.0
    switch_uncertainty_fraction: float = 0.25
    switch_guard_enabled: bool = True
    status: str = "active"
    parent_version: str | None = None
    hypothesis: str | None = None


def _seed_profile(market_id: str) -> StrategyRuleProfile:
    market_id=market_id.upper()
    if market_id=="CN":
        return StrategyRuleProfile(
            version="strategy-rules-cn@0.3.0",
            market_id="CN",
            window_weights=(0.35,0.30,0.20,0.15),
            max_group_size=12,
            max_weight=0.28,
            entry_confirm_days=5,
            exit_confirm_days=3,
            cooldown_days=10,
            near_duplicate_corr=1.01,
            family_cap=12,
            redundancy_penalty=0.0,
            uncertainty_penalty=0.0,
            switch_hurdle_bps=0.0,
            switch_uncertainty_fraction=0.0,
            switch_guard_enabled=True,
        )
    return StrategyRuleProfile(
        version="strategy-rules-us@0.3.0",
        market_id="US",
        window_weights=(0.35,0.30,0.20,0.15),
        max_group_size=12,
        max_weight=0.28,
        entry_confirm_days=3,
        exit_confirm_days=3,
        cooldown_days=5,
        near_duplicate_corr=1.01,
        family_cap=12,
        redundancy_penalty=0.0,
        uncertainty_penalty=0.0,
        switch_hurdle_bps=0.0,
        switch_uncertainty_fraction=0.0,
        switch_guard_enabled=False,
    )


class StrategyEvolutionModule:
    """Independent self-evolution loop for Strategy Population rules."""

    version="strategy-evolution@0.3.0"
    min_verified_runs=20

    def __init__(self,store:RunStore) -> None:
        self.store=store
        raw=store.load_json("strategy_evolution.json",default={})
        if not raw:
            raw={"markets":{}}
        for market_id in ("US","CN"):
            if market_id not in raw.setdefault("markets",{}):
                seed=_seed_profile(market_id)
                raw["markets"][market_id]={
                    "active_version":seed.version,
                    "profiles":{seed.version:asdict(seed)},
                    "history":[],
                }
            else:
                self._migrate_market(raw["markets"][market_id],market_id)
        self.state=raw
        self._save()

    def _defaults(self,market_id:str)->dict:
        return asdict(_seed_profile(market_id))

    def _migrate_market(self,m:dict,market_id:str)->None:
        defaults=self._defaults(market_id)
        for version,p in list(m.get("profiles",{}).items()):
            for key,value in defaults.items():
                if key not in p and key not in {"version","status","parent_version","hypothesis"}:
                    p[key]=value
            p["market_id"]=market_id
        active=m.get("active_version")
        if active not in m.get("profiles",{}):
            seed=_seed_profile(market_id)
            m["active_version"]=seed.version
            m.setdefault("profiles",{})[seed.version]=asdict(seed)
        m.setdefault("history",[])

    def _market(self,market_id:str)->dict:
        key=market_id.upper()
        if key not in self.state["markets"]:
            seed=_seed_profile(key)
            self.state["markets"][key]={
                "active_version":seed.version,
                "profiles":{seed.version:asdict(seed)},
                "history":[],
            }
            self._save()
        self._migrate_market(self.state["markets"][key],key)
        return self.state["markets"][key]

    def _save(self)->None:
        self.store.save_json("strategy_evolution.json",self.state)

    def active(self,market_id:str)->StrategyRuleProfile:
        m=self._market(market_id)
        return StrategyRuleProfile(**m["profiles"][m["active_version"]])

    def status(self,market_id:str|None=None)->dict:
        if market_id:
            return self._market(market_id)
        return self.state

    def _simulate_profile(self,runs:list[RunRecord],profile:StrategyRuleProfile)->list[float]:
        population=StrategyPopulationModule()
        population.configure_market(profile)
        previous_group=None
        returns=[]
        for run in sorted(runs,key=lambda r:(r.market.as_of,r.created_at)):
            if not run.evaluation or run.evaluation.status!="EVALUATED":
                continue
            group=population.select(
                profile.market_id,
                run.strategy_states,
                profile.max_group_size,
                previous_group=previous_group,
                base_cost_bps=float(run.market.metadata.get("base_cost_bps",2.0) or 2.0),
            )
            realized=run.evaluation.strategy_realized_returns
            gross=sum(group.weights.get(k,0.0)*float(realized.get(k,0.0)) for k in group.weights)
            if previous_group is None:
                turnover=sum(abs(v) for k,v in group.weights.items() if k!="P28_CASH")
            else:
                turnover=sum(abs(group.weights.get(k,0.0)-previous_group.weights.get(k,0.0)) for k in set(group.weights)|set(previous_group.weights))
            bps=float(run.market.metadata.get("base_cost_bps",2.0) or 2.0)
            net=gross-turnover*bps/10000.0
            returns.append(net)
            previous_group=group
        return returns

    def diagnose(self,market_id:str,runs:Iterable[RunRecord])->dict:
        rows=[
            r for r in runs
            if r.market.market_id.upper()==market_id.upper()
            and r.evaluation and r.evaluation.status=="EVALUATED"
        ]
        if not rows:
            return {
                "evaluated_runs":0,
                "mean_strategy_group_return":None,
                "positive_rate":None,
                "oracle_best_single_mean":None,
                "selection_headroom":None,
            }
        baseline=[float(r.evaluation.baseline_return or 0.0) for r in rows]
        oracle=[]
        for r in rows:
            vals=list(r.evaluation.strategy_realized_returns.values())
            oracle.append(max(vals) if vals else 0.0)
        return {
            "evaluated_runs":len(rows),
            "mean_strategy_group_return":mean(baseline),
            "positive_rate":sum(1 for x in baseline if x>0)/len(baseline),
            "oracle_best_single_mean":mean(oracle),
            "selection_headroom":mean(o-b for o,b in zip(oracle,baseline)),
        }

    def _candidate_profiles(self,active:StrategyRuleProfile)->list[StrategyRuleProfile]:
        weight_sets=[
            ("default",active.window_weights),
            ("short",(0.45,0.30,0.15,0.10)),
            ("balanced",(0.25,0.25,0.25,0.25)),
            ("long",(0.20,0.25,0.25,0.30)),
        ]
        sizes=sorted(set([max(6,active.max_group_size-2),active.max_group_size,min(16,active.max_group_size+2)]))
        redundancy_values=sorted(set([
            max(0.0,active.redundancy_penalty-0.15),
            active.redundancy_penalty,
            min(0.80,active.redundancy_penalty+0.15),
        ]))
        switch_modes=sorted(set([active.switch_guard_enabled,not active.switch_guard_enabled]))
        out=[]
        for label,weights in weight_sets:
            for size in sizes:
                for redundancy in redundancy_values:
                    for switch_guard in switch_modes:
                        out.append(StrategyRuleProfile(
                        version="",
                        market_id=active.market_id,
                        window_weights=weights,
                        max_group_size=size,
                        max_weight=active.max_weight,
                        entry_confirm_days=active.entry_confirm_days,
                        exit_confirm_days=active.exit_confirm_days,
                        cooldown_days=active.cooldown_days,
                        near_duplicate_corr=active.near_duplicate_corr,
                        family_cap=active.family_cap,
                        redundancy_penalty=redundancy,
                        uncertainty_penalty=active.uncertainty_penalty,
                        switch_hurdle_bps=active.switch_hurdle_bps,
                        switch_uncertainty_fraction=active.switch_uncertainty_fraction,
                        switch_guard_enabled=switch_guard,
                        status="candidate",
                        parent_version=active.version,
                        hypothesis=f"{label}-horizon weighting, max group {size}, redundancy penalty {redundancy:.2f}, switch guard {switch_guard}",
                    ))
        return out

    def propose_candidate(self,market_id:str,runs:Iterable[RunRecord])->dict:
        market_id=market_id.upper()
        rows=[
            r for r in runs
            if r.market.market_id.upper()==market_id
            and r.evaluation and r.evaluation.status=="EVALUATED"
        ]
        diag=self.diagnose(market_id,rows)
        if len(rows)<self.min_verified_runs:
            return {
                "created":False,
                "reason":"INSUFFICIENT_VERIFIED_RUNS",
                "required":self.min_verified_runs,
                "available":len(rows),
                "diagnosis":diag,
            }

        rows=sorted(rows,key=lambda r:(r.market.as_of,r.created_at))
        dev_count=max(1,int(len(rows)*0.70))
        dev=rows[:dev_count]
        active=self.active(market_id)
        active_scores=self._simulate_profile(dev,active)
        if not active_scores:
            return {"created":False,"reason":"NO_SIMULATABLE_DEVELOPMENT_RUNS","diagnosis":diag}
        active_mean=mean(active_scores)

        best=None
        for candidate in self._candidate_profiles(active):
            vals=self._simulate_profile(dev,candidate)
            if not vals:
                continue
            score=mean(vals)
            if best is None or score>best[0]:
                best=(score,candidate)

        if best is None or best[0]<=active_mean+1e-6:
            return {
                "created":False,
                "reason":"NO_DEVELOPMENT_IMPROVEMENT",
                "active_development_mean":active_mean,
                "diagnosis":diag,
            }

        m=self._market(market_id)
        n=sum(1 for k in m["profiles"] if "candidate" in k)+1
        candidate=best[1]
        candidate.version=f"strategy-rules-{market_id.lower()}-candidate-{n:03d}"
        candidate.hypothesis=(
            f"{candidate.hypothesis}; development mean {best[0]:+.6f} vs active {active_mean:+.6f}. "
            "Reserved holdout observations were not used for candidate selection."
        )
        m["profiles"][candidate.version]=asdict(candidate)
        m["history"].append({
            "event":"CANDIDATE_CREATED",
            "version":candidate.version,
            "parent":active.version,
            "development_runs":len(dev),
            "reserved_holdout_runs":len(rows)-len(dev),
            "active_development_mean":active_mean,
            "candidate_development_mean":best[0],
            "diagnosis":diag,
            "hypothesis":candidate.hypothesis,
        })
        self._save()
        return {
            "created":True,
            "candidate":asdict(candidate),
            "development_runs":len(dev),
            "reserved_holdout_runs":len(rows)-len(dev),
            "diagnosis":diag,
        }

    def promote(self,market_id:str,version:str,validation:dict)->dict:
        market_id=market_id.upper()
        m=self._market(market_id)
        if version not in m["profiles"]:
            raise KeyError(version)
        checks={
            "replay_pass":validation.get("replay_pass") is True,
            "holdout_pass":validation.get("holdout_pass") is True,
            "shadow_pass":validation.get("shadow_pass") is True,
            "audit_pass":validation.get("audit_pass") is True,
        }
        if not all(checks.values()):
            return {"promoted":False,"checks":checks}
        previous=m["active_version"]
        m["profiles"][previous]["status"]="superseded"
        m["profiles"][version]["status"]="active"
        m["active_version"]=version
        m["history"].append({
            "event":"PROMOTED",
            "version":version,
            "previous":previous,
            "validation":validation,
        })
        self._save()
        return {
            "promoted":True,
            "market_id":market_id,
            "active_version":version,
            "previous":previous,
            "checks":checks,
        }
