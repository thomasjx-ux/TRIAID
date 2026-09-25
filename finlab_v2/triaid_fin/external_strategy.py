from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field, field_validator

from .contracts import BilingualText, StrategyDefinition, StrategyState
from .market_registry import normalize_market_id


IsolationState = Literal["QUARANTINE", "SHADOW", "ACTIVE", "FROZEN"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_id(value: str, field: str) -> str:
    key=str(value or "").strip().upper()
    if not key or not re.fullmatch(r"[A-Z0-9_.-]{1,64}", key):
        raise ValueError(f"{field} must match [A-Z0-9_.-] and be <=64 chars")
    return key


def external_strategy_id(provider_id: str, local_strategy_id: str) -> str:
    return f"EXT::{_clean_id(provider_id,'provider_id')}::{_clean_id(local_strategy_id,'local_strategy_id')}"


class ExternalStrategySpec(BaseModel):
    provider_id: str
    local_strategy_id: str
    account_id: str
    strategy_pool_id: str
    market_support: List[str]
    name_zh: str
    name_en: str
    summary_zh: str = "外部交易员策略，经隔离区验证后才可进入TRIAID分配。"
    summary_en: str = "External trader strategy; allocation is allowed only after isolation validation."
    isolation_state: IsolationState = "QUARANTINE"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_id")
    @classmethod
    def _provider_id(cls, value):
        return _clean_id(value,"provider_id")

    @field_validator("local_strategy_id")
    @classmethod
    def _local_strategy_id(cls, value):
        return _clean_id(value,"local_strategy_id")

    @field_validator("account_id","strategy_pool_id")
    @classmethod
    def _binding_id(cls, value):
        value=str(value or "").strip()
        if not value:
            raise ValueError("account_id/strategy_pool_id cannot be empty")
        return value

    @field_validator("market_support")
    @classmethod
    def _markets(cls, value):
        rows=[]
        for market in value:
            key=normalize_market_id(str(market))
            if key not in rows:
                rows.append(key)
        if not rows:
            raise ValueError("market_support cannot be empty")
        return rows

    @property
    def strategy_id(self) -> str:
        return external_strategy_id(self.provider_id,self.local_strategy_id)


class ExternalStrategyObservation(BaseModel):
    provider_id: str
    local_strategy_id: str
    market_id: str
    as_of: str
    expected_net_return: float
    risk: float = 0.0
    uncertainty: float = 0.0
    estimated_cost: float = 0.0
    eligible: bool = True
    liquidity_ok: bool = True
    capacity_ok: bool = True
    risk_ok: bool = True
    concentration_ok: bool = True
    hard_failure: bool = False
    oos_marginal_value: float | None = None
    recent_returns: List[float] = Field(default_factory=list)
    metrics: Dict[str,float] = Field(default_factory=dict)

    @field_validator("provider_id")
    @classmethod
    def _provider_id(cls, value):
        return _clean_id(value,"provider_id")

    @field_validator("local_strategy_id")
    @classmethod
    def _local_strategy_id(cls, value):
        return _clean_id(value,"local_strategy_id")

    @field_validator("market_id")
    @classmethod
    def _market_id(cls, value):
        return normalize_market_id(str(value))


class ExternalStrategyIsolationRequest(BaseModel):
    strategy_id: str
    target_state: IsolationState


class ExternalStrategyModule:
    """Thin adapter boundary for real-trader/external strategy pools.

    New strategies enter QUARANTINE. QUARANTINE and SHADOW observations are
    visible for evaluation but can never receive allocation because the module
    forces a non-active StrategyState lifecycle. Promotion to ACTIVE is explicit
    and still subject to the normal TRIAID hard constraints.
    """

    version="external-strategy@1.0.0"
    registry_file="external_strategy_registry.json"
    feedback_file="external_strategy_feedback.jsonl"

    def __init__(self,store,strategy_population,account_registry)->None:
        self.store=store
        self.strategy_population=strategy_population
        self.account_registry=account_registry
        self._specs:dict[str,ExternalStrategySpec]={}
        self._latest:dict[str,dict]={}
        self._load()

    def _load(self)->None:
        payload=self.store.load_json(self.registry_file,default={}) or {}
        for raw in (payload.get("strategies") or {}).values():
            try:
                spec=ExternalStrategySpec.model_validate(raw)
                self._specs[spec.strategy_id]=spec
                self._register_definition(spec)
            except Exception:
                continue
        self._latest=dict(payload.get("latest_observations") or {})

    def _persist(self)->None:
        self.store.save_json(self.registry_file,{
            "version":self.version,
            "strategies":{sid:spec.model_dump(mode="json") for sid,spec in self._specs.items()},
            "latest_observations":dict(self._latest),
        })

    def _register_definition(self,spec:ExternalStrategySpec)->None:
        self.strategy_population.register(StrategyDefinition(
            strategy_id=spec.strategy_id,
            version="external-adapter@1",
            market_support=list(spec.market_support),
            name=BilingualText(zh=spec.name_zh,en=spec.name_en),
            summary=BilingualText(zh=spec.summary_zh,en=spec.summary_en),
            logic=BilingualText(
                zh="由外部交易员或外部策略提供者输入；TRIAID只消费标准化后的状态，不耦合原始实现。",
                en="Provided by an external trader/strategy provider; TRIAID consumes only normalized state and stays decoupled from the implementation.",
            ),
            best_conditions=BilingualText(
                zh="由策略提供者定义，并由隔离区中的前瞻结果持续验证。",
                en="Defined by the provider and validated prospectively in the isolation zone.",
            ),
            main_risks=BilingualText(
                zh="外部策略可能存在数据、执行、容量和行为漂移风险；未晋级前不得获得资金权重。",
                en="External strategies may carry data, execution, capacity and behavior-drift risks; no capital allocation before promotion.",
            ),
        ))

    def _feedback(self,event:str,spec:ExternalStrategySpec,**extra)->dict:
        payload={
            "at":_utc_now(),
            "event":event,
            "strategy_id":spec.strategy_id,
            "provider_id":spec.provider_id,
            "account_id":spec.account_id,
            "strategy_pool_id":spec.strategy_pool_id,
            "isolation_state":spec.isolation_state,
            "allocation_eligible":spec.isolation_state=="ACTIVE",
            **extra,
        }
        self.store.append_jsonl(self.feedback_file,payload)
        return payload

    def register(self,spec:ExternalStrategySpec)->dict:
        account=self.account_registry.get_account(spec.account_id)
        if account.strategy_pool_id!=spec.strategy_pool_id:
            raise ValueError("external strategy must bind to the account's strategy_pool_id")
        if account.allowed_markets:
            unsupported=[m for m in spec.market_support if m not in account.allowed_markets]
            if unsupported:
                raise ValueError(f"strategy markets outside account scope: {unsupported}")
        sid=spec.strategy_id
        existing=self._specs.get(sid)
        if existing and (
            existing.account_id!=spec.account_id
            or existing.strategy_pool_id!=spec.strategy_pool_id
        ):
            raise ValueError("strategy_id already bound to another account/pool")
        # Registration never changes isolation state. New strategies always
        # start in QUARANTINE; existing strategies preserve their current state.
        if existing is None:
            spec=spec.model_copy(update={"isolation_state":"QUARANTINE"})
        else:
            spec=spec.model_copy(update={"isolation_state":existing.isolation_state})
        self._specs[sid]=spec
        self._register_definition(spec)
        self._persist()
        event=self._feedback("REGISTERED",spec,message="External strategy registered in isolation zone.")
        return {"strategy":self._row(spec),"feedback":event}

    def _row(self,spec:ExternalStrategySpec)->dict:
        latest=self._latest.get(spec.strategy_id)
        return {
            **spec.model_dump(mode="json"),
            "strategy_id":spec.strategy_id,
            "latest_observation":latest,
            "allocation_eligible":spec.isolation_state=="ACTIVE",
            "isolation_rule":"QUARANTINE_OR_SHADOW_NEVER_RECEIVES_CAPITAL",
        }

    def promote(self,strategy_id:str,target_state:IsolationState)->dict:
        try:
            spec=self._specs[strategy_id]
        except KeyError as exc:
            raise KeyError(f"unknown external strategy: {strategy_id}") from exc
        allowed={
            "QUARANTINE":{"SHADOW","FROZEN"},
            "SHADOW":{"ACTIVE","FROZEN","QUARANTINE"},
            "ACTIVE":{"SHADOW","FROZEN"},
            "FROZEN":{"SHADOW","QUARANTINE"},
        }
        if target_state==spec.isolation_state:
            event=self._feedback("ISOLATION_UNCHANGED",spec,message="No isolation-state change.")
            return {"strategy":self._row(spec),"feedback":event}
        if target_state not in allowed[spec.isolation_state]:
            raise ValueError(f"invalid isolation transition: {spec.isolation_state}->{target_state}")
        if target_state=="ACTIVE":
            latest=self._latest.get(strategy_id)
            if not latest:
                raise ValueError("cannot activate without a validated observation")
            state=StrategyState.model_validate(latest["normalized_state"])
            hard_ok=(
                state.eligible
                and not state.hard_failure
                and state.liquidity_ok
                and state.capacity_ok
                and state.risk_ok
                and state.concentration_ok
            )
            if not hard_ok:
                raise ValueError("cannot activate while hard eligibility constraints fail")
        previous=spec.isolation_state
        spec=spec.model_copy(update={"isolation_state":target_state})
        self._specs[strategy_id]=spec
        self._persist()
        event=self._feedback(
            "ISOLATION_CHANGED",spec,
            previous_state=previous,
            message=f"Isolation state changed {previous}->{target_state}.",
        )
        return {"strategy":self._row(spec),"feedback":event}

    def ingest(self,observation:ExternalStrategyObservation)->dict:
        sid=external_strategy_id(observation.provider_id,observation.local_strategy_id)
        try:
            spec=self._specs[sid]
        except KeyError as exc:
            raise KeyError(f"external strategy not registered: {sid}") from exc
        if observation.market_id not in spec.market_support:
            raise ValueError(f"market not supported by external strategy: {observation.market_id}")
        account=self.account_registry.get_account(spec.account_id)

        risk_ok=bool(observation.risk_ok)
        max_dd=observation.metrics.get("max_drawdown")
        if account.max_drawdown_constraint is not None and max_dd is not None:
            risk_ok=risk_ok and float(max_dd)>=float(account.max_drawdown_constraint)

        lifecycle={
            "QUARANTINE":"shadow",
            "SHADOW":"shadow",
            "ACTIVE":"active",
            "FROZEN":"frozen",
        }[spec.isolation_state]
        eligible=bool(observation.eligible) and spec.isolation_state!="QUARANTINE"
        if spec.isolation_state=="FROZEN":
            eligible=False

        state=StrategyState(
            strategy_id=sid,
            eligible=eligible,
            lifecycle=lifecycle,
            expected_net_return=observation.expected_net_return,
            risk=observation.risk,
            uncertainty=observation.uncertainty,
            estimated_cost=observation.estimated_cost,
            liquidity_ok=observation.liquidity_ok,
            capacity_ok=observation.capacity_ok,
            risk_ok=risk_ok,
            concentration_ok=observation.concentration_ok,
            hard_failure=observation.hard_failure,
            oos_marginal_value=observation.oos_marginal_value,
            metrics=dict(observation.metrics),
            recent_returns=list(observation.recent_returns),
        )
        row={
            "received_at":_utc_now(),
            "as_of":observation.as_of,
            "market_id":observation.market_id,
            "normalized_state":state.model_dump(mode="json"),
        }
        self._latest[sid]=row
        self._persist()
        allocation_eligible=(
            spec.isolation_state=="ACTIVE"
            and state.eligible
            and state.lifecycle=="active"
            and not state.hard_failure
            and state.liquidity_ok
            and state.capacity_ok
            and state.risk_ok
            and state.concentration_ok
        )
        event=self._feedback(
            "OBSERVATION_ACCEPTED",spec,
            market_id=observation.market_id,
            as_of=observation.as_of,
            allocation_eligible=allocation_eligible,
            message=(
                "Observation accepted; strategy is eligible for normal TRIAID selection."
                if allocation_eligible
                else "Observation accepted inside isolation; no capital allocation is permitted."
            ),
        )
        return {
            "strategy_id":sid,
            "normalized_state":state.model_dump(mode="json"),
            "isolation_state":spec.isolation_state,
            "allocation_eligible":allocation_eligible,
            "feedback":event,
        }

    def states_for_account(
        self,
        market_id:str,
        account_id:str,
        strategy_pool_id:str,
    )->list[StrategyState]:
        market=normalize_market_id(market_id)
        out=[]
        for sid,spec in self._specs.items():
            if spec.account_id!=account_id or spec.strategy_pool_id!=strategy_pool_id:
                continue
            if market not in spec.market_support:
                continue
            latest=self._latest.get(sid)
            if not latest or latest.get("market_id")!=market:
                continue
            try:
                out.append(StrategyState.model_validate(latest["normalized_state"]))
            except Exception:
                continue
        return out

    def strategy_ids_for_account(
        self,
        market_id:str,
        account_id:str,
        strategy_pool_id:str,
    )->tuple[str,...]:
        market=normalize_market_id(market_id)
        return tuple(sorted(
            sid for sid,spec in self._specs.items()
            if spec.account_id==account_id
            and spec.strategy_pool_id==strategy_pool_id
            and market in spec.market_support
        ))

    def status(self,account_id:str|None=None)->dict:
        rows=[
            self._row(spec)
            for spec in self._specs.values()
            if account_id is None or spec.account_id==account_id
        ]
        return {
            "version":self.version,
            "count":len(rows),
            "strategies":sorted(rows,key=lambda x:x["strategy_id"]),
            "isolation_policy":{
                "default":"QUARANTINE",
                "promotion_path":"QUARANTINE->SHADOW->ACTIVE",
                "quarantine_allocation":False,
                "shadow_allocation":False,
                "active_still_subject_to_hard_constraints":True,
                "fault_isolation":"provider/account/pool scoped",
            },
        }

    def feedback(self,limit:int=100,account_id:str|None=None)->list[dict]:
        rows=self.store.read_jsonl(self.feedback_file,limit=max(1,min(1000,int(limit))))
        if account_id is not None:
            rows=[row for row in rows if row.get("account_id")==account_id]
        return rows[-limit:]
