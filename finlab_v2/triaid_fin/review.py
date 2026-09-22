from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from .contracts import RunRecord


class ReviewModule:
    version="review@0.4.1"

    @staticmethod
    def _evidence_eligible(run:RunRecord)->bool:
        metadata=run.market.metadata or {}
        return (
            metadata.get("evidence_eligible") is not False
            and str(metadata.get("run_scope") or "OFFICIAL_EVIDENCE")!="MANUAL_PREVIEW"
            and run.status!="PREVIEW_READY"
        )

    @staticmethod
    def _primary_mode(market_id:str)->str:
        market_id=str(market_id).upper()
        return "CN_RETURN_MAX_CAPACITY" if market_id=="CN" else "US_RETURN_MAX_CAPACITY" if market_id=="US" else ""

    @classmethod
    def _primary_route(cls,run:RunRecord)->bool:
        expected=cls._primary_mode(run.market.market_id)
        return bool(
            expected
            and str((run.market.metadata or {}).get("experiment_mode") or "").upper()==expected
        )

    def daily_summary(self,runs:Iterable[RunRecord])->dict:
        rows=[
            r for r in runs
            if r.market.snapshot_id!="PENDING"
            and self._evidence_eligible(r)
            and self._primary_route(r)
        ]
        dates=sorted({r.market.as_of for r in rows if r.market.as_of})
        target_date=dates[-1] if dates else datetime.now(timezone.utc).date().isoformat()
        day=[r for r in rows if r.market.as_of==target_date]
        evaluated=[
            r for r in day
            if r.evaluation and r.evaluation.status=="EVALUATED"
            and (r.market.metadata or {}).get("daily_bar_complete") is not False
        ]
        excess=sum(float(r.evaluation.excess_return or 0.0) for r in evaluated)
        latest=day[-1] if day else None

        analysis=[]
        for r in evaluated:
            x=float(r.evaluation.excess_return or 0.0)
            if x>0:
                analysis.append(f"{r.market.market_id}: TRIAID posterior return exceeded its baseline by {x:+.4%}.")
            elif x<0:
                analysis.append(f"{r.market.market_id}: TRIAID posterior return was below its baseline by {abs(x):.4%}; inspect weight-adjustment attribution.")
            else:
                analysis.append(f"{r.market.market_id}: TRIAID posterior return matched its baseline for this evaluated period.")

        return {
            "date":target_date,
            "runs":len(day),
            "evaluated_runs":len(evaluated),
            "cumulative_excess_return_for_day":excess,
            "analysis":analysis,
            "latest_market_regime":latest.market.regime if latest else None,
            "runs_detail":[self._detail(r) for r in day],
        }

    def _detail(self,r:RunRecord)->dict:
        states={
            s.strategy_id:{
                "lifecycle":s.lifecycle,
                "expected_net_return":s.expected_net_return,
                "risk":s.risk,
                "uncertainty":s.uncertainty,
                "metrics":s.metrics,
            }
            for s in r.strategy_states
        }
        return {
            "run_id":r.run_id,
            "market_id":r.market.market_id,
            "as_of":r.market.as_of,
            "snapshot_id":r.market.snapshot_id,
            "regime":r.market.regime,
            "status":r.status,
            "experiment_mode":(r.market.metadata or {}).get("experiment_mode"),
            "market_route":(r.market.metadata or {}).get("market_route"),
            "primary_reference_bootstrap":(r.market.metadata or {}).get("primary_reference_bootstrap"),
            "module_manifest":r.module_manifest,
            "strategy_states":states,
            "strategy_group":{
                "members":r.strategy_group.members,
                "weights":r.strategy_group.weights,
                "reasons":{k:v.model_dump() for k,v in r.strategy_group.reasons.items()},
            } if r.strategy_group else None,
            "triaid_decision":{
                "core_version":r.triaid_decision.core_version,
                "weights_before":r.triaid_decision.weights_before,
                "weights_after":r.triaid_decision.weights_after,
                "reasons":{k:v.model_dump() for k,v in r.triaid_decision.reasons.items()},
                "diagnostics":r.triaid_decision.diagnostics,
            } if r.triaid_decision else None,
            "evaluation":r.evaluation.model_dump() if r.evaluation else None,
            "diagnostic_summary":r.diagnostic_summary,
            "audit":r.audit.model_dump() if r.audit else None,
        }

    def curves(self,runs:Iterable[RunRecord])->List[dict]:
        ordered=sorted(
            [
                r for r in runs
                if r.evaluation and r.evaluation.status=="EVALUATED"
                and self._evidence_eligible(r)
                and self._primary_route(r)
                and (r.market.metadata or {}).get("daily_bar_complete") is not False
            ],
            key=lambda r:(r.market.as_of,r.created_at),
        )
        baseline_equity=1.0
        triaid_equity=1.0
        cumulative_excess=0.0
        points=[]
        for run in ordered:
            baseline_return=float(run.evaluation.baseline_return or 0.0)
            triaid_return=float(run.evaluation.triaid_return or 0.0)
            excess=float(run.evaluation.excess_return or 0.0)
            baseline_equity*=1.0+baseline_return
            triaid_equity*=1.0+triaid_return
            cumulative_excess+=excess
            points.append({
                "time":run.market.as_of or run.created_at,
                "run_id":run.run_id,
                "market_id":run.market.market_id,
                "baseline_equity":baseline_equity,
                "triaid_equity":triaid_equity,
                "cumulative_excess_return":cumulative_excess,
                "excess_equity_gap":triaid_equity-baseline_equity,
            })
        return points
