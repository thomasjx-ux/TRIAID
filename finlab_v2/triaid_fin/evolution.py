from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    version = "core-evolution@0.2.0"
    min_verified_runs = 20

    def __init__(self, store: RunStore) -> None:
        self.store = store
        raw = store.load_json("core_evolution.json", default={})
        if not raw:
            seed = CoreParameters(version="triaid-core-v2@0.2.0")
            raw = {"active_version": seed.version, "cores": {seed.version: asdict(seed)}, "history": [], "validations": {}}
            store.save_json("core_evolution.json", raw)
        raw.setdefault("history",[])
        raw.setdefault("validations",{})
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
        eligible=[
            r for r in runs
            if r.evaluation and r.evaluation.status=="EVALUATED"
            and (r.market.metadata or {}).get("daily_bar_complete") is not False
        ]
        eligible=sorted(eligible,key=lambda r:(r.market.as_of,r.created_at,r.run_id))
        if len(eligible)<self.min_verified_runs:
            return {
                "created":False,
                "reason":"INSUFFICIENT_POSTERIOR_EVALUATIONS",
                "required":self.min_verified_runs,
                "available":len(eligible),
                "diagnosis":self.diagnose(eligible),
            }
        dev_count=max(1,int(len(eligible)*0.70))
        dev=eligible[:dev_count]
        holdout=eligible[dev_count:]
        diag=self.diagnose(dev)
        parent=self.active()

        n=sum(1 for k in self.state["cores"] if k.startswith("triaid-core-v2-candidate-"))+1
        version=f"triaid-core-v2-candidate-{n:03d}"
        cand=CoreParameters(**asdict(parent))
        cand.version=version
        cand.status="candidate"
        cand.parent_version=parent.version

        if (diag["mean_excess_return"] or 0.0) < 0 or (diag["negative_rate"] or 0.0) > 0.55:
            cand.intervention_strength=max(0.10,parent.intervention_strength*0.85)
            cand.uncertainty_penalty=min(3.0,parent.uncertainty_penalty*1.10)
            cand.hypothesis="Reduce intervention strength and demand more evidence because development-period interventions show excessive negative relative return."
        else:
            cand.intervention_strength=min(0.90,parent.intervention_strength*1.05)
            cand.hypothesis="Slightly increase intervention strength because development-period interventions show positive average relative return."

        created_at=datetime.now(timezone.utc).isoformat()
        self.state["cores"][version]=asdict(cand)
        self.state["history"].append({
            "event":"CANDIDATE_CREATED",
            "version":version,
            "parent":parent.version,
            "created_at":created_at,
            "development_run_ids":[r.run_id for r in dev],
            "reserved_holdout_run_ids":[r.run_id for r in holdout],
            "diagnosis":diag,
            "hypothesis":cand.hypothesis,
        })
        self.store.save_json("core_evolution.json",self.state)
        return {
            "created":True,
            "candidate":asdict(cand),
            "development_runs":len(dev),
            "reserved_holdout_runs":len(holdout),
            "diagnosis":diag,
        }

    def candidate_manifest(self,version:str)->dict|None:
        for row in reversed(self.state.get("history",[])):
            if row.get("event")=="CANDIDATE_CREATED" and row.get("version")==version:
                return deepcopy(row)
        return None

    def record_validation(self,version:str,receipt:dict)->dict:
        if version not in self.state["cores"]:
            raise KeyError(version)
        row=deepcopy(receipt)
        row["version"]=version
        row.setdefault("recorded_at",datetime.now(timezone.utc).isoformat())
        self.state.setdefault("validations",{})[version]=row
        self.state["history"].append({
            "event":"VALIDATION_RECORDED",
            "version":version,
            "receipt_id":row.get("receipt_id"),
            "recorded_at":row.get("recorded_at"),
            "passed":bool(row.get("passed")),
        })
        self.store.save_json("core_evolution.json",self.state)
        return deepcopy(row)

    def promote(self, version: str, validation: dict) -> dict:
        if version not in self.state["cores"]:
            raise KeyError(version)
        receipt=self.state.get("validations",{}).get(version) or {}
        receipt_id=str(validation.get("receipt_id") or "")
        required={
            "internal_receipt_match":bool(receipt_id and receipt_id==str(receipt.get("receipt_id") or "")),
            "replay_pass":receipt.get("replay_pass") is True,
            "holdout_pass":receipt.get("holdout_pass") is True,
            "shadow_pass":receipt.get("shadow_pass") is True,
            "audit_pass":receipt.get("audit_pass") is True,
        }
        if not all(required.values()) or receipt.get("passed") is not True:
            return {"promoted":False,"reason":"INTERNAL_VALIDATION_REQUIRED","checks":required}
        previous=self.state["active_version"]
        self.state["cores"][previous]["status"]="superseded"
        self.state["cores"][version]["status"]="active"
        self.state["active_version"]=version
        self.state["history"].append({"event":"PROMOTED","version":version,"previous":previous,"validation_receipt_id":receipt_id})
        self.store.save_json("core_evolution.json",self.state)
        return {"promoted":True,"active_version":version,"previous":previous,"checks":required,"validation_receipt_id":receipt_id}
