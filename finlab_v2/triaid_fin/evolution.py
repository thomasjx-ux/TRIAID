from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Iterable

from .contracts import RunRecord
from .store import RunStore


@dataclass
class CoreParameters:
    version: str
    risk_penalty: float = 0.25
    uncertainty_penalty: float = 1.0
    intervention_strength: float = 0.55
    risk_off_multiplier: float = 0.55
    status: str = "active"
    parent_version: str | None = None
    hypothesis: str | None = None


class EvolutionModule:
    version = "core-evolution@0.1.0"

    def __init__(self, store: RunStore) -> None:
        self.store = store
        raw = store.load_json("core_evolution.json", default={})
        if not raw:
            seed = CoreParameters(version="triaid-core-v2@0.2.0")
            raw = {"active_version": seed.version, "cores": {seed.version: asdict(seed)}, "history": []}
            store.save_json("core_evolution.json", raw)
        self.state = raw

    def active(self) -> CoreParameters:
        version = self.state["active_version"]
        return CoreParameters(**self.state["cores"][version])

    def get(self, version: str) -> CoreParameters:
        return CoreParameters(**self.state["cores"][version])

    def status(self) -> dict:
        return deepcopy(self.state)

    def diagnose(self, runs: Iterable[RunRecord]) -> dict:
        evaluated=[r for r in runs if r.evaluation and r.evaluation.status=="EVALUATED"]
        if not evaluated:
            return {"evaluated_runs":0,"mean_excess_return":None,"negative_rate":None,"worst_excess_return":None}
        excess=[float(r.evaluation.excess_return or 0.0) for r in evaluated]
        return {
            "evaluated_runs":len(excess),
            "mean_excess_return":sum(excess)/len(excess),
            "negative_rate":sum(1 for x in excess if x<0)/len(excess),
            "worst_excess_return":min(excess),
            "best_excess_return":max(excess),
        }

    def propose_candidate(self, runs: Iterable[RunRecord]) -> dict:
        diag=self.diagnose(runs)
        parent=self.active()
        if not diag["evaluated_runs"]:
            return {"created":False,"reason":"NO_EVALUATED_RUNS","diagnosis":diag}

        n=sum(1 for k in self.state["cores"] if k.startswith("triaid-core-v2-candidate-"))+1
        version=f"triaid-core-v2-candidate-{n:03d}"
        cand=CoreParameters(**asdict(parent))
        cand.version=version
        cand.status="candidate"
        cand.parent_version=parent.version

        if (diag["mean_excess_return"] or 0.0) < 0 or (diag["negative_rate"] or 0.0) > 0.55:
            cand.intervention_strength=max(0.10,parent.intervention_strength*0.85)
            cand.uncertainty_penalty=min(3.0,parent.uncertainty_penalty*1.10)
            cand.hypothesis="Reduce intervention strength and demand more evidence because recent interventions show excessive negative contribution."
        else:
            cand.intervention_strength=min(0.90,parent.intervention_strength*1.05)
            cand.hypothesis="Slightly increase intervention strength because recent verified interventions show positive average contribution."

        self.state["cores"][version]=asdict(cand)
        self.state["history"].append({"event":"CANDIDATE_CREATED","version":version,"parent":parent.version,"diagnosis":diag,"hypothesis":cand.hypothesis})
        self.store.save_json("core_evolution.json",self.state)
        return {"created":True,"candidate":asdict(cand),"diagnosis":diag}

    def promote(self, version: str, validation: dict) -> dict:
        if version not in self.state["cores"]:
            raise KeyError(version)
        required = {
            "replay_pass": validation.get("replay_pass") is True,
            "holdout_pass": validation.get("holdout_pass") is True,
            "shadow_pass": validation.get("shadow_pass") is True,
            "audit_pass": validation.get("audit_pass") is True,
        }
        if not all(required.values()):
            return {"promoted":False,"checks":required}
        previous=self.state["active_version"]
        self.state["cores"][previous]["status"]="superseded"
        self.state["cores"][version]["status"]="active"
        self.state["active_version"]=version
        self.state["history"].append({"event":"PROMOTED","version":version,"previous":previous,"validation":validation})
        self.store.save_json("core_evolution.json",self.state)
        return {"promoted":True,"active_version":version,"previous":previous,"checks":required}
