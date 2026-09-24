from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone


VERSION="triaid-outcome-resolver@1.0.0"


def _hash(payload:dict)->str:
    raw=json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite(value):
    try:
        x=float(value)
    except Exception:
        return None
    return x


class OutcomeResolver:
    """Pair frozen T0 evidence with existing realized T1 evaluation.

    This layer does not recalculate market returns. It only normalizes the
    already-audited US/HK route reviews and generic run evaluations into one
    comparable experiment record.
    """

    version=VERSION

    def __init__(self,evidence_repository,outcome_read_port,journal)->None:
        self.evidence_repository=evidence_repository
        self.outcome_read_port=outcome_read_port
        self.journal=journal

    @staticmethod
    def _normalize_route(market:str,review:dict)->dict|None:
        observation_days=int(review.get("observation_days") or 0)
        if observation_days<=0:
            return None
        if market=="US":
            triaid=_finite(review.get("current_return_max_theoretical_return"))
            baseline=_finite(review.get("current_generic_core_theoretical_return"))
            benchmark=_finite(review.get("current_spy_buy_hold_return"))
        else:
            triaid=_finite(review.get("current_triaid_theoretical_return"))
            baseline=_finite(review.get("current_baseline_theoretical_return"))
            benchmark=_finite(review.get("current_benchmark_buy_hold_return"))
        if triaid is None or baseline is None:
            return None
        path=review.get("daily_path") or []
        sleeves=((review.get("capital_sleeves") or {}).get("sleeves") or [])
        execution_sleeves=[
            {
                key:deepcopy(row.get(key))
                for key in row
                if key in {
                    "sleeve_id",
                    "starting_capital_usd",
                    "starting_capital_hkd",
                    "fill_ratio",
                    "total_execution_cost_usd",
                    "total_execution_cost_hkd",
                    "current_equity_usd",
                    "current_equity_hkd",
                    "current_net_pnl_usd",
                    "current_net_pnl_hkd",
                    "current_net_return",
                    "observation_days",
                }
            }
            for row in sleeves
            if isinstance(row,dict)
        ]
        return {
            "state":"EVALUATED",
            "method":"SYMMETRIC_THEORETICAL_HOLDINGS_COMPARISON",
            "observation_days":observation_days,
            "outcome_as_of":path[-1].get("as_of") if path else None,
            "triaid_realized_return":triaid,
            "baseline_realized_return":baseline,
            "benchmark_realized_return":benchmark,
            "triaid_excess_vs_baseline":triaid-baseline,
            "triaid_excess_vs_benchmark":(
                triaid-benchmark if benchmark is not None else None
            ),
            "trading_cost":None,
            "contribution_deltas":{},
            "execution_sleeves":execution_sleeves,
            "interpretation_guard":"PRIMARY_EXCESS_USES_SYMMETRIC_THEORETICAL_HOLDINGS; EXECUTION_SLEEVES_ARE_SUPPLEMENTAL_AND_NOT_USED_AS_AN_ASYMMETRIC_BASELINE_COMPARISON",
        }

    @staticmethod
    def _normalize_run(payload:dict)->dict|None:
        evaluation=payload.get("evaluation") or {}
        if evaluation.get("status")!="EVALUATED":
            return None
        triaid=_finite(evaluation.get("triaid_return"))
        baseline=_finite(evaluation.get("baseline_return"))
        if triaid is None or baseline is None:
            return None
        base=evaluation.get("baseline_contributions") or {}
        tri=evaluation.get("triaid_contributions") or {}
        ids=set(base)|set(tri)
        deltas={
            sid:float(tri.get(sid,0.0))-float(base.get(sid,0.0))
            for sid in ids
        }
        return {
            "state":"EVALUATED",
            "method":"GENERIC_EVALUATION_MODULE",
            "observation_days":1,
            "outcome_as_of":payload.get("outcome_as_of"),
            "triaid_realized_return":triaid,
            "baseline_realized_return":baseline,
            "benchmark_realized_return":None,
            "triaid_excess_vs_baseline":_finite(evaluation.get("excess_return")),
            "triaid_excess_vs_benchmark":None,
            "trading_cost":_finite(evaluation.get("trading_cost")) or 0.0,
            "contribution_deltas":dict(
                sorted(deltas.items(),key=lambda item:abs(item[1]),reverse=True)
            ),
            "execution_sleeves":[],
            "interpretation_guard":"GENERIC_EVALUATION_USES_THE_EXISTING_AUDITED_BASELINE_AND_TRIAID_CONTRIBUTION_ACCOUNTING; NO_RETURNS_ARE_RECOMPUTED_BY_THE_RESOLVER",
        }

    def resolve_evidence(self,evidence:dict)->dict:
        market=str(evidence.get("market_id") or "").upper()
        evidence_id=evidence.get("evidence_id")
        lineage=evidence.get("decision_lineage") or {}
        if not market or not evidence_id:
            return {"state":"ERROR","reason":"INVALID_FORMAL_EVIDENCE"}
        raw=self.outcome_read_port.review(
            market,
            lineage.get("decision_id"),
            lineage.get("run_id"),
        )
        if raw is None:
            return {
                "state":"WAITING",
                "market_id":market,
                "evidence_id":evidence_id,
                "reason":"NO_REALIZED_OUTCOME_YET",
            }
        if raw.get("kind")=="ROUTE_REVIEW":
            normalized=self._normalize_route(market,raw.get("review") or {})
        else:
            normalized=self._normalize_run(raw)
        if normalized is None:
            return {
                "state":"WAITING",
                "market_id":market,
                "evidence_id":evidence_id,
                "reason":"REALIZED_OUTCOME_NOT_MATURE",
            }

        core={
            "outcome_schema":"triaid-t0-t1-outcome@1.0.0",
            "resolver_version":self.version,
            "market_id":market,
            "evidence_id":evidence_id,
            "evidence_hash_sha256":evidence.get("evidence_hash_sha256"),
            "decision_lineage":deepcopy(lineage),
            "t0_market_as_of":lineage.get("market_as_of"),
            **normalized,
        }
        outcome_hash=_hash(core)
        resolved_at=datetime.now(timezone.utc).isoformat()
        artifact={
            **core,
            "outcome_hash_sha256":outcome_hash,
            "resolved_at_utc":resolved_at,
        }
        path=f"verified_outcomes/{market}/{evidence_id}.json"
        previous=self.journal.load_json(path,default={})
        changed=previous.get("outcome_hash_sha256")!=outcome_hash
        if changed:
            self.journal.save_json(path,artifact)
            self.journal.save_json(
                f"verified_outcomes/{market}/latest.json",
                artifact,
            )
            self.journal.append_jsonl(
                "verified_outcomes/ledger.jsonl",
                {
                    "market_id":market,
                    "evidence_id":evidence_id,
                    "decision_id":lineage.get("decision_id"),
                    "t0_market_as_of":lineage.get("market_as_of"),
                    "outcome_as_of":normalized.get("outcome_as_of"),
                    "observation_days":normalized.get("observation_days"),
                    "triaid_excess_vs_baseline":normalized.get("triaid_excess_vs_baseline"),
                    "outcome_hash_sha256":outcome_hash,
                    "resolved_at_utc":resolved_at,
                },
            )
        return {**artifact,"changed":changed}

    def resolve_market(self,market_id:str,limit:int=50)->dict:
        market=str(market_id).upper()
        ledger=self.evidence_repository.recent(market,limit)
        evaluated=[]
        waiting=[]
        for row in ledger:
            evidence=self.evidence_repository.get(
                market,str(row.get("evidence_id") or "")
            )
            if not evidence:
                continue
            result=self.resolve_evidence(evidence)
            if result.get("state")=="EVALUATED":
                evaluated.append(result)
            else:
                waiting.append(result)
        latest=self.journal.load_json(
            f"verified_outcomes/{market}/latest.json",
            default={},
        )
        return {
            "resolver_version":self.version,
            "market_id":market,
            "formal_evidence_count":len(ledger),
            "evaluated_count":len(evaluated),
            "waiting_count":len(waiting),
            "latest_evaluated":latest or None,
            "latest_waiting":waiting[-1] if waiting else None,
            "rule":"T0_FORMAL_EVIDENCE_IS_PAIRED_ONLY_WITH_EXISTING_AUDITED_T1_RESULTS; RESOLVER_DOES_NOT_RECOMPUTE_RETURNS",
        }

    def history(self,market_id:str,limit:int=200)->list[dict]:
        market=str(market_id).upper()
        rows=self.journal.read_jsonl(
            "verified_outcomes/ledger.jsonl",
            limit=max(200,int(limit)*4),
        )
        latest_by_evidence={}
        for row in rows:
            if str(row.get("market_id") or "").upper()!=market:
                continue
            evidence_id=str(row.get("evidence_id") or "")
            if not evidence_id:
                continue
            latest_by_evidence[evidence_id]=dict(row)
        ordered=sorted(
            latest_by_evidence.values(),
            key=lambda row:(
                str(row.get("outcome_as_of") or ""),
                str(row.get("resolved_at_utc") or ""),
                str(row.get("evidence_id") or ""),
            ),
        )
        return ordered[-int(limit):]

    def latest(self,market_id:str)->dict:
        market=str(market_id).upper()
        return self.journal.load_json(
            f"verified_outcomes/{market}/latest.json",
            default={},
        )
