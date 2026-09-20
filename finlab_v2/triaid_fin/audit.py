from __future__ import annotations

from .contracts import AuditReceipt, RunRecord


class AuditModule:
    version = "audit@0.1.0"

    def audit(self, run: RunRecord) -> AuditReceipt:
        decision = run.triaid_decision
        group = run.strategy_group

        checks = {
            "module_manifest_present": bool(run.module_manifest),
            "market_snapshot_present": bool(run.market.snapshot_id),
            "strategy_group_present": group is not None,
            "decision_present": decision is not None,
        }

        if group is not None:
            checks["group_weights_nonnegative"] = all(v >= 0 for v in group.weights.values())
            checks["group_weights_sum"] = sum(group.weights.values()) <= 1.0000001
        else:
            checks["group_weights_nonnegative"] = False
            checks["group_weights_sum"] = False

        if decision is not None:
            checks["decision_weights_nonnegative"] = all(v >= 0 for v in decision.weights_after.values())
            checks["decision_weights_sum"] = sum(decision.weights_after.values()) <= 1.0000001
        else:
            checks["decision_weights_nonnegative"] = False
            checks["decision_weights_sum"] = False

        passed = all(checks.values())
        notes = []
        if run.evaluation is None or run.evaluation.status != "EVALUATED":
            notes.append("Decision is structurally auditable but market outcome has not been submitted yet.")

        return AuditReceipt(passed=passed, checks=checks, notes=notes)
