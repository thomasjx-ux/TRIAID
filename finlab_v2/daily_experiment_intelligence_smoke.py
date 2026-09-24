from __future__ import annotations

from triaid_fin.contracts import (
    BilingualText,
    EvaluationResult,
    MarketSnapshot,
    RunRecord,
    StrategyGroup,
    StrategyState,
    TriaidDecision,
)
from triaid_fin.daily_experiment_intelligence import (
    VERSION,
    build_cross_market_learning,
    build_market_intelligence,
)


def bt(text: str) -> BilingualText:
    return BilingualText(zh=text, en=text)


class FakeStore:
    def __init__(self, events):
        self._events = list(events)

    def read_jsonl(self, name: str, limit: int = 100):
        if name == "decision_events.jsonl":
            return self._events[-limit:]
        return []


def state(strategy_id: str, expected: float) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        expected_net_return=expected,
        lifecycle="active",
        selection_reason=bt(strategy_id),
    )


def run(
    run_id: str,
    as_of: str,
    status: str,
    evaluation: EvaluationResult | None,
    weights_before: dict[str, float],
    weights_after: dict[str, float],
) -> RunRecord:
    members=list(weights_before)
    return RunRecord(
        run_id=run_id,
        status=status,
        module_manifest={"review": "review@0.7.0"},
        market=MarketSnapshot(
            market_id="HK",
            as_of=as_of,
            snapshot_id=f"HK:{as_of}:FINAL:test",
            regime="mixed",
            metadata={
                "experiment_mode": "HK_RETURN_MAX_CAPACITY",
                "run_scope": "OFFICIAL_EVIDENCE",
                "evidence_eligible": True,
                "daily_bar_complete": True,
            },
        ),
        strategy_states=[
            state("P00_BUY_HOLD", 0.01),
            state("P10_VOL20", 0.008),
            state("P16_REV5", -0.01),
        ],
        strategy_group=StrategyGroup(
            group_version="g",
            config_version="c",
            market_id="HK",
            members=members,
            weights=weights_before,
            reasons={sid: bt(sid) for sid in members},
        ),
        triaid_decision=TriaidDecision(
            core_version="triaid-core-v2@0.2.0",
            weights_before=weights_before,
            weights_after=weights_after,
            reasons={sid: bt(sid) for sid in set(weights_before)|set(weights_after)},
            diagnostics={
                "kept_strategy_ids": ["P00_BUY_HOLD", "P10_VOL20"],
                "risk_off_detected": False,
                "intervention_strength": 0.55,
            },
        ),
        evaluation=evaluation,
        diagnostic_summary={
            "projected_baseline_expected_return": -0.02,
            "projected_triaid_expected_return": -0.014,
            "projected_excess_expected_return": 0.006,
            "projection_basis": "TEST_STATE_ESTIMATE_NOT_FORECAST",
        },
    )


evaluated=run(
    "HK-prev",
    "2026-09-23",
    "VERIFIED",
    EvaluationResult(
        status="EVALUATED",
        baseline_return=-0.0018,
        triaid_return=-0.0020,
        excess_return=-0.0002,
        trading_cost=0.00005,
        strategy_realized_returns={
            "P00_BUY_HOLD": -0.0024,
            "P10_VOL20": -0.0024,
            "P16_REV5": 0.0,
        },
        baseline_contributions={
            "P00_BUY_HOLD": -0.00096,
            "P10_VOL20": -0.00072,
            "P16_REV5": 0.0,
        },
        triaid_contributions={
            "P00_BUY_HOLD": -0.00120,
            "P10_VOL20": -0.00072,
            "P16_REV5": 0.0,
        },
    ),
    {"P00_BUY_HOLD": 0.4, "P10_VOL20": 0.3, "P16_REV5": 0.3},
    {"P00_BUY_HOLD": 0.5, "P10_VOL20": 0.3, "P16_REV5": 0.2},
)

pending=run(
    "HK-current",
    "2026-09-24",
    "DECISION_READY_AWAITING_OUTCOME",
    EvaluationResult(status="PENDING_OUTCOME"),
    {"P00_BUY_HOLD": 0.4, "P10_VOL20": 0.4, "P16_REV5": 0.2},
    {"P00_BUY_HOLD": 0.5, "P10_VOL20": 0.45, "P16_REV5": 0.05},
)

events=[
    {
        "session_date": "2026-09-24",
        "market_id": "HK",
        "created_at": "2026-09-24T02:46:52+00:00",
        "event_type": "TRANSITION_RESEARCH_DECISION",
        "assessment": {"urgent": True},
        "decision": {
            "transition_regime": "intraday_risk_off",
            "allocation_change_recommended": True,
            "weight_change_l1_vs_reference": 0.79,
        },
    },
    {
        "session_date": "2026-09-24",
        "market_id": "HK",
        "created_at": "2026-09-24T02:48:25+00:00",
        "event_type": "TRANSITION_RESEARCH_DECISION",
        "assessment": {"urgent": False},
        "decision": {
            "transition_regime": "intraday_risk_on",
            "allocation_change_recommended": True,
            "weight_change_l1_vs_reference": 0.24,
        },
    },
    {
        "session_date": "2026-09-24",
        "market_id": "HK",
        "created_at": "2026-09-24T03:00:00+00:00",
        "event_type": "TRANSITION_RESEARCH_DECISION",
        "assessment": {"urgent": False},
        "decision": {
            "transition_regime": "intraday_risk_off",
            "allocation_change_recommended": True,
            "weight_change_l1_vs_reference": 0.55,
        },
    },
    {
        "session_date": "2026-09-24",
        "market_id": "HK",
        "created_at": "2026-09-24T03:01:00+00:00",
        "event_type": "TRANSITION_SKIPPED",
        "assessment": {"reason": "MIN_RECOMPUTE_INTERVAL"},
    },
    {
        "session_date": "2026-09-24",
        "market_id": "HK",
        "created_at": "2026-09-24T08:10:13+00:00",
        "event_type": "CLOSE_FINAL",
    },
]

store=FakeStore(events)
report=build_market_intelligence([evaluated,pending],store,"HK","2026-09-24")

assert report["version"]==VERSION
assert report["highest_discipline"]["no_hindsight_contamination"] is True
score=report["realized_scorecard"]
assert score["status"]=="EVALUATED"
assert score["hindsight_best_strategy_id"]=="P16_REV5"
assert abs(score["opportunity_gap_to_hindsight_best"]-0.0020)<1e-12
cap=report["capitalized_scorecard"]
assert cap["currency"]=="HKD"
assert len(cap["rows"])==4
assert abs(cap["rows"][0]["realized_excess_pnl"]-cap["rows"][0]["starting_capital"]*(-0.0002))<1e-9

attr=report["intervention_attribution"]
assert attr["status"]=="EVALUATED"
assert abs(attr["gross_intervention_effect"]+0.00015)<1e-12
assert abs(attr["trading_cost"]-0.00005)<1e-12
assert attr["negative"][0]["strategy_id"]=="P00_BUY_HOLD"

trans=report["transition_evidence"]
assert trans["recomputed_count"]==3
assert trans["skipped_count"]==1
assert trans["risk_off_decisions"]==2
assert trans["risk_on_decisions"]==1
assert trans["regime_switch_count"]==2
assert trans["oscillation_flag"] is True
assert trans["urgent_event_count"]==1

gap=report["goal_gap"]
assert gap["primary_gap_layer"]=="TRANSITION_TO_INTERVENTION_TIMESCALE"
assert gap["evolution_to_economic_alignment"]=="NEGATIVE"

cf=report["counterfactual_replay"]
assert cf["status"]=="REPLAY_REQUIRED"
assert "timestamp-aligned" in cf["data_discipline"]

cross=build_cross_market_learning(
    [evaluated,pending],
    store,
    {"HK":"2026-09-24"},
)
assert cross["markets"][0]["market_id"]=="HK"
assert any(x["hypothesis"]=="TRANSITION_PERSISTENCE_AND_HYSTERESIS" for x in cross["shared_hypotheses"])
assert "do not borrow" in cross["transfer_rule"]

print("TRIAID_DAILY_EXPERIMENT_INTELLIGENCE_SMOKE_PASS")
