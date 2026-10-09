from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
FINLAB=ROOT/"finlab_v2"
if str(FINLAB) not in sys.path:
    sys.path.insert(0,str(FINLAB))

from triaid_fin.contracts import StrategyGroup
from triaid_fin.core import TriaidCoreModule
from triaid_fin.evolution import CoreParameters
from triaid_fin.market_lab import prepare_live_market
from triaid_fin.market_registry import MARKETS
from triaid_fin.strategy_population import StrategyPopulationModule
from triaid_fin.trading_calendar import VERSION as CALENDAR_VERSION, trading_day_info
from triaid_fin.us_route_guard import StateAwareUSReturnMaxRoute
from triaid_fin.value_frontier_shadow_v2 import allocate_shadow, VERSION as SHADOW_VERSION

PROTOCOL_VERSION="gpt-forward-validation@1.1.0"
CONSTITUTION_VERSION="triaid-constitution@1.0.0"
RUNNER_VERSION="gpt-us-independent-runner@1.1.0"
EVALUATION_VERSION="gpt-forward-evaluation@1.0.0"
CAPITAL_CASES=(100_000.0,1_000_000.0,10_000_000.0,100_000_000.0)

def sha256_file(path: Path)->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def canonical_sha(payload: dict)->str:
    raw=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()

def next_trade_date(after_date, tz):
    for n in range(1,15):
        d=after_date+timedelta(days=n)
        probe=datetime(d.year,d.month,d.day,12,0,tzinfo=tz)
        info=trading_day_info("US",probe)
        if bool(info.get("is_trading_day")):
            return d.isoformat()
    raise RuntimeError("CALENDAR_UNCERTAIN:no_next_trade_day")

def previous_trade_date(before_date, tz):
    for n in range(1,15):
        d=before_date-timedelta(days=n)
        probe=datetime(d.year,d.month,d.day,12,0,tzinfo=tz)
        info=trading_day_info("US",probe)
        if bool(info.get("is_trading_day")):
            return d.isoformat()
    raise RuntimeError("CALENDAR_UNCERTAIN:no_previous_trade_day")

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    tz=ZoneInfo("America/New_York")
    now=datetime.now(tz)
    spec=MARKETS["US"]
    prepared=prepare_live_market("US")
    snapshot=prepared["snapshot"]
    meta=dict(snapshot.metadata or {})
    t0_date=datetime.fromisoformat(prepared["latest_as_of"]).date()
    info=trading_day_info("US",datetime(t0_date.year,t0_date.month,t0_date.day,12,0,tzinfo=tz))
    close_text=str(info.get("early_close_time") or "16:00")
    gate={
        "market_id":"US",
        "runner_version":RUNNER_VERSION,
        "checked_at":now.isoformat(),
        "t0_trade_date":t0_date.isoformat(),
        "session_phase":meta.get("session_phase"),
        "daily_bar_complete":bool(meta.get("daily_bar_complete")),
        "provider":meta.get("source"),
        "data_quality":meta.get("data_quality"),
        "source_latest_ts":meta.get("source_latest_ts"),
        "required_assets":list(spec.assets),
        "observed_assets":list(meta.get("assets") or []),
        "calendar_version":CALENDAR_VERSION,
        "actual_close_time":close_text,
        "exchange_timezone":"America/New_York",
    }
    missing=sorted(set(spec.assets)-set(meta.get("assets") or []))
    if not bool(info.get("is_trading_day")):
        gate["validity_status"]="CALENDAR_UNCERTAIN"
        gate["reason"]="latest DAILY bar maps to non-trading date"
        print(json.dumps(gate,ensure_ascii=False))
        return 20
    if not meta.get("daily_bar_complete") or missing:
        gate["validity_status"]="DATA_INCOMPLETE"
        gate["missing_assets"]=missing
        print(json.dumps(gate,ensure_ascii=False))
        return 21

    states=list(prepared["strategy_states"])
    pop=StrategyPopulationModule()
    group=pop.select(
        "US",states,max_members=10,base_cost_bps=float(spec.base_cost_bps),
        experiment_mode="US_RETURN_MAX_CAPACITY",allow_shadow_simulation=False,
    )
    params=CoreParameters(version="triaid-core-v2@0.2.0")
    core=TriaidCoreModule(params)
    generic=core.decide(snapshot,group,states)
    route_engine=StateAwareUSReturnMaxRoute()
    route=route_engine.decide(
        prepared["panel"],group,generic,states,meta.get("session_phase"),previous_decision=None
    )
    frozen_risk_budget=max(
        0.0,
        min(1.0,1.0-float(route.get("cash_residual_weight") or 0.0)),
    )
    shadow=allocate_shadow(
        "US",states,group.members,
        risk_budget=frozen_risk_budget,
        position_cap=float((group.diagnostics or {}).get("max_strategy_weight_constraint") or 0.28),
        frozen_incumbent=route.get("target_strategy_weights"),
        previous_weights=None,
        modeled_cost_bps=float(spec.base_cost_bps),
        absolute_return_calibrated=False,
    ).to_dict()

    source_receipts=[]
    for symbol in spec.assets:
        source_receipts.append({
            "provider":meta.get("source"),
            "instrument":symbol,
            "source_timestamp":meta.get("source_latest_ts"),
            "receipt_timestamp":now.isoformat(),
            "price_adjustment":"ADJUSTED_CLOSE_WITH_VOLUME_NOTIONAL_RESCALE",
            "freshness":"SAME_SESSION_FINAL_DAILY",
        })
    constitution=ROOT/"TRIAID_CONSTITUTION.md"
    protocol=ROOT/"gpt_async_validation"/"PROTOCOL.md"
    core_file=FINLAB/"triaid_fin"/"core.py"
    route_file=FINLAB/"triaid_fin"/"us_route_guard.py"
    parent_route_file=FINLAB/"triaid_fin"/"us_return_max.py"
    state_break_file=FINLAB/"triaid_fin"/"state_break.py"
    exposure_guard_file=FINLAB/"triaid_fin"/"exposure_control.py"
    payload={
        "protocol_version":PROTOCOL_VERSION,
        "runner_version":RUNNER_VERSION,
        "market_id":"US",
        "t0_trade_date":t0_date.isoformat(),
        "target_t1_trade_date":next_trade_date(t0_date,tz),
        "previous_real_trade_date":previous_trade_date(t0_date,tz),
        "scheduler_trigger_time":os.getenv("SCHEDULER_TRIGGER_TIME") or now.isoformat(),
        "evidence_ready_time":now.isoformat(),
        "freeze_start_time":now.isoformat(),
        "freeze_verified_time":now.isoformat(),
        "exchange_timezone":"America/New_York",
        "exchange_session_type":"REGULAR" if not info.get("early_close") else "EARLY_CLOSE",
        "actual_close_time":close_text,
        "calendar_source":info.get("source") or info.get("official_source"),
        "calendar_version":CALENDAR_VERSION,
        "source_receipts":source_receipts,
        "strategy_registry_version":pop.version,
        "eligible_universe":[s.strategy_id for s in states],
        "excluded_universe":{
            s.strategy_id:"HARD_OR_LIFECYCLE_CONSTRAINT"
            for s in states
            if not (s.eligible and not s.hard_failure and s.lifecycle in {"active","reduced"} and s.liquidity_ok and s.capacity_ok and s.risk_ok and s.concentration_ok)
        },
        "core_version":params.version,
        "core_implementation_version":generic.diagnostics.get("implementation_version"),
        "core_commit_sha":os.getenv("GITHUB_SHA") or "LOCAL_UNPINNED",
        "core_file_sha256":sha256_file(core_file),
        "state_break_file_sha256":sha256_file(state_break_file),
        "exposure_guard_file_sha256":sha256_file(exposure_guard_file),
        "us_route_version":route.get("route_version"),
        "us_route_file_sha256":sha256_file(route_file),
        "us_parent_route_file_sha256":sha256_file(parent_route_file),
        "constitution_version":CONSTITUTION_VERSION,
        "constitution_sha256":sha256_file(constitution),
        "discipline_commit_sha":os.getenv("GITHUB_SHA") or "LOCAL_UNPINNED",
        "protocol_sha256":sha256_file(protocol),
        "evaluation_version":EVALUATION_VERSION,
        "executable_status":"EXECUTED_EXACT_PINNED_CORE_WITH_FAST_BRAKE_AND_EXPOSURE_GUARD",
        "validity_status":"READY_TO_FREEZE",
        "weights_before":dict(generic.weights_before),
        "weights_after":dict(route.get("target_strategy_weights") or {}),
        "no_intervention_weights":dict(group.weights),
        "cash_weights":{"P28_CASH":1.0},
        "market_benchmark":{"P00_BUY_HOLD":1.0},
        "generic_core_control_weights":dict(generic.weights_after),
        "value_frontier_shadow_v2":shadow,
        "value_frontier_shadow_version":SHADOW_VERSION,
        "strategy_states":[s.model_dump(mode="json") for s in states],
        "selection_diagnostics":route.get("candidate_selection_scores"),
        "core_diagnostics":generic.diagnostics,
        "state_break_guard":route.get("state_break_guard"),
        "underlying_exposure_guard":route.get("underlying_exposure_guard"),
        "us_route":route,
        "modeled_turnover":shadow.get("modeled_turnover"),
        "modeled_cost":shadow.get("modeled_execution_cost"),
        "capital_cases":[x for x in ((route.get("capital_capacity") or {}).get("sleeves") or [])],
        "required_capital_cases_usd":list(CAPITAL_CASES),
        "capacity_assumptions":{
            "adv_lookback_days":StateAwareUSReturnMaxRoute.adv_lookback,
            "base_cost_bps":spec.base_cost_bps,
            "impact_coefficient_bps":spec.impact_coefficient_bps,
            "max_participation_adv":spec.max_participation_adv,
        },
        "pre_registered_t1_metrics":[
            "net_return","modeled_pnl_usd","delta_vs_no_intervention",
            "delta_vs_spy","delta_vs_cash","delta_vs_value_frontier_shadow_v2",
            "turnover","modeled_cost","capacity_status","missed_opportunity"
        ],
        "research_only":True,
        "broker_execution_enabled":False,
        "production_mutation":False,
    }
    payload["canonical_payload_sha256"]=canonical_sha(payload)
    out=Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"validity_status":"READY_TO_FREEZE","path":str(out),"t0_trade_date":payload["t0_trade_date"],"target_t1_trade_date":payload["target_t1_trade_date"],"canonical_payload_sha256":payload["canonical_payload_sha256"],"state_break":route.get("state_break_guard"),"exposure_guard":route.get("underlying_exposure_guard")}))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
