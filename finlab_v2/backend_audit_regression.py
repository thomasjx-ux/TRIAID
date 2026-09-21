from __future__ import annotations

import math
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

tmp=tempfile.mkdtemp(prefix="triaid-backend-audit-")
os.environ["TRIAID_STORAGE_BACKEND"]="file"
os.environ["TRIAID_DATA_DIR"]=tmp

from triaid_fin.contracts import (
    BilingualText, MarketSnapshot, OutcomeRequest, StrategyGroup, StrategyState, TriaidDecision
)
from triaid_fin.evaluation import EvaluationModule
from triaid_fin.evolution import EvolutionModule
from triaid_fin.frequency_policy import FrequencyPolicy
from triaid_fin.market_lab import MarketPanel, MarketSpec
from triaid_fin.observation import MarketObservationStore
from triaid_fin.population_state import PopulationStateTracker
from triaid_fin.store import RunStore
from triaid_fin.strategy_evolution import StrategyEvolutionModule
from triaid_fin.strategy_population import StrategyPopulationModule
from triaid_fin.us_return_max import USReturnMaxRoute


def expect_failure(fn):
    try:
        fn()
    except Exception:
        return
    raise AssertionError("expected failure")

# Numeric boundaries fail closed.
expect_failure(lambda:StrategyState(strategy_id="X",expected_net_return=float("nan")))
expect_failure(lambda:OutcomeRequest(realized_returns={"X":0.01},trading_cost=-0.01))
expect_failure(lambda:OutcomeRequest(realized_returns={"X":float("inf")},trading_cost=0.0))

# Missing posterior returns may not silently become zero.
group=StrategyGroup(
    group_version="audit",config_version="audit",market_id="US",
    members=["A","B"],weights={"A":0.5,"B":0.5},
    reasons={"A":BilingualText(zh="a",en="a"),"B":BilingualText(zh="b",en="b")},
)
decision=TriaidDecision(
    core_version="audit",weights_before={"A":0.5,"B":0.5},
    weights_after={"A":0.6,"B":0.4},reasons={}
)
expect_failure(lambda:EvaluationModule().evaluate(group,decision,{"A":0.01},0.0))

store=RunStore(root=tmp)

# Frequency adaptation holds when value evidence is absent.
freq=FrequencyPolicy(store)
missing=freq.record_evidence(
    "US","REALTIME",evaluated_samples=50,
    incremental_net_return=None,incremental_information_gain=None,confidence=0.99,
)
assert missing["evaluation_action"]=="HOLD_MISSING_VALUE_EVIDENCE"
assert missing["interval_seconds"]==60

# Provisional bars cannot advance lifecycle/shadow evidence.
population=StrategyPopulationModule()
tracker=PopulationStateTracker(store,population)
shadow=StrategyState(
    strategy_id="C29_SIZE_REL20",lifecycle="shadow",
    expected_net_return=0.10,risk=0.1,uncertainty=0.01,
    metrics={"latest_return":0.02},
)
p0=tracker.apply("CN",[shadow],observation_key="DAILY:2026-09-21",advance_observation=False)[0]
assert p0.metrics["shadow_live_days"]==0.0
p1=tracker.apply("CN",[shadow],observation_key="DAILY:2026-09-21",advance_observation=True)[0]
assert p1.metrics["shadow_live_days"]==1.0

# External self-attestation can no longer promote Core or strategy-rule candidates.
evo=EvolutionModule(store)
parent=evo.active()
fake=deepcopy(parent.__dict__)
fake.update({"version":"triaid-core-v2-candidate-audit","status":"candidate","parent_version":parent.version})
evo.state["cores"][fake["version"]]=fake
evo.store.save_json("core_evolution.json",evo.state)
blocked=evo.promote(fake["version"],{"replay_pass":True,"holdout_pass":True,"shadow_pass":True,"audit_pass":True})
assert blocked["promoted"] is False
assert blocked["reason"]=="INTERNAL_VALIDATION_REQUIRED"

sevo=StrategyEvolutionModule(store)
m=sevo._market("US")
active=sevo.active("US")
raw=deepcopy(active.__dict__)
raw.update({"version":"strategy-rules-us-candidate-audit","status":"candidate","parent_version":active.version})
m["profiles"][raw["version"]]=raw
sevo._save()
blocked_rules=sevo.promote("US",raw["version"],{"replay_pass":True,"holdout_pass":True,"shadow_pass":True,"audit_pass":True})
assert blocked_rules["promoted"] is False
assert blocked_rules["reason"]=="INTERNAL_VALIDATION_REQUIRED"

# Corrupt persisted JSON is surfaced, never replaced by an empty default.
Path(tmp,"corrupt.json").write_text("{not-json",encoding="utf-8")
expect_failure(lambda:store.load_json("corrupt.json",default={}))

# DAILY freshness compares local trading date, avoiding provider timestamp-convention regressions.
d1=int(datetime(2026,9,21,12,0,tzinfo=ZoneInfo("America/New_York")).timestamp())
d2=int(datetime(2026,9,21,16,0,tzinfo=ZoneInfo("America/New_York")).timestamp())
assert MarketObservationStore._freshness_value("US","DAILY",d1)==MarketObservationStore._freshness_value("US","DAILY",d2)

# US Return-Max excludes inadmissible higher-scoring states.
route=USReturnMaxRoute()
winner,tied=route._strict_max_strategy([
    StrategyState(strategy_id="BAD",lifecycle="active",expected_net_return=0.90,hard_failure=True),
    StrategyState(strategy_id="GOOD",lifecycle="active",expected_net_return=0.20),
])
assert winner.strategy_id=="GOOD"
assert tied==["GOOD"]

print("TRIAID_BACKEND_AUDIT_REGRESSION_PASS")
