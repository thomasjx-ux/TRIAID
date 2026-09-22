from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from .cross_market_crash import MARKET_INDEXES, PRIMARY_INDEX
from .long_cycle_hypothesis import LongCycleHypothesisExperiment
from .store import RunStore

HORIZONS=(20,60,120,250)


class HazardProspectiveLedger:
    version="hazard-prospective-ledger@0.3.0"
    ledger_file="hazard_prospective_ledger.jsonl"
    state_file="hazard_prospective_state.json"
    latest_file="hazard_prospective_latest.json"

    def __init__(self,store:RunStore)->None:
        self.store=store

    @staticmethod
    def _canonical(payload:dict)->str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    def rows(self,limit:int=5000)->list[dict]:
        state=self.store.load_json(self.state_file,default={}) or {}
        rows=list(state.get("rows") or [])
        if rows:
            return rows[-int(limit):]
        return self.store.read_jsonl(self.ledger_file,limit=limit)

    @staticmethod
    def _future_metrics(series:dict,as_of:str,horizon:int)->dict|None:
        rows=[]
        for ts,px in zip(series["ts"],series["close"]):
            d=datetime.fromtimestamp(int(ts),timezone.utc).date().isoformat()
            rows.append((d,float(px)))
        idx=next((i for i,(d,_) in enumerate(rows) if d>=as_of),None)
        if idx is None or idx+int(horizon)>=len(rows):
            return None
        start=float(rows[idx][1])
        path=rows[idx+1:idx+int(horizon)+1]
        end=float(path[-1][1])
        trough=min(float(x[1]) for x in path)
        peak=max(float(x[1]) for x in path)
        return {
            "horizon_trading_days":int(horizon),
            "start_date":rows[idx][0],
            "end_date":path[-1][0],
            "start_price":start,
            "end_price":end,
            "return":end/start-1.0 if start>0 else None,
            "max_loss_from_start":trough/start-1.0 if start>0 else None,
            "max_gain_from_start":peak/start-1.0 if start>0 else None,
        }

    def freeze(self,hazard_report:dict,policy_curve:dict|None=None)->dict:
        hazard_as_of=str(hazard_report.get("as_of") or "")
        if not hazard_as_of:
            raise ValueError("hazard report missing as_of")
        curve_as_of=str((policy_curve or {}).get("as_of") or hazard_as_of)
        effective_as_of=max(hazard_as_of,curve_as_of)

        rows=self.rows(5000)
        migrated=False
        for row in rows:
            old_curve=str(row.get("policy_curve_as_of") or "")
            if (
                row.get("evidence_eligible") is not False
                and old_curve
                and str(row.get("as_of") or "")<old_curve
            ):
                row["evidence_eligible"]=False
                row["invalidated_reason"]="PRE_V0_3_MIXED_TIMESTAMP_LOOKAHEAD_LABEL"
                migrated=True
        if migrated:
            self.store.save_json(self.state_file,{"version":self.version,"rows":rows})
            self.store.append_jsonl(self.ledger_file,{
                "event":"MIGRATION_INVALIDATE",
                "at":datetime.now(timezone.utc).isoformat(),
                "reason":"PRE_V0_3_MIXED_TIMESTAMP_LOOKAHEAD_LABEL",
            })

        existing=next((
            x for x in reversed(rows)
            if x.get("as_of")==effective_as_of
            and x.get("source_experiment_hash")==hazard_report.get("experiment_hash")
            and x.get("policy_curve_snapshot_id")==((policy_curve or {}).get("snapshot_id"))
            and x.get("evidence_eligible") is not False
        ),None)
        if existing:
            return existing

        current=hazard_report.get("current_state") or {}
        hazard_date=date.fromisoformat(hazard_as_of)
        curve_date=date.fromisoformat(curve_as_of)
        payload={
            "version":self.version,
            "as_of":effective_as_of,
            "hazard_signal_as_of":hazard_as_of,
            "policy_curve_as_of":curve_as_of,
            "component_lag_calendar_days":abs((curve_date-hazard_date).days),
            "evidence_eligible":True,
            "frozen_at":datetime.now(timezone.utc).isoformat(),
            "source_experiment_id":hazard_report.get("experiment_id"),
            "source_experiment_hash":hazard_report.get("experiment_hash"),
            "shadow_only":True,
            "applied_to_weights":False,
            "current_state":current,
            "statistically_supported_composite_rows":hazard_report.get("statistically_supported_composite_rows") or [],
            "policy_curve_snapshot_id":(policy_curve or {}).get("snapshot_id"),
            "policy_curve_metrics":(policy_curve or {}).get("metrics"),
            "outcomes":{},
            "resolved_horizons":[],
            "pending_horizons":list(HORIZONS),
            "time_alignment_guard":"The ledger as_of is the latest component date, so later policy-curve information is never backdated to an earlier hazard-signal date.",
            "immutable_signal_guard":"Frozen signal fields are never recomputed after insertion; only future outcome fields may be appended.",
        }
        digest=self._hash(payload)
        payload["ledger_id"]=f"HAZARD-SHADOW-{effective_as_of}-{digest[:10]}"
        rows=self.rows(5000)
        rows.append(payload)
        self.store.save_json(self.state_file,{"version":self.version,"rows":rows})
        self.store.append_jsonl(self.ledger_file,{"event":"FREEZE",**payload})
        self.store.save_json(self.latest_file,payload)
        return payload

    def resolve(self)->dict:
        rows=self.rows(5000)
        if not rows:
            return {"version":self.version,"rows":0,"updated":0}
        primary={}
        errors={}
        for market,label in PRIMARY_INDEX.items():
            symbol=MARKET_INDEXES[market][label]
            try:
                primary[market]=LongCycleHypothesisExperiment._fetch_yahoo_full(symbol,timeout=30)
            except Exception as exc:
                errors[market]=f"{type(exc).__name__}:{exc}"
        updated=0
        new_rows=[]
        for row in rows:
            out=dict(row)
            if out.get("evidence_eligible") is False:
                new_rows.append(out)
                continue
            outcomes=dict(out.get("outcomes") or {})
            for horizon in HORIZONS:
                key=str(horizon)
                if key in outcomes:
                    continue
                market_results={}
                complete=True
                for market,series in primary.items():
                    metric=self._future_metrics(series,str(out["as_of"]),horizon)
                    if metric is None:
                        complete=False
                        break
                    market_results[market]=metric
                if complete and len(market_results)==len(PRIMARY_INDEX):
                    outcomes[key]={
                        "resolved_at":datetime.now(timezone.utc).isoformat(),
                        "markets":market_results,
                        "worst_market_return":min(x["return"] for x in market_results.values()),
                        "worst_market_drawdown_from_start":min(x["max_loss_from_start"] for x in market_results.values()),
                    }
                    updated+=1
            out["outcomes"]=outcomes
            out["resolved_horizons"]=sorted(int(k) for k in outcomes)
            out["pending_horizons"]=[h for h in HORIZONS if str(h) not in outcomes]
            new_rows.append(out)

        if new_rows!=rows:
            self.store.save_json(self.state_file,{"version":self.version,"rows":new_rows})
            latest=new_rows[-1]
            self.store.save_json(self.latest_file,latest)
            self.store.append_jsonl(self.ledger_file,{
                "event":"RESOLVE",
                "at":datetime.now(timezone.utc).isoformat(),
                "updated_outcomes":updated,
                "latest_ledger_id":latest.get("ledger_id"),
            })
        else:
            latest=rows[-1]
        return {
            "version":self.version,
            "rows":len(rows),
            "updated_outcomes":updated,
            "latest":latest,
            "errors":errors,
        }

    def latest(self)->dict|None:
        row=self.store.load_json(self.latest_file,default={})
        return row or None

    def status(self)->dict:
        latest=self.latest()
        return {
            "version":self.version,
            "ledger_rows":len(self.rows(5000)),
            "latest_ledger_id":latest.get("ledger_id") if latest else None,
            "latest_as_of":latest.get("as_of") if latest else None,
            "pending_horizons":latest.get("pending_horizons") if latest else list(HORIZONS),
        }
