from __future__ import annotations

from datetime import datetime, timezone

from .market_registry import market_ids, normalize_market_id


VERSION="validation-summary-projection@1.0.0"


class ValidationSummaryProjection:
    """UI read model for realized TRIAID value evidence.

    The browser consumes one projection. This class aggregates existing
    OutcomeResolver records only; it does not recompute returns.
    """

    version=VERSION

    def __init__(self,outcome_resolver)->None:
        self.outcome_resolver=outcome_resolver

    @staticmethod
    def _stats(rows:list[dict])->dict:
        xs=[
            float(row.get("triaid_excess_vs_baseline"))
            for row in rows
            if row.get("triaid_excess_vs_baseline") is not None
        ]
        positives=sum(1 for x in xs if x>0)
        negatives=sum(1 for x in xs if x<0)
        zeroes=len(xs)-positives-negatives
        running=0.0
        curve=[]
        for idx,row in enumerate(rows,1):
            value=row.get("triaid_excess_vs_baseline")
            if value is None:
                continue
            x=float(value)
            running+=x
            curve.append({
                "sample":len(curve)+1,
                "market_id":row.get("market_id"),
                "evidence_id":row.get("evidence_id"),
                "t0_market_as_of":row.get("t0_market_as_of"),
                "outcome_as_of":row.get("outcome_as_of"),
                "excess":x,
                "cumulative_sample_excess":running,
            })
        return {
            "evaluated_samples":len(xs),
            "positive_samples":positives,
            "negative_samples":negatives,
            "flat_samples":zeroes,
            "positive_rate":(positives/len(xs)) if xs else None,
            "mean_excess":(sum(xs)/len(xs)) if xs else None,
            "sample_excess_sum":sum(xs) if xs else None,
            "curve":curve,
        }

    def full(self,market_id:str)->dict:
        market=normalize_market_id(market_id)
        per_market={}
        all_rows=[]
        waiting_total=0

        for key in market_ids():
            status=self.outcome_resolver.resolve_market(key)
            history=self.outcome_resolver.history(key,200)
            history=[
                {
                    **row,
                    "market_id":key,
                }
                for row in history
            ]
            stats=self._stats(history)
            latest=status.get("latest_evaluated")
            waiting=int(status.get("waiting_count") or 0)
            waiting_total+=waiting
            per_market[key]={
                "market_id":key,
                "state":"READY" if stats["evaluated_samples"] else "WAITING",
                "formal_evidence_count":int(status.get("formal_evidence_count") or 0),
                "evaluated_count":stats["evaluated_samples"],
                "waiting_count":waiting,
                "latest_evaluated":latest,
                "stats":stats,
            }
            all_rows.extend(history)

        all_rows=sorted(
            all_rows,
            key=lambda row:(
                str(row.get("outcome_as_of") or ""),
                str(row.get("resolved_at_utc") or ""),
                str(row.get("market_id") or ""),
                str(row.get("evidence_id") or ""),
            ),
        )
        overall=self._stats(all_rows)
        overall["markets_with_evaluated_samples"]=sum(
            1 for value in per_market.values()
            if value["evaluated_count"]>0
        )
        overall["waiting_evidence"]=waiting_total

        selected=per_market[market]
        return {
            "contract_version":self.version,
            "projection_scope":"TRIAID_REALIZED_VALUE_VALIDATION",
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "market_id":market,
            "overall":overall,
            "selected_market":selected,
            "markets":per_market,
            "definitions":{
                "primary_metric":"triaid_excess_vs_baseline",
                "sample_excess_sum":"Arithmetic sum of de-duplicated evaluated T0/T1 sample excess values; research validation statistic, not account cumulative return.",
                "positive_rate":"Share of evaluated T0/T1 samples with TRIAID excess versus the frozen baseline above zero.",
                "curve":"Cumulative arithmetic sum across evaluated evidence samples ordered by T1 outcome date; not a compounded account equity curve.",
            },
            "integrity":{
                "passed":True,
                "status":"READY" if overall["evaluated_samples"] else "WAITING",
                "frontend_safe":True,
                "errors":[],
                "warnings":[] if overall["evaluated_samples"] else ["NO_EVALUATED_T1_SAMPLES_YET"],
                "rule":"UI_CONSUMES_ONE_VALIDATION_PROJECTION; PROJECTION_AGGREGATES_EXISTING_AUDITED_OUTCOMES_AND_DOES_NOT_RECOMPUTE_RETURNS",
            },
        }
