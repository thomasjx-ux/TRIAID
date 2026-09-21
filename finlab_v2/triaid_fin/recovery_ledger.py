from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from math import prod
from typing import Any

from .contracts import utc_now
from .store import RunStore


class RecoveryWaveLedger:
    """Append-only decision + outcome ledger for the recovery-wave shadow core."""

    version="recovery-wave-ledger@0.1.0"
    decision_file="recovery_wave_decisions.jsonl"
    outcome_file="recovery_wave_outcomes.jsonl"
    index_file="recovery_wave_index.json"
    outcome_index_file="recovery_wave_outcome_index.json"

    def __init__(self, store: RunStore) -> None:
        self.store=store
        self.index=store.load_json(self.index_file,default={}) or {}
        self.outcome_index=store.load_json(self.outcome_index_file,default={}) or {}

    @staticmethod
    def _canonical(payload: dict) -> str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    def decisions(self,market_id:str|None=None,limit:int=500)->list[dict]:
        rows=self.store.read_jsonl(self.decision_file,limit=max(limit*4,limit))
        if market_id:
            key=market_id.upper()
            rows=[r for r in rows if str(r.get("market_id","")).upper()==key]
        return rows[-limit:]

    def outcomes(self,market_id:str|None=None,limit:int=1000)->list[dict]:
        rows=self.store.read_jsonl(self.outcome_file,limit=max(limit*4,limit))
        if market_id:
            key=market_id.upper()
            rows=[r for r in rows if str(r.get("market_id","")).upper()==key]
        return rows[-limit:]

    def by_snapshot(self,market_id:str,snapshot_id:str)->dict|None:
        key=f"{market_id.upper()}:{snapshot_id}"
        decision_id=self.index.get("snapshots",{}).get(key)
        if not decision_id:
            return None
        for row in reversed(self.decisions(market_id,1000)):
            if row.get("decision_id")==decision_id:
                return deepcopy(row)
        return None

    def latest(self,market_id:str)->dict|None:
        rows=self.decisions(market_id,1000)
        return deepcopy(rows[-1]) if rows else None

    def freeze(self,decision:dict,snapshot_id:str,market_as_of:str)->dict:
        market=str(decision.get("market_id") or "").upper()
        if not market or not snapshot_id:
            raise ValueError("RecoveryWaveLedger requires market_id and snapshot_id.")
        existing=self.by_snapshot(market,snapshot_id)
        if existing:
            return existing

        prior=self.latest(market)
        row=deepcopy(decision)
        row["market_id"]=market
        row["snapshot_id"]=snapshot_id
        row["market_as_of"]=market_as_of
        row["frozen_at"]=utc_now()
        row["previous_decision_id"]=prior.get("decision_id") if prior else None
        row["previous_decision_hash"]=prior.get("decision_hash") if prior else None
        body=deepcopy(row)
        row["decision_hash"]=self._hash(body)
        row["decision_id"]=f"RW-{market}-{row.get('source_latest_ts')}-{row['decision_hash'][:10]}"
        self.store.append_jsonl(self.decision_file,row)
        self.index.setdefault("snapshots",{})[f"{market}:{snapshot_id}"]=row["decision_id"]
        self.index["latest_decision_id"]=row["decision_id"]
        self.index["latest_decision_hash"]=row["decision_hash"]
        self.store.save_json(self.index_file,self.index)
        return deepcopy(row)

    def record_outcome(
        self,
        market_id:str,
        as_of:str,
        period_start_as_of:str,
        product_returns:dict[str,float],
        source_snapshot_id:str|None=None,
    )->dict:
        market=market_id.upper()
        key=f"{market}:{as_of}"
        if key in self.outcome_index:
            return {"recorded":False,"reason":"DUPLICATE_OUTCOME_DATE","outcome_id":self.outcome_index[key]}
        row={
            "outcome_id":f"RWO-{market}-{as_of}",
            "recorded_at":utc_now(),
            "market_id":market,
            "as_of":as_of,
            "period_start_as_of":period_start_as_of,
            "source_snapshot_id":source_snapshot_id,
            "product_returns":{str(k):float(v) for k,v in product_returns.items()},
        }
        self.store.append_jsonl(self.outcome_file,row)
        self.outcome_index[key]=row["outcome_id"]
        self.store.save_json(self.outcome_index_file,self.outcome_index)
        return {"recorded":True,"outcome":deepcopy(row)}

    @staticmethod
    def _compound(xs:list[float])->float:
        return prod(1.0+float(x) for x in xs)-1.0 if xs else 0.0

    def review_decision(self,decision:dict)->dict:
        market=str(decision.get("market_id") or "").upper()
        decision_date=str(decision.get("market_as_of") or "")
        raw_future=[
            o for o in self.outcomes(market,2000)
            if str(o.get("as_of") or "")>decision_date
        ]
        weights={
            str(x.get("symbol")):float(x.get("target_weight") or 0.0)
            for x in decision.get("trade_opinions",[])
            if x.get("symbol")
        }
        products=sorted(weights)
        incomplete_dates=[
            o.get("as_of") for o in raw_future
            if any(s not in (o.get("product_returns") or {}) for s in products)
        ]
        future=[
            o for o in raw_future
            if all(s in (o.get("product_returns") or {}) for s in products)
        ]
        daily=[]
        portfolio_returns=[]
        equal_returns=[]
        running_product={s:1.0 for s in products}
        running_portfolio=1.0
        running_equal=1.0
        for index,o in enumerate(future,1):
            rr={s:float((o.get("product_returns") or {}).get(s,0.0)) for s in products}
            for s in products:
                running_product[s]*=1.0+rr[s]
            pr=sum(weights[s]*rr[s] for s in products)
            er=sum(rr.values())/len(products) if products else 0.0
            portfolio_returns.append(pr)
            equal_returns.append(er)
            running_portfolio*=1.0+pr
            running_equal*=1.0+er
            daily.append({
                "period_number":index,
                "as_of":o.get("as_of"),
                "product_returns":rr,
                "product_cumulative_returns":{s:running_product[s]-1.0 for s in products},
                "portfolio_return":pr,
                "equal_weight_return":er,
                "portfolio_cumulative_return":running_portfolio-1.0,
                "equal_weight_cumulative_return":running_equal-1.0,
                "excess_vs_equal_weight":running_portfolio-running_equal,
            })

        horizons={}
        for h in (3,5,10,20):
            if len(future)<h:
                continue
            sample=daily[:h]
            horizons[str(h)]={
                "horizon_days":h,
                "portfolio_cumulative_return":sample[-1]["portfolio_cumulative_return"],
                "equal_weight_cumulative_return":sample[-1]["equal_weight_cumulative_return"],
                "excess_vs_equal_weight":sample[-1]["excess_vs_equal_weight"],
                "product_cumulative_returns":sample[-1]["product_cumulative_returns"],
            }

        return {
            "decision_id":decision.get("decision_id"),
            "decision_hash":decision.get("decision_hash"),
            "market_as_of":decision_date,
            "frozen_at":decision.get("frozen_at"),
            "observation_days":len(future),
            "incomplete_outcome_dates":incomplete_dates,
            "trade_opinions":deepcopy(decision.get("trade_opinions",[])),
            "daily_path":daily,
            "current_portfolio_cumulative_return":self._compound(portfolio_returns),
            "current_equal_weight_cumulative_return":self._compound(equal_returns),
            "current_excess_vs_equal_weight":(
                self._compound(portfolio_returns)-self._compound(equal_returns)
            ),
            "matured_horizons":horizons,
        }

    def verify_integrity(self,market_id:str|None=None)->dict:
        rows=self.decisions(market_id,5000)
        previous_hash_by_market={}
        checks=[]
        for row in rows:
            market=str(row.get("market_id") or "").upper()
            body={k:deepcopy(v) for k,v in row.items() if k not in {"decision_hash","decision_id"}}
            expected=self._hash(body)
            hash_ok=expected==row.get("decision_hash")
            expected_previous=previous_hash_by_market.get(market)
            chain_ok=row.get("previous_decision_hash")==expected_previous
            checks.append({
                "decision_id":row.get("decision_id"),
                "market_id":market,
                "hash_ok":hash_ok,
                "chain_ok":chain_ok,
            })
            previous_hash_by_market[market]=row.get("decision_hash")
        return {
            "passed":all(x["hash_ok"] and x["chain_ok"] for x in checks),
            "decision_count":len(rows),
            "checks":checks[-20:],
        }

    def daily_report(self,market_id:str="CN")->dict|None:
        rows=self.decisions(market_id,1000)
        if not rows:
            return None
        latest=rows[-1]
        previous=rows[-2] if len(rows)>1 else None
        latest_review=self.review_decision(latest)
        previous_review=self.review_decision(previous) if previous else None
        history=[
            {
                "decision_id":row.get("decision_id"),
                "market_as_of":row.get("market_as_of"),
                "frozen_at":row.get("frozen_at"),
                "decision_status":row.get("decision_status"),
                "trade_opinions":deepcopy(row.get("trade_opinions",[])),
                "review":self.review_decision(row),
            }
            for row in rows[-10:]
        ]
        return {
            "report_version":self.version,
            "latest_decision":deepcopy(latest),
            "latest_decision_review":latest_review,
            "previous_decision_review":previous_review,
            "decision_history":history,
            "latest_outcome_as_of":(
                self.outcomes(market_id,1)[-1].get("as_of")
                if self.outcomes(market_id,1) else None
            ),
            "integrity":self.verify_integrity(market_id),
            "interpretation_guard":"Trade opinions are frozen research recommendations, not broker orders. Historical analog statistics are evidence, not guaranteed probabilities. Do not rewrite frozen recommendations after outcomes are known.",
        }

    def status(self,market_id:str|None=None)->dict:
        return {
            "version":self.version,
            "decision_count":len(self.decisions(market_id,5000)),
            "outcome_count":len(self.outcomes(market_id,5000)),
            "integrity":self.verify_integrity(market_id),
        }
