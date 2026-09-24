from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .contracts import RunRecord
from .capital_capacity import CAPITAL_SLEEVES_CNY
from .us_return_max import USD_CAPITAL_SLEEVES
from .hk_return_max import HKD_CAPITAL_SLEEVES


VERSION = "daily-experiment-intelligence@0.1.0"


def _as_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _primary_mode(market_id: str) -> str:
    market = str(market_id).upper()
    return {
        "US": "US_RETURN_MAX_CAPACITY",
        "CN": "CN_RETURN_MAX_CAPACITY",
        "HK": "HK_RETURN_MAX_CAPACITY",
    }.get(market, "")


def _official_primary_runs(runs: Iterable[RunRecord], market_id: str) -> list[RunRecord]:
    market = str(market_id).upper()
    mode = _primary_mode(market)
    rows = []
    for run in runs:
        metadata = run.market.metadata or {}
        if str(run.market.market_id).upper() != market:
            continue
        if str(metadata.get("experiment_mode") or "").upper() != mode:
            continue
        if metadata.get("evidence_eligible") is False:
            continue
        if str(metadata.get("run_scope") or "OFFICIAL_EVIDENCE") == "MANUAL_PREVIEW":
            continue
        if metadata.get("primary_reference_superseded") is True or run.status == "SUPERSEDED":
            continue
        rows.append(run)
    return sorted(rows, key=lambda r: (r.market.as_of, r.created_at, r.run_id))


def _latest_evaluated(rows: list[RunRecord]) -> RunRecord | None:
    eligible = [
        run for run in rows
        if run.evaluation
        and run.evaluation.status == "EVALUATED"
        and (run.market.metadata or {}).get("daily_bar_complete") is not False
    ]
    return eligible[-1] if eligible else None


def _latest_decision(rows: list[RunRecord]) -> RunRecord | None:
    eligible = [
        run for run in rows
        if run.strategy_group is not None
        and run.triaid_decision is not None
        and run.status in {"DECISION_READY_AWAITING_OUTCOME", "VERIFIED"}
    ]
    return eligible[-1] if eligible else None


def _attribution(run: RunRecord | None) -> dict:
    if run is None or run.evaluation is None or run.evaluation.status != "EVALUATED":
        return {
            "status": "PENDING",
            "positive": [],
            "negative": [],
            "gross_intervention_effect": None,
            "trading_cost": None,
            "net_excess_return": None,
        }
    ev = run.evaluation
    ids = set(ev.baseline_contributions) | set(ev.triaid_contributions)
    rows = []
    for strategy_id in ids:
        baseline = float(ev.baseline_contributions.get(strategy_id, 0.0))
        triaid = float(ev.triaid_contributions.get(strategy_id, 0.0))
        rows.append({
            "strategy_id": strategy_id,
            "baseline_contribution": baseline,
            "triaid_contribution": triaid,
            "delta": triaid - baseline,
            "realized_strategy_return": _as_float(ev.strategy_realized_returns.get(strategy_id)),
        })
    positive = sorted([x for x in rows if x["delta"] > 1e-15], key=lambda x: x["delta"], reverse=True)
    negative = sorted([x for x in rows if x["delta"] < -1e-15], key=lambda x: x["delta"])
    net = float(ev.excess_return or 0.0)
    trading_cost = float(ev.trading_cost or 0.0)
    return {
        "status": "EVALUATED",
        "positive": positive,
        "negative": negative,
        "gross_intervention_effect": net + trading_cost,
        "trading_cost": trading_cost,
        "net_excess_return": net,
        "positive_intervention_count": len(positive),
        "negative_intervention_count": len(negative),
    }


def _scorecard(run: RunRecord | None) -> dict:
    if run is None or run.evaluation is None or run.evaluation.status != "EVALUATED":
        return {
            "status": "PENDING_OUTCOME",
            "decision_as_of": run.market.as_of if run else None,
        }
    ev = run.evaluation
    realized = {k: float(v) for k, v in ev.strategy_realized_returns.items()}
    ranked = sorted(realized.items(), key=lambda item: item[1], reverse=True)
    best_id, best_return = ranked[0] if ranked else (None, None)
    baseline = float(ev.baseline_return or 0.0)
    triaid = float(ev.triaid_return or 0.0)
    excess = float(ev.excess_return or 0.0)
    return {
        "status": "EVALUATED",
        "decision_run_id": run.run_id,
        "decision_as_of": run.market.as_of,
        "baseline_realized_return": baseline,
        "triaid_realized_return": triaid,
        "realized_excess_return": excess,
        "trading_cost": float(ev.trading_cost or 0.0),
        "hindsight_best_strategy_id": best_id,
        "hindsight_best_strategy_return": best_return,
        "opportunity_gap_to_hindsight_best": (
            best_return - triaid if best_return is not None else None
        ),
        "baseline_gap_to_hindsight_best": (
            best_return - baseline if best_return is not None else None
        ),
        "hindsight_semantics": (
            "The best realized strategy is an opportunity ceiling only. "
            "It must not be used as if it were knowable at decision time."
        ),
    }


def _capitalized_scorecard(market_id: str, scorecard: dict) -> dict:
    market = str(market_id).upper()
    if market == "US":
        currency, sleeves = "USD", USD_CAPITAL_SLEEVES
    elif market == "CN":
        currency, sleeves = "CNY", CAPITAL_SLEEVES_CNY
    elif market == "HK":
        currency, sleeves = "HKD", HKD_CAPITAL_SLEEVES
    else:
        currency, sleeves = "NATIVE", ()
    baseline = scorecard.get("baseline_realized_return")
    triaid = scorecard.get("triaid_realized_return")
    excess = scorecard.get("realized_excess_return")
    cost = scorecard.get("trading_cost")
    rows = []
    for capital in sleeves:
        capital = float(capital)
        rows.append({
            "starting_capital": capital,
            "baseline_realized_pnl": capital * baseline if baseline is not None else None,
            "triaid_realized_pnl": capital * triaid if triaid is not None else None,
            "realized_excess_pnl": capital * excess if excess is not None else None,
            "trading_cost_amount": capital * cost if cost is not None else None,
        })
    return {
        "currency": currency,
        "rows": rows,
        "semantics": "Amounts scale the latest evaluated primary-route return. They are realized analytical P&L equivalents, not a forecast.",
    }


def _transition_summary(store, market_id: str, session_date: str, events: list[dict] | None = None) -> dict:
    market = str(market_id).upper()
    source_events = events if events is not None else store.read_jsonl("decision_events.jsonl", limit=10000)
    events = [
        row for row in source_events
        if str(row.get("market_id") or "").upper() == market
        and str(row.get("session_date") or "") == str(session_date)
    ]
    events.sort(key=lambda row: str(row.get("created_at") or ""))
    recomputed = [r for r in events if r.get("event_type") == "TRANSITION_RESEARCH_DECISION"]
    skipped = [r for r in events if r.get("event_type") == "TRANSITION_SKIPPED"]
    close_rows = [r for r in events if str(r.get("event_type") or "").startswith("CLOSE_")]

    regimes = []
    urgent = 0
    allocation = 0
    l1_values = []
    first_risk_off = None
    last_risk_off = None
    first_risk_on = None
    last_risk_on = None
    for row in recomputed:
        assessment = row.get("assessment") or {}
        decision = row.get("decision") or {}
        regime = str(decision.get("transition_regime") or "")
        if regime:
            regimes.append(regime)
        if assessment.get("urgent") is True:
            urgent += 1
        if decision.get("allocation_change_recommended") is True:
            allocation += 1
        l1 = _as_float(decision.get("weight_change_l1_vs_reference"))
        if l1 is not None:
            l1_values.append(l1)
        created = row.get("created_at")
        if regime == "intraday_risk_off":
            first_risk_off = first_risk_off or created
            last_risk_off = created
        elif regime == "intraday_risk_on":
            first_risk_on = first_risk_on or created
            last_risk_on = created

    switches = 0
    for before, after in zip(regimes, regimes[1:]):
        if before != after:
            switches += 1
    risk_off_count = sum(1 for x in regimes if x == "intraday_risk_off")
    risk_on_count = sum(1 for x in regimes if x == "intraday_risk_on")
    oscillation = bool(
        risk_off_count > 0
        and risk_on_count > 0
        and switches >= 2
    )

    skipped_reasons = {}
    for row in skipped:
        reason = str((row.get("assessment") or {}).get("reason") or "UNKNOWN")
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    return {
        "session_date": session_date,
        "recomputed_count": len(recomputed),
        "skipped_count": len(skipped),
        "risk_off_decisions": risk_off_count,
        "risk_on_decisions": risk_on_count,
        "regime_switch_count": switches,
        "urgent_event_count": urgent,
        "allocation_change_recommended_count": allocation,
        "first_risk_off_at": first_risk_off,
        "last_risk_off_at": last_risk_off,
        "first_risk_on_at": first_risk_on,
        "last_risk_on_at": last_risk_on,
        "mean_weight_change_l1": (
            sum(l1_values) / len(l1_values) if l1_values else None
        ),
        "max_weight_change_l1": max(l1_values) if l1_values else None,
        "oscillation_flag": oscillation,
        "skipped_reason_counts": dict(sorted(skipped_reasons.items())),
        "close_event": close_rows[-1] if close_rows else None,
        "discipline": (
            "High-frequency observations are evidence. Recomputed transition decisions remain "
            "research-only and are not broker execution."
        ),
    }


def _next_decision(run: RunRecord | None) -> dict:
    if run is None:
        return {"status": "MISSING"}
    diagnostic = run.diagnostic_summary or {}
    decision_diag = run.triaid_decision.diagnostics if run.triaid_decision else {}
    return {
        "status": run.status,
        "run_id": run.run_id,
        "as_of": run.market.as_of,
        "regime": run.market.regime,
        "projected_baseline_state_return": _as_float(
            diagnostic.get("projected_baseline_expected_return")
        ),
        "projected_triaid_state_return": _as_float(
            diagnostic.get("projected_triaid_expected_return")
        ),
        "projected_state_return_gap": _as_float(
            diagnostic.get("projected_excess_expected_return")
        ),
        "projection_semantics": diagnostic.get("projection_basis")
        or "Historical/model state-return estimate, not a calibrated forecast.",
        "kept_strategy_ids": list(decision_diag.get("kept_strategy_ids") or []),
        "risk_off_detected": decision_diag.get("risk_off_detected"),
        "intervention_strength": _as_float(decision_diag.get("intervention_strength")),
    }


def _gap_diagnosis(scorecard: dict, transition: dict, attribution: dict) -> dict:
    excess = scorecard.get("realized_excess_return")
    has_transition_signal = transition.get("recomputed_count", 0) > 0
    oscillation = bool(transition.get("oscillation_flag"))
    if excess is None:
        layer = "OUTCOME_PENDING"
        lesson = "The next frozen outcome is still pending. Preserve the decision and wait for prospective resolution."
    elif excess < 0 and has_transition_signal and oscillation:
        layer = "TRANSITION_TO_INTERVENTION_TIMESCALE"
        lesson = (
            "Transition evidence was present, but rapid risk-on/risk-off switching and the realized "
            "underperformance indicate that action timing, persistence and intervention strength need calibration."
        )
    elif excess < 0 and has_transition_signal:
        layer = "TRANSITION_TO_INTERVENTION"
        lesson = (
            "Transition evidence was present, but it did not convert into positive realized excess return. "
            "Inspect action timing, strategy mapping and intervention strength."
        )
    elif excess < 0:
        layer = "STATE_OR_SELECTION"
        lesson = (
            "The frozen TRIAID allocation underperformed without a strong intraday transition explanation. "
            "Inspect state estimation and strategy ranking before changing the action layer."
        )
    elif excess > 0:
        layer = "ECONOMIC_ALIGNMENT"
        lesson = (
            "TRIAID produced positive realized excess return. The next question is whether the gain repeats "
            "prospectively and can be attributed to the intended state, transition and intervention mechanism."
        )
    else:
        layer = "NEUTRAL"
        lesson = "TRIAID matched the baseline. Use attribution and transition evidence to identify whether the equality was structural or incidental."

    gross = attribution.get("gross_intervention_effect")
    cost = attribution.get("trading_cost")
    return {
        "primary_gap_layer": layer,
        "evolution_to_economic_alignment": (
            "UNRESOLVED" if excess is None
            else "NEGATIVE" if excess < 0
            else "POSITIVE" if excess > 0
            else "FLAT"
        ),
        "realized_excess_return": excess,
        "gross_intervention_effect_before_reported_trading_cost": gross,
        "reported_trading_cost": cost,
        "lesson": lesson,
    }


def _counterfactual_plan(transition: dict) -> dict:
    event_count = int(transition.get("recomputed_count") or 0)
    return {
        "status": "REPLAY_REQUIRED" if event_count else "NO_TRANSITION_DECISIONS",
        "frozen_transition_decisions_available": event_count,
        "comparisons": [
            "Prior frozen allocation with no intraday intervention",
            "First salient risk-off intervention",
            "Persistence-confirmed intervention after consecutive state confirmation",
            "Lower intervention strength with the same state signal",
            "Higher intervention strength only as a shadow comparison",
        ],
        "data_discipline": (
            "Replay must use timestamp-aligned market information available after each frozen decision. "
            "Full-day hindsight returns must not be substituted for intraday counterfactual returns."
        ),
    }


def build_market_intelligence(
    runs: Iterable[RunRecord],
    store,
    market_id: str,
    session_date: str,
    events: list[dict] | None = None,
) -> dict:
    rows = _official_primary_runs(runs, market_id)
    evaluated = _latest_evaluated(rows)
    decision = _latest_decision(rows)
    scorecard = _scorecard(evaluated)
    attribution = _attribution(evaluated)
    evaluated_diagnostics=(evaluated.diagnostic_summary or {}) if evaluated else {}
    decision_diagnostics=(decision.diagnostic_summary or {}) if decision else {}
    policy_triage=evaluated_diagnostics.get("policy_triage") or decision_diagnostics.get("policy_triage")
    policy_triage_outcome=evaluated_diagnostics.get("policy_triage_outcome")
    transition = _transition_summary(store, market_id, session_date, events=events)
    next_decision = _next_decision(decision)
    gap = _gap_diagnosis(scorecard, transition, attribution)

    return {
        "version": VERSION,
        "market_id": str(market_id).upper(),
        "session_date": session_date,
        "highest_discipline": {
            "internal_goal": "Validate and evolve TRIAID prospectively.",
            "external_goal": "Maximize long-run realizable net return.",
            "joint_goal": (
                "Continuously reduce the distance between TRIAID capability gains and economic gains "
                "without contaminating evidence with hindsight."
            ),
            "no_hindsight_contamination": True,
        },
        "realized_scorecard": scorecard,
        "capitalized_scorecard": _capitalized_scorecard(market_id, scorecard),
        "intervention_attribution": attribution,
        "policy_triage": policy_triage,
        "policy_triage_outcome": policy_triage_outcome,
        "transition_evidence": transition,
        "goal_gap": gap,
        "next_frozen_decision": next_decision,
        "counterfactual_replay": _counterfactual_plan(transition),
        "next_validation_plan": {
            "diagnose_first": gap.get("primary_gap_layer"),
            "tests": [
                "Keep the next formal decision frozen before the outcome is known.",
                "Separate structural state ranking from fast transition overlays.",
                "Test persistence or hysteresis before promoting fast transition signals into stronger allocation changes.",
                "Measure whether lower transition-to-action error also reduces opportunity loss and improves realized excess return.",
            ],
            "promotion_rule": (
                "A change is progress only when prospective evidence improves TRIAID capability and the "
                "economic result moves in the same direction, or when a temporary economic shortfall is "
                "clearly localized to a downstream conversion layer that is then prospectively repaired."
            ),
        },
    }


def build_cross_market_learning(
    runs: Iterable[RunRecord],
    store,
    session_dates: dict[str, str],
    events: list[dict] | None = None,
) -> dict:
    rows = []
    for market in ("US", "CN", "HK"):
        day = session_dates.get(market)
        if not day:
            continue
        intelligence = build_market_intelligence(runs, store, market, day, events=events)
        score = intelligence["realized_scorecard"]
        gap = intelligence["goal_gap"]
        trans = intelligence["transition_evidence"]
        rows.append({
            "market_id": market,
            "session_date": day,
            "realized_excess_return": score.get("realized_excess_return"),
            "primary_gap_layer": gap.get("primary_gap_layer"),
            "lesson": gap.get("lesson"),
            "transition_recomputes": trans.get("recomputed_count"),
            "risk_off_decisions": trans.get("risk_off_decisions"),
            "risk_on_decisions": trans.get("risk_on_decisions"),
            "regime_switch_count": trans.get("regime_switch_count"),
            "oscillation_flag": trans.get("oscillation_flag"),
        })

    shared = []
    if any(row.get("oscillation_flag") for row in rows):
        shared.append({
            "hypothesis": "TRANSITION_PERSISTENCE_AND_HYSTERESIS",
            "reason": (
                "At least one market showed repeated opposite transition regimes in the same session. "
                "All markets should test persistence-aware action gates, while keeping market-specific thresholds."
            ),
        })
    if any((row.get("realized_excess_return") or 0.0) < 0 for row in rows):
        shared.append({
            "hypothesis": "STATE_TO_ACTION_CONVERSION",
            "reason": (
                "At least one market has a negative realized TRIAID gap. Compare whether the error arose in "
                "state ranking, transition recognition, strategy mapping, intervention strength or trading cost."
            ),
        })
    return {
        "version": VERSION,
        "markets": rows,
        "shared_hypotheses": shared,
        "transfer_rule": (
            "Markets share hypotheses, failure modes and validation designs only. "
            "They do not borrow another market's weights, outcomes or thresholds as evidence."
        ),
    }
