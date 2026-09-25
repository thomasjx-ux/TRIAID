from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BilingualText(BaseModel):
    zh: str
    en: str


class StrategyDefinition(BaseModel):
    strategy_id: str
    version: str
    market_support: List[str] = Field(default_factory=lambda: ["US","CN","HK"])
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

    @field_validator("expected_net_return","risk","uncertainty","estimated_cost","oos_marginal_value")
    @classmethod
    def _finite_scalar(cls,value):
        if value is not None and not math.isfinite(float(value)):
            raise ValueError("strategy state numeric fields must be finite")
        return value

    @field_validator("metrics")
    @classmethod
    def _finite_metrics(cls,value):
        if any(not math.isfinite(float(x)) for x in value.values()):
            raise ValueError("strategy metrics must be finite")
        return value

    @field_validator("recent_returns")
    @classmethod
    def _finite_recent_returns(cls,value):
        if any(not math.isfinite(float(x)) for x in value):
            raise ValueError("recent strategy returns must be finite")
        return value


class MarketSnapshot(BaseModel):
    market_id: str
    as_of: str
    snapshot_id: str
    regime: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StrategyPoolSpec(BaseModel):
    pool_id: str
    allowed_strategy_ids: List[str] = Field(default_factory=list)
    denied_strategy_ids: List[str] = Field(default_factory=list)
    market_strategy_ids: Dict[str, List[str]] = Field(default_factory=dict)
    max_group_size: int = Field(default=10, ge=1, le=100)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AccountProfile(BaseModel):
    account_id: str
    strategy_pool_id: str = "GLOBAL"
    base_currency: str = "USD"
    capital: Optional[float] = Field(default=None, gt=0.0, allow_inf_nan=False)
    allowed_markets: List[str] = Field(default_factory=list)
    risk_budget: float = Field(default=1.0, gt=0.0, le=1.0, allow_inf_nan=False)
    max_strategy_weight: Optional[float] = Field(default=None, gt=0.0, le=1.0, allow_inf_nan=False)
    max_drawdown_constraint: Optional[float] = Field(default=None, ge=-1.0, le=0.0, allow_inf_nan=False)
    liquidity_constraint: Dict[str, Any] = Field(default_factory=dict)
    objective: str = "MAXIMIZE_NET_RETURN"
    execution_profile: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DecisionContext(BaseModel):
    market_id: str
    account_id: str = "GLOBAL"
    strategy_pool_id: str = "GLOBAL"
    objective: str = "MAXIMIZE_NET_RETURN"
    capital_state: Dict[str, Any] = Field(default_factory=dict)
    risk_state: Dict[str, Any] = Field(default_factory=dict)
    cross_market_state: Dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    market: MarketSnapshot
    strategy_states: List[StrategyState]
    account: Optional[AccountProfile] = None
    strategy_pool: Optional[StrategyPoolSpec] = None
    decision_context: Optional[DecisionContext] = None
    max_group_size: int = Field(default=10, ge=1, le=100)


class OutcomeRequest(BaseModel):
    realized_returns: Dict[str, float]
    trading_cost: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    as_of: Optional[str] = None

    @field_validator("realized_returns")
    @classmethod
    def _finite_realized_returns(cls,value):
        if any(not math.isfinite(float(x)) for x in value.values()):
            raise ValueError("realized returns must be finite")
        return value


class StrategyGroup(BaseModel):
    group_version: str
    config_version: str
    market_id: str
    selected_at: str = Field(default_factory=utc_now)
    members: List[str]
    weights: Dict[str, float]
    reasons: Dict[str, BilingualText]
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("weights")
    @classmethod
    def _valid_group_weights(cls,value):
        vals=[float(x) for x in value.values()]
        if any(not math.isfinite(x) or x<0 for x in vals):
            raise ValueError("strategy-group weights must be finite and nonnegative")
        if sum(vals)>1.0000001:
            raise ValueError("strategy-group weights cannot exceed 1")
        return value


class TriaidDecision(BaseModel):
    core_version: str
    decided_at: str = Field(default_factory=utc_now)
    weights_before: Dict[str, float]
    weights_after: Dict[str, float]
    reasons: Dict[str, BilingualText]
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("weights_before","weights_after")
    @classmethod
    def _valid_decision_weights(cls,value):
        vals=[float(x) for x in value.values()]
        if any(not math.isfinite(x) or x<0 for x in vals):
            raise ValueError("decision weights must be finite and nonnegative")
        if sum(vals)>1.0000001:
            raise ValueError("decision weights cannot exceed 1")
        return value


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
        "PREVIEW_READY",
        "VERIFIED",
        "NO_NEW_DATA",
        "SUPERSEDED",
        "FAILED",
    ] = "CREATED"
    module_manifest: Dict[str, str]
    market: MarketSnapshot
    account_id: str = "GLOBAL"
    strategy_pool_id: str = "GLOBAL"
    decision_context: Optional[DecisionContext] = None
    strategy_states: List[StrategyState] = Field(default_factory=list)
    strategy_group: Optional[StrategyGroup] = None
    triaid_decision: Optional[TriaidDecision] = None
    evaluation: Optional[EvaluationResult] = None
    audit: Optional[AuditReceipt] = None
    previous_run_id: Optional[str] = None
    diagnostic_summary: Dict[str, Any] = Field(default_factory=dict)
