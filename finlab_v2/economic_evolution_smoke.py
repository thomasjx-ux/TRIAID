from __future__ import annotations

from triaid_fin.contracts import (
    AuditReceipt,
    EvaluationResult,
    MarketSnapshot,
    RunRecord,
    StrategyGroup,
    TriaidDecision,
)
from triaid_fin.economic_evolution import EconomicEvolutionModule


def run(day: str, a: float, b: float, *, account_id: str = "GLOBAL", primary: bool = True) -> RunRecord:
    mode = "US_RETURN_MAX_CAPACITY" if primary else "SECONDARY_EXPERIMENT"
    return RunRecord(
        run_id=f"ECON-{day}-{account_id}-{mode}",
        created_at=f"{day}T15:00:00+00:00",
        status="VERIFIED",
        module_manifest={},
        account_id=account_id,
        strategy_pool_id="GLOBAL",
        market=MarketSnapshot(
            market_id="US",
            as_of=day,
            snapshot_id=f"US:{day}:CLOSE",
            metadata={
                "run_scope": "OFFICIAL_EVIDENCE",
                "daily_bar_complete": True,
                "evidence_eligible": True,
                "experiment_mode": mode,
                "base_cost_bps": 10.0,
                "account_risk_budget": 1.0,
            },
        ),
        strategy_group=StrategyGroup(
            group_version="test",
            config_version="test",
            market_id="US",
            members=["A"],
            weights={"A": 1.0},
            reasons={},
            diagnostics={"max_strategy_weight_constraint": 1.0},
        ),
        triaid_decision=TriaidDecision(
            core_version="test",
            weights_before={"A": 1.0},
            weights_after={"A": 0.5, "B": 0.5},
            reasons={},
        ),
        evaluation=EvaluationResult(
            status="EVALUATED",
            baseline_return=a,
            triaid_return=0.5 * (a + b),
            trading_cost=0.0,
            strategy_realized_returns={"A": a, "B": b},
            baseline_contributions={"A": a},
            triaid_contributions={"A": 0.5 * a, "B": 0.5 * b},
        ),
        audit=AuditReceipt(passed=True, checks={"synthetic": True}),
        diagnostic_summary={"outcome_as_of": day},
    )


model = EconomicEvolutionModule()
r1 = run("2026-09-01", 0.20, -0.10)
r2 = run("2026-09-02", -0.10, 0.20)
rows = [r1, r2]
report = model.report(rows, "US", primary_mode="US_RETURN_MAX_CAPACITY")
growth = report["net_compound_growth_proxy"]
oracle = report["hindsight_diagnostics_not_decision_time_targets"]
assert report["observations"] == 2
assert report["status"] == "EARLY_EVIDENCE_NOT_DECISION_GRADE"
assert abs(growth["baseline"] - 0.08) < 1e-12
assert abs(growth["triaid"] - 0.1025) < 1e-12
assert abs(growth["triaid_minus_baseline"] - 0.0225) < 1e-12
assert abs(oracle["best_fixed_strategy_gross_compound_return"] - 0.08) < 1e-12
assert abs(oracle["daily_clairvoyant_gross_compound_ceiling"] - 0.44) < 1e-12
assert oracle["best_fixed_strategy_gross_compound_return"] < growth["triaid"]
assert oracle["best_capped_fixed_single_plus_cash"] is not None
assert report["next_gate"]["production_core_changed"] is False

# Strict market/global/primary/audit/preview boundaries.
other = run("2026-09-03", 10.0, -0.1, account_id="TRADER_SHADOW_X")
untrusted = run("2026-09-03", 10.0, -0.1)
untrusted.audit.passed = False
secondary = run("2026-09-03", 10.0, -0.1, primary=False)
preview = run("2026-09-03", 10.0, -0.1)
preview.market.metadata["run_scope"] = "MANUAL_PREVIEW"
isolated = model.report(rows + [other, untrusted, secondary, preview], "US", primary_mode="US_RETURN_MAX_CAPACITY")
assert isolated["observations"] == 2
assert isolated["net_compound_growth_proxy"] == growth

# Duplicate frozen records cannot compound the same trading date twice.
duplicate = r2.model_copy(deep=True)
duplicate.run_id = "ECON-DUPLICATE"
duplicate.created_at = "2026-09-02T16:00:00+00:00"
dedup = model.report(rows + [duplicate], "US", primary_mode="US_RETURN_MAX_CAPACITY")
assert dedup["observations"] == 2

# Explicitly compare the same turnover-cost convention on each route.
rotating = r2.model_copy(deep=True)
rotating.strategy_group.members = ["B"]
rotating.strategy_group.weights = {"B": 1.0}
rotating.evaluation.baseline_return = 0.20
rotating.evaluation.baseline_contributions = {"B": 0.20}
rotating_report = model.report([r1, rotating], "US", primary_mode="US_RETURN_MAX_CAPACITY")
day2 = rotating_report["latest_five_completed_sessions"][-1]
assert abs(day2["baseline_full_turnover_proxy"] - 2.0) < 1e-12
assert abs(day2["baseline_modeled_cost"] - 0.002) < 1e-12
assert abs(day2["triaid_full_turnover_proxy"]) < 1e-12
assert abs(day2["triaid_modeled_cost"]) < 1e-12

# A missing allocated outcome may not silently count as zero.
missing = r2.model_copy(deep=True)
missing.evaluation.strategy_realized_returns.pop("B")
incomplete = model.report([r1, missing], "US", primary_mode="US_RETURN_MAX_CAPACITY")
assert incomplete["observations"] == 1
assert incomplete["skipped"]

empty = model.report([], "US", primary_mode="US_RETURN_MAX_CAPACITY")
assert empty["status"] == "WAITING_FOR_MATCHED_FORMAL_OUTCOMES"

print("TRIAID_ECONOMIC_EVOLUTION_SMOKE_PASS", {
    "matched_sessions": report["observations"],
    "triaid_growth": growth["triaid"],
    "best_fixed_gross": oracle["best_fixed_strategy_gross_compound_return"],
    "daily_oracle_gross": oracle["daily_clairvoyant_gross_compound_ceiling"],
    "baseline_transition_cost": day2["baseline_modeled_cost"],
    "core_unchanged": report["next_gate"]["production_core_changed"],
})
