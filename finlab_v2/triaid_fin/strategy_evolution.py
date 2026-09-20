from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Iterable

from .contracts import RunRecord, StrategyState
from .store import RunStore


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
    status: str = "active"
    parent_version: str | None = None
    hypothesis: str | None = None


def _seed_profile(market_id: str) -> StrategyRuleProfile:
    market_id=market_id.upper()
    if market_id=="CN":
        return StrategyRuleProfile(
            version="strategy-rules-cn@0.1.0",
            market_id="CN",
            window_weights=(0.35,0.30,0.20,0.15),
            max_group_size=12,
            max_weight=0.28,
            entry_confirm_days=5,
            exit_confirm_days=3,
            cooldown_days=10,
        )
    return StrategyRuleProfile(
        version="strategy-rules-us@0.1.0",
        market_id="US",
        window_weights=(0.35,0.30,0.20,0.15),
        max_group_size=12,
        max_weight=0.28,
        entry_confirm_days=3,
        exit_confirm_days=3,
        cooldown_days=5,
    )


class StrategyEvolutionModule:
    """Independent evolution loop for Strategy Population rules.

    It never edits production rules in place. Candidate rules are created from
    verified outcomes and require replay/holdout/shadow/audit gates before promotion.
    """

    version="strategy-evolution@0.1.0"
    min_verified_runs=20

    def __init__(self,store:RunStore) -> None:
        self.store=store
        raw=store.load_json("strategy_evolution.json",default={})
        if not raw:
            raw={"markets":{}}
            for market_id in ("US","CN"):
                seed=_seed_profile(market_id)
                raw["markets"][market_id]={
                    "active_version":seed.version,
                    "profiles":{seed.version:asdict(seed)},
                    "history":[],
                }
            store.save_json("strategy_evolution.json",raw)
        self.state=raw

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

    @staticmethod
    def _score_state(state:StrategyState,profile:StrategyRuleProfile)->float:
        windows=(21,63,126,252)
        vals=[]
        used=[]
        for h,w in zip(windows,profile.window_weights):
            key=f"return_{h}d_ann"
            if key in state.metrics:
                vals.append(float(state.metrics[key])*float(w))
                used.append(float(w))
        if used and sum(used)>0:
            return sum(vals)/sum(used)
        return float(state.expected_net_return)

    @staticmethod
    def _allocate(scored:list[tuple[float,StrategyState]],profile:StrategyRuleProfile)->dict[str,float]:
        selected=[(score,state) for score,state in scored if score>0][:profile.max_group_size]
        positive={s.strategy_id:score for score,s in selected}
        active=set(positive)
        weights={}
        remaining=1.0
        while active and remaining>1e-12:
            total=sum(positive[k] for k in active)
            if total<=0:
                break
            tentative={k:remaining*positive[k]/total for k in active}
            capped=[k for k,w in tentative.items() if w>profile.max_weight]
            if not capped:
                weights.update(tentative)
                remaining=0.0
                break
            for k in capped:
                weights[k]=profile.max_weight
                remaining-=profile.max_weight
                active.remove(k)
        if remaining>1e-12:
            weights["P28_CASH"]=remaining
        return weights

    def _simulated_group_return(self,run:RunRecord,profile:StrategyRuleProfile)->float|None:
        if not run.evaluation or run.evaluation.status!="EVALUATED":
            return None
        realized=run.evaluation.strategy_realized_returns
        if not realized:
            return None
        feasible=[
            s for s in run.strategy_states
            if s.strategy_id!="P28_CASH"
            and s.lifecycle in {"active","reduced"}
            and s.eligible and not s.hard_failure
            and s.risk_ok and s.capacity_ok and s.liquidity_ok and s.concentration_ok
        ]
        scored=sorted(
            [(self._score_state(s,profile),s) for s in feasible],
            key=lambda x:x[0],
            reverse=True,
        )
        weights=self._allocate(scored,profile)
        gross=sum(weights.get(k,0.0)*float(realized.get(k,0.0)) for k in weights)
        bps=float(run.market.metadata.get("base_cost_bps",2.0) or 2.0)
        return gross-bps/10000.0*sum(abs(v) for k,v in weights.items() if k!="P28_CASH")

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
            ("short",(0.45,0.30,0.15,0.10)),
            ("balanced",(0.25,0.25,0.25,0.25)),
            ("long",(0.20,0.25,0.25,0.30)),
        ]
        sizes=sorted(set([max(6,active.max_group_size-2),active.max_group_size,min(16,active.max_group_size+2)]))
        out=[]
        for label,weights in weight_sets:
            for size in sizes:
                out.append(StrategyRuleProfile(
                    version="",
                    market_id=active.market_id,
                    window_weights=weights,
                    max_group_size=size,
                    max_weight=active.max_weight,
                    entry_confirm_days=active.entry_confirm_days,
                    exit_confirm_days=active.exit_confirm_days,
                    cooldown_days=active.cooldown_days,
                    status="candidate",
                    parent_version=active.version,
                    hypothesis=f"{label}-horizon weighting with group size {size}",
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
        active_scores=[self._simulated_group_return(r,active) for r in dev]
        active_scores=[x for x in active_scores if x is not None]
        if not active_scores:
            return {"created":False,"reason":"NO_SIMULATABLE_DEVELOPMENT_RUNS","diagnosis":diag}
        active_mean=mean(active_scores)

        best=None
        for candidate in self._candidate_profiles(active):
            vals=[self._simulated_group_return(r,candidate) for r in dev]
            vals=[x for x in vals if x is not None]
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
            "Holdout observations were not used for candidate selection."
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
