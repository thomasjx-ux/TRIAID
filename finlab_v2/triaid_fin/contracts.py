from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BilingualText(BaseModel):
    zh: str
    en: str


class StrategyDefinition(BaseModel):
    strategy_id: str
    version: str
    market_support: List[str] = Field(default_factory=lambda: ["US","CN"])
    name: BilingualText
    summary: BilingualText
    logic: BilingualText
    best_conditions: BilingualText
    main_risks: BilingualText


class StrategyState(BaseModel):
    strategy_id: str
    eligible: bool = True
    lifecycle: Literal["research", "candidate", "shadow", "active", "reduced", "frozen", "retired"] = "candidate"
    expected_net_return: float
    risk: float = 0.0
    uncertainty: float = 0.0
    estimated_cost: float = 0.0
    liquidity_ok: bool = True
    capacity_ok: bool = True
    risk_ok: bool = True
    concentration_ok: bool = True
    hard_failure: bool = False
    oos_marginal_value: Optional[float] = None
    shadow_evidence_pass: bool = False
    new_evidence_pass: bool = False
    evidence_days: int = 0
    horizon_multiples: float = 0.0
    independent_decisions: int = 0
    metrics: Dict[str, float] = Field(default_factory=dict)
    recent_returns: List[float] = Field(default_factory=list)
    selection_reason: Optional[BilingualText] = None


class MarketSnapshot(BaseModel):
    market_id: str
    as_of: str
    snapshot_id: str
    regime: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    market: MarketSnapshot
    strategy_states: List[StrategyState]
    max_group_size: int = Field(default=10, ge=1, le=100)


class OutcomeRequest(BaseModel):
    realized_returns: Dict[str, float]
    trading_cost: float = 0.0


class StrategyGroup(BaseModel):
    group_version: str
    config_version: str
    market_id: str
    selected_at: str = Field(default_factory=utc_now)
    members: List[str]
    weights: Dict[str, float]
    reasons: Dict[str, BilingualText]
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


class TriaidDecision(BaseModel):
    core_version: str
    decided_at: str = Field(default_factory=utc_now)
    weights_before: Dict[str, float]
    weights_after: Dict[str, float]
    reasons: Dict[str, BilingualText]
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    status: Literal["PENDING_OUTCOME", "EVALUATED"]
    baseline_return: Optional[float] = None
    triaid_return: Optional[float] = None
    excess_return: Optional[float] = None
    trading_cost: float = 0.0
    strategy_realized_returns: Dict[str, float] = Field(default_factory=dict)
    baseline_contributions: Dict[str, float] = Field(default_factory=dict)
    triaid_contributions: Dict[str, float] = Field(default_factory=dict)


class AuditReceipt(BaseModel):
    passed: bool
    checks: Dict[str, bool]
    notes: List[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    run_id: str
    created_at: str = Field(default_factory=utc_now)
    status: Literal[
        "CREATED",
        "FETCHING_DATA",
        "DECISION_READY_AWAITING_OUTCOME",
        "VERIFIED",
        "NO_NEW_DATA",
        "FAILED",
    ] = "CREATED"
    module_manifest: Dict[str, str]
    market: MarketSnapshot
    strategy_states: List[StrategyState] = Field(default_factory=list)
    strategy_group: Optional[StrategyGroup] = None
    triaid_decision: Optional[TriaidDecision] = None
    evaluation: Optional[EvaluationResult] = None
    audit: Optional[AuditReceipt] = None
    previous_run_id: Optional[str] = None
    diagnostic_summary: Dict[str, Any] = Field(default_factory=dict)
