from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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

    version="strategy-evolution@0.4.0"
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
                    "validations":{},
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
        m.setdefault("validations",{})

    def _market(self,market_id:str)->dict:
        key=market_id.upper()
        if key not in self.state["markets"]:
            seed=_seed_profile(key)
            self.state["markets"][key]={
                "active_version":seed.version,
                "profiles":{seed.version:asdict(seed)},
                "history":[],
                "validations":{},
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

    @staticmethod
    def _states_for_profile(run:RunRecord,profile:StrategyRuleProfile):
        windows=(21,63,126,252)
        weights=tuple(float(x) for x in profile.window_weights)
        total=sum(weights)
        weights=(0.35,0.30,0.20,0.15) if total<=0 else tuple(x/total for x in weights)
        rebuilt=[]
        for original in run.strategy_states:
            state=original.model_copy(deep=True)
            if state.strategy_id=="P28_CASH":
                state.expected_net_return=0.0
                rebuilt.append(state)
                continue
            rs=[float(x) for x in state.recent_returns]
            weighted=[]
            used=[]
            for h,w in zip(windows,weights):
                if len(rs)>=h:
                    ann=mean(rs[-h:])*252.0
                    weighted.append(ann*w)
                    used.append(w)
                    state.metrics[f"return_{h}d_ann"]=ann
            state.expected_net_return=sum(weighted)/sum(used) if used else 0.0
            rebuilt.append(state)
        return rebuilt

    def _simulate_profile(self,runs:list[RunRecord],profile:StrategyRuleProfile)->list[float]:
        population=StrategyPopulationModule()
        population.configure_market(profile)
        previous_group=None
        returns=[]
        for run in sorted(runs,key=lambda r:(r.market.as_of,r.created_at)):
            if not run.evaluation or run.evaluation.status!="EVALUATED":
                continue
            replay_states=self._states_for_profile(run,profile)
            group=population.select(
                profile.market_id,
                replay_states,
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
            and (r.market.metadata or {}).get("daily_bar_complete") is not False
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
            "Reserved holdout observations were not used for candidate selection. Candidate window weights are replayed by rebuilding the state-return estimate from each run's frozen recent-return history."
        )
        m["profiles"][candidate.version]=asdict(candidate)
        holdout=rows[dev_count:]
        created_at=datetime.now(timezone.utc).isoformat()
        m["history"].append({
            "event":"CANDIDATE_CREATED",
            "version":candidate.version,
            "parent":active.version,
            "created_at":created_at,
            "development_runs":len(dev),
            "reserved_holdout_runs":len(holdout),
            "development_run_ids":[r.run_id for r in dev],
            "reserved_holdout_run_ids":[r.run_id for r in holdout],
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

    def candidate_manifest(self,market_id:str,version:str)->dict|None:
        m=self._market(market_id)
        for row in reversed(m.get("history",[])):
            if row.get("event")=="CANDIDATE_CREATED" and row.get("version")==version:
                return dict(row)
        return None

    def validate_candidate(self,market_id:str,version:str,runs:Iterable[RunRecord])->dict:
        market_id=market_id.upper()
        m=self._market(market_id)
        if version not in m["profiles"]:
            raise KeyError(version)
        candidate=StrategyRuleProfile(**m["profiles"][version])
        manifest=self.candidate_manifest(market_id,version)
        if manifest is None or not candidate.parent_version or candidate.parent_version not in m["profiles"]:
            receipt={
                "receipt_id":f"STRATVAL-{market_id}-{version}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
                "passed":False,
                "replay_pass":False,
                "holdout_pass":False,
                "shadow_pass":False,
                "audit_pass":False,
                "reason":"CANDIDATE_EVIDENCE_MANIFEST_MISSING",
            }
            m["validations"][version]=receipt
            self._save()
            return receipt
        parent=StrategyRuleProfile(**m["profiles"][candidate.parent_version])
        eligible=[
            r for r in runs
            if r.market.market_id.upper()==market_id
            and r.evaluation and r.evaluation.status=="EVALUATED"
            and (r.market.metadata or {}).get("daily_bar_complete") is not False
        ]
        run_map={r.run_id:r for r in eligible}
        dev=[run_map[x] for x in manifest.get("development_run_ids",[]) if x in run_map]
        holdout=[run_map[x] for x in manifest.get("reserved_holdout_run_ids",[]) if x in run_map]
        known_ids=set(manifest.get("development_run_ids",[]))|set(manifest.get("reserved_holdout_run_ids",[]))
        created_at=str(manifest.get("created_at") or "")
        shadow=[r for r in eligible if created_at and r.created_at>created_at and r.run_id not in known_ids]

        def compare(rows:list[RunRecord])->dict:
            cand=self._simulate_profile(rows,candidate)
            base=self._simulate_profile(rows,parent)
            valid=len(cand)==len(rows) and len(base)==len(rows)
            return {
                "count":len(cand),
                "candidate_mean":mean(cand) if cand else None,
                "parent_mean":mean(base) if base else None,
                "valid":valid,
            }

        dev_result=compare(dev)
        holdout_result=compare(holdout)
        shadow_result=compare(shadow)
        replay_pass=bool(dev_result["valid"] and dev_result["count"]>=1 and dev_result["candidate_mean"]>=dev_result["parent_mean"]-1e-12)
        holdout_pass=bool(holdout_result["valid"] and holdout_result["count"]>=1 and holdout_result["candidate_mean"]>=holdout_result["parent_mean"]-1e-12)
        shadow_pass=bool(shadow_result["valid"] and shadow_result["count"]>=5 and shadow_result["candidate_mean"]>=shadow_result["parent_mean"]-1e-12)
        audit_pass=bool(
            abs(sum(candidate.window_weights)-1.0)<=1e-9
            and candidate.max_group_size>=1
            and 0.0<candidate.max_weight<=1.0
            and candidate.entry_confirm_days>=1
            and candidate.exit_confirm_days>=1
            and candidate.cooldown_days>=0
            and candidate.redundancy_penalty>=0
            and candidate.uncertainty_penalty>=0
            and candidate.family_cap>=1
        )
        receipt={
            "receipt_id":f"STRATVAL-{market_id}-{version}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
            "passed":all((replay_pass,holdout_pass,shadow_pass,audit_pass)),
            "replay_pass":replay_pass,
            "holdout_pass":holdout_pass,
            "shadow_pass":shadow_pass,
            "audit_pass":audit_pass,
            "development":dev_result,
            "holdout":holdout_result,
            "shadow":shadow_result,
            "shadow_min_runs":5,
            "validation_discipline":"INTERNAL_REPLAY_RESERVED_HOLDOUT_AND_POST_CREATION_SHADOW_ONLY",
        }
        m["validations"][version]=receipt
        m["history"].append({
            "event":"VALIDATION_RECORDED",
            "version":version,
            "receipt_id":receipt["receipt_id"],
            "recorded_at":datetime.now(timezone.utc).isoformat(),
            "passed":receipt["passed"],
        })
        self._save()
        return receipt

    def promote(self,market_id:str,version:str,validation:dict)->dict:
        market_id=market_id.upper()
        m=self._market(market_id)
        if version not in m["profiles"]:
            raise KeyError(version)
        receipt=m.get("validations",{}).get(version) or {}
        receipt_id=str(validation.get("receipt_id") or "")
        checks={
            "internal_receipt_match":bool(receipt_id and receipt_id==str(receipt.get("receipt_id") or "")),
            "replay_pass":receipt.get("replay_pass") is True,
            "holdout_pass":receipt.get("holdout_pass") is True,
            "shadow_pass":receipt.get("shadow_pass") is True,
            "audit_pass":receipt.get("audit_pass") is True,
        }
        if not all(checks.values()) or receipt.get("passed") is not True:
            return {"promoted":False,"reason":"INTERNAL_VALIDATION_REQUIRED","checks":checks}
        previous=m["active_version"]
        m["profiles"][previous]["status"]="superseded"
        m["profiles"][version]["status"]="active"
        m["active_version"]=version
        m["history"].append({
            "event":"PROMOTED",
            "version":version,
            "previous":previous,
            "validation_receipt_id":receipt_id,
        })
        self._save()
        return {
            "promoted":True,
            "market_id":market_id,
            "active_version":version,
            "previous":previous,
            "checks":checks,
            "validation_receipt_id":receipt_id,
        }
