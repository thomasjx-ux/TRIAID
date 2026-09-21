from __future__ import annotations

import math

from .contracts import AuditReceipt, RunRecord


class AuditModule:
    version="audit@0.3.0"

    def audit(self,run:RunRecord)->AuditReceipt:
        decision=run.triaid_decision
        group=run.strategy_group
        checks={
            "module_manifest_present":bool(run.module_manifest),
            "market_snapshot_present":bool(run.market.snapshot_id and run.market.snapshot_id!="PENDING"),
            "market_as_of_present":bool(run.market.as_of),
            "strategy_states_present":bool(run.strategy_states),
            "strategy_group_present":group is not None,
            "decision_present":decision is not None,
        }

        if group is not None:
            checks["group_members_unique"]=len(group.members)==len(set(group.members))
            checks["group_weights_finite"]=all(math.isfinite(float(v)) for v in group.weights.values())
            checks["group_weights_nonnegative"]=all(float(v)>=0 for v in group.weights.values())
            checks["group_weights_sum"]=sum(float(v) for v in group.weights.values())<=1.0000001
            checks["group_weights_members_only"]=all(k in group.members for k in group.weights)
        else:
            checks.update({
                "group_members_unique":False,
                "group_weights_finite":False,
                "group_weights_nonnegative":False,
                "group_weights_sum":False,
                "group_weights_members_only":False,
            })

        if decision is not None and group is not None:
            checks["decision_weights_finite"]=all(math.isfinite(float(v)) for v in decision.weights_after.values())
            checks["decision_weights_nonnegative"]=all(float(v)>=0 for v in decision.weights_after.values())
            checks["decision_weights_sum"]=sum(float(v) for v in decision.weights_after.values())<=1.0000001
            checks["decision_members_from_group"]=all(k in group.members for k in decision.weights_after)
            before_keys=set(decision.weights_before)|set(group.weights)
            checks["decision_before_matches_group"]=all(
                abs(float(decision.weights_before.get(k,0.0))-float(group.weights.get(k,0.0)))<=1e-12
                for k in before_keys
            )
            checks["core_version_recorded"]=bool(decision.core_version)
        else:
            checks.update({
                "decision_weights_finite":False,
                "decision_weights_nonnegative":False,
                "decision_weights_sum":False,
                "decision_members_from_group":False,
                "decision_before_matches_group":False,
                "core_version_recorded":False,
            })

        if run.evaluation and run.evaluation.status=="EVALUATED":
            values=(
                run.evaluation.baseline_return,
                run.evaluation.triaid_return,
                run.evaluation.excess_return,
                run.evaluation.trading_cost,
                *run.evaluation.strategy_realized_returns.values(),
                *run.evaluation.baseline_contributions.values(),
                *run.evaluation.triaid_contributions.values(),
            )
            checks["evaluation_finite"]=all(
                x is not None and math.isfinite(float(x))
                for x in values
            )
            baseline=float(run.evaluation.baseline_return or 0.0)
            triaid=float(run.evaluation.triaid_return or 0.0)
            excess=float(run.evaluation.excess_return or 0.0)
            checks["evaluation_arithmetic"]=abs(excess-(triaid-baseline))<=1e-12
            checks["evaluation_cost_nonnegative"]=float(run.evaluation.trading_cost)>=0

        passed=all(checks.values())
        notes=[]
        if run.evaluation is None or run.evaluation.status!="EVALUATED":
            notes.append("Decision is structurally audited; market outcome remains pending until the next observable period.")
        return AuditReceipt(passed=passed,checks=checks,notes=notes)
