from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .contracts import AccountProfile, MarketSnapshot, StrategyGroup, StrategyPoolSpec, StrategyState
from .external_strategy import ExternalStrategyObservation, ExternalStrategySpec, external_strategy_id
from .market_registry import MARKET_REGISTRY, normalize_market_id
from .strategy_interfaces import StrategyInterfaceCatalog
from .strategy_registry import FAMILIES, strategy_ids_for_market


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_trader_id(value: str) -> str:
    key=str(value or "").strip().upper()
    if not key or not re.fullmatch(r"[A-Z0-9_.-]{1,48}",key):
        raise ValueError("trader_id must match [A-Z0-9_.-] and be <=48 chars")
    return key


class TraderDecisionResult(BaseModel):
    strategy_ids: List[str]
    weights: Dict[str,float] = Field(default_factory=dict)

    @field_validator("strategy_ids")
    @classmethod
    def _strategy_ids(cls,value):
        rows=[]
        for item in value:
            sid=str(item or "").strip().upper()
            if sid and sid not in rows:
                rows.append(sid)
        if not rows:
            raise ValueError("decision_result.strategy_ids cannot be empty")
        return rows

    @field_validator("weights")
    @classmethod
    def _weights(cls,value):
        out={}
        for key,raw in dict(value or {}).items():
            val=float(raw)
            if not math.isfinite(val) or val<0:
                raise ValueError("decision_result.weights must be finite and nonnegative")
            out[str(key).strip().upper()]=val
        return out


class TraderShadowDecision(BaseModel):
    trader_id: str
    market_id: str
    decision_process: str
    decision_result: TraderDecisionResult
    capital: float | None = Field(default=None,gt=0.0,allow_inf_nan=False)

    @field_validator("trader_id")
    @classmethod
    def _trader_id(cls,value):
        return _clean_trader_id(value)

    @field_validator("market_id")
    @classmethod
    def _market_id(cls,value):
        return normalize_market_id(str(value))

    @field_validator("decision_process")
    @classmethod
    def _decision_process(cls,value):
        text=str(value or "").strip()
        if not text:
            raise ValueError("decision_process cannot be empty")
        return text


class TraderCustomStrategyRegistration(BaseModel):
    trader_id: str
    local_strategy_id: str
    market_support: List[str]
    name: str
    name_en: str | None = None
    summary: str | None = None

    @field_validator("trader_id")
    @classmethod
    def _trader_id(cls,value):
        return _clean_trader_id(value)

    @field_validator("local_strategy_id")
    @classmethod
    def _local_strategy_id(cls,value):
        key=str(value or "").strip().upper()
        if not key or not re.fullmatch(r"[A-Z0-9_.-]{1,64}",key):
            raise ValueError("local_strategy_id must match [A-Z0-9_.-] and be <=64 chars")
        return key

    @field_validator("market_support")
    @classmethod
    def _markets(cls,value):
        rows=[]
        for raw in value:
            market=normalize_market_id(str(raw))
            if market not in rows:
                rows.append(market)
        if not rows:
            raise ValueError("market_support cannot be empty")
        return rows

    @field_validator("name")
    @classmethod
    def _name(cls,value):
        text=str(value or "").strip()
        if not text:
            raise ValueError("name cannot be empty")
        return text


class TraderCustomStrategyObservation(BaseModel):
    trader_id: str
    local_strategy_id: str
    market_id: str
    as_of: str
    expected_net_return: float
    risk: float = 0.0
    uncertainty: float = 0.0
    estimated_cost: float = 0.0
    recent_returns: List[float] = Field(default_factory=list)
    max_drawdown: float | None = None
    liquidity_ok: bool = True
    capacity_ok: bool = True
    risk_ok: bool = True
    concentration_ok: bool = True
    hard_failure: bool = False

    @field_validator("trader_id")
    @classmethod
    def _trader_id(cls,value):
        return _clean_trader_id(value)

    @field_validator("local_strategy_id")
    @classmethod
    def _local_strategy_id(cls,value):
        key=str(value or "").strip().upper()
        if not key or not re.fullmatch(r"[A-Z0-9_.-]{1,64}",key):
            raise ValueError("local_strategy_id must match [A-Z0-9_.-] and be <=64 chars")
        return key

    @field_validator("market_id")
    @classmethod
    def _market_id(cls,value):
        return normalize_market_id(str(value))


class TraderShadowOutcome(BaseModel):
    decision_id: str
    realized_returns: Dict[str,float]
    outcome_as_of: str

    @field_validator("realized_returns")
    @classmethod
    def _finite_returns(cls,value):
        out={}
        for key,raw in dict(value or {}).items():
            val=float(raw)
            if not math.isfinite(val):
                raise ValueError("realized_returns must be finite")
            out[str(key).strip().upper()]=val
        return out


FAMILY_LABELS = {
    "market_beta":("市场基准","Market Beta"),
    "volatility_control":("波动控制","Volatility Control"),
    "drawdown_control":("回撤控制","Drawdown Control"),
    "time_series_momentum":("趋势与动量","Trend & Momentum"),
    "risk_control":("风险控制","Risk Control"),
    "short_horizon_reversal":("短周期反转","Short-Horizon Reversal"),
    "cross_asset_momentum":("跨资产动量","Cross-Asset Momentum"),
    "defensive_rotation":("防御轮动","Defensive Rotation"),
    "cross_asset_trend":("跨资产趋势","Cross-Asset Trend"),
    "strategic_allocation":("战略配置","Strategic Allocation"),
    "risk_balanced_allocation":("风险平衡配置","Risk-Balanced Allocation"),
    "breadth_rotation":("市场宽度轮动","Breadth Rotation"),
    "cash":("现金","Cash"),
    "market_extension":("市场扩展策略","Market Extension"),
    "external":("外部策略","External Strategy"),
    "trader_custom":("交易员自定义策略","Trader Custom Strategy"),
}


class TraderShadowModule:
    """Minimal shadow-trader workflow.

    One trader submission creates three comparable shadow routes:
    TRADER = trader's original strategy group/weights;
    ASSISTED = same trader-selected group, reweighted by TRIAID Core;
    AUTO = TRIAID selects and weights from the full admissible account universe.

    The module never places broker orders and never writes global evidence ledgers.
    """

    version="trader-shadow@1.1.0"
    registry_file="trader_shadow_decisions.json"
    event_file="trader_shadow_events.jsonl"

    def __init__(
        self,
        store,
        account_registry,
        strategy_population,
        external_strategies,
        core_provider,
    )->None:
        self.store=store
        self.account_registry=account_registry
        self.strategy_population=strategy_population
        self.external_strategies=external_strategies
        self.strategy_interfaces=StrategyInterfaceCatalog(
            self.strategy_population,
            self.external_strategies,
        )
        self.core_provider=core_provider
        payload=self.store.load_json(self.registry_file,default={}) or {}
        self._decisions=dict(payload.get("decisions") or {})

    @staticmethod
    def account_id(trader_id:str)->str:
        return f"TRADER_SHADOW_{_clean_trader_id(trader_id)}"

    @staticmethod
    def pool_id(trader_id:str)->str:
        return f"TRADER_SHADOW_POOL_{_clean_trader_id(trader_id)}"

    @staticmethod
    def custom_provider_id(trader_id:str)->str:
        return f"TRADER_{_clean_trader_id(trader_id)}"

    def _persist(self)->None:
        self.store.save_json(self.registry_file,{
            "version":self.version,
            "decisions":dict(self._decisions),
        })

    def _event(self,event:str,**payload)->dict:
        row={"at":_utc_now(),"event":event,**payload}
        self.store.append_jsonl(self.event_file,row)
        return row

    def ensure_trader(self,trader_id:str)->tuple[AccountProfile,StrategyPoolSpec]:
        trader=_clean_trader_id(trader_id)
        account_id=self.account_id(trader)
        pool_id=self.pool_id(trader)
        try:
            pool=self.account_registry.get_pool(pool_id)
        except KeyError:
            pool=self.account_registry.register_pool(
                StrategyPoolSpec(
                    pool_id=pool_id,
                    max_group_size=10,
                    metadata={
                        "shadow_trading":True,
                        "selection_mode":"FULL_MARKET_AUTO_DEFAULT",
                        "pool_policy":"FULL_ADMISSIBLE_UNIVERSE_WITH_OPTIONAL_TRADER_GROUP",
                    },
                )
            )
            self.account_registry.persist()
        try:
            account=self.account_registry.get_account(account_id)
        except KeyError:
            account=self.account_registry.register_account(
                AccountProfile(
                    account_id=account_id,
                    strategy_pool_id=pool_id,
                    allowed_markets=[],
                    objective="MAXIMIZE_NET_RETURN",
                    metadata={
                        "shadow_trading":True,
                        "trader_id":trader,
                        "broker_execution_enabled":False,
                        "minimal_input_contract":"DECISION_PROCESS_PLUS_DECISION_RESULT_OPTIONAL_CAPITAL",
                    },
                )
            )
            self.account_registry.persist()
        if account.strategy_pool_id!=pool.pool_id:
            raise ValueError("shadow trader account/pool binding mismatch")
        return account,pool

    def _available_ids(self,market_id:str,account:AccountProfile,pool:StrategyPoolSpec)->tuple[str,...]:
        candidates=strategy_ids_for_market(market_id)
        internal=self.account_registry.resolve_strategy_ids(
            market_id,
            candidates,
            account_id=account.account_id,
            pool_id=pool.pool_id,
        )
        external=self.external_strategies.strategy_ids_for_account(
            market_id,
            account.account_id,
            pool.pool_id,
        )
        return tuple(dict.fromkeys((*internal,*external)))

    def register_custom_strategy(self,request:TraderCustomStrategyRegistration)->dict:
        account,pool=self.ensure_trader(request.trader_id)
        spec=ExternalStrategySpec(
            provider_id=self.custom_provider_id(request.trader_id),
            local_strategy_id=request.local_strategy_id,
            account_id=account.account_id,
            strategy_pool_id=pool.pool_id,
            market_support=list(request.market_support),
            name_zh=request.name,
            name_en=request.name_en or request.name,
            summary_zh=request.summary or "交易员自定义策略；进入影子模型前需要至少一次标准化状态观测。",
            summary_en=request.summary or "Trader-defined strategy; at least one standardized state observation is required before shadow-model use.",
        )
        result=self.external_strategies.register(spec)
        self._event(
            "CUSTOM_STRATEGY_REGISTERED",
            trader_id=_clean_trader_id(request.trader_id),
            strategy_id=result["strategy"]["strategy_id"],
            market_support=list(request.market_support),
        )
        return result

    def observe_custom_strategy(self,request:TraderCustomStrategyObservation)->dict:
        account,pool=self.ensure_trader(request.trader_id)
        provider_id=self.custom_provider_id(request.trader_id)
        sid=external_strategy_id(provider_id,request.local_strategy_id)
        rows={
            row.get("strategy_id"):row
            for row in (self.external_strategies.status(account.account_id).get("strategies") or [])
        }
        spec=rows.get(sid)
        if spec is None:
            raise KeyError(f"custom strategy is not registered: {sid}")
        if str(spec.get("strategy_pool_id"))!=pool.pool_id:
            raise ValueError("custom strategy is bound to another strategy pool")
        if str(spec.get("isolation_state"))=="QUARANTINE":
            self.external_strategies.promote(sid,"SHADOW")
        metrics={}
        if request.max_drawdown is not None:
            metrics["max_drawdown"]=float(request.max_drawdown)
        result=self.external_strategies.ingest(ExternalStrategyObservation(
            provider_id=provider_id,
            local_strategy_id=request.local_strategy_id,
            market_id=request.market_id,
            as_of=request.as_of,
            expected_net_return=request.expected_net_return,
            risk=request.risk,
            uncertainty=request.uncertainty,
            estimated_cost=request.estimated_cost,
            recent_returns=list(request.recent_returns),
            liquidity_ok=request.liquidity_ok,
            capacity_ok=request.capacity_ok,
            risk_ok=request.risk_ok,
            concentration_ok=request.concentration_ok,
            hard_failure=request.hard_failure,
            metrics=metrics,
        ))
        self._event(
            "CUSTOM_STRATEGY_OBSERVED",
            trader_id=_clean_trader_id(request.trader_id),
            strategy_id=sid,
            market_id=request.market_id,
            as_of=request.as_of,
            shadow_simulation_eligible=bool(
                result.get("isolation_state")=="SHADOW"
                and (result.get("normalized_state") or {}).get("eligible")
            ),
        )
        return {
            **result,
            "shadow_simulation_eligible":bool(
                result.get("isolation_state") in {"SHADOW","ACTIVE"}
                and (result.get("normalized_state") or {}).get("eligible")
            ),
            "global_allocation_eligible":bool(result.get("allocation_eligible")),
            "automation":"FIRST_OBSERVATION_AUTO_PROMOTES_QUARANTINE_TO_SHADOW_ONLY",
        }

    def catalog(self,trader_id:str,market_id:str)->dict:
        market=normalize_market_id(market_id)
        account,pool=self.ensure_trader(trader_id)
        ids=self._available_ids(market,account,pool)
        interface_catalog=self.strategy_interfaces.catalog(
            market,
            account.account_id,
            ids,
        )
        families:dict[str,list[dict]]={}
        for row in interface_catalog["strategies"]:
            family=row.get("family") or "external"
            labels=FAMILY_LABELS.get(family,(family,family))
            item={
                **row,
                "family_zh":labels[0],
                "family_en":labels[1],
            }
            families.setdefault(family,[]).append(item)
        family_rows=[]
        for family,strategies in sorted(families.items()):
            labels=FAMILY_LABELS.get(family,(family,family))
            family_rows.append({
                "family":family,
                "name_zh":labels[0],
                "name_en":labels[1],
                "count":len(strategies),
                "strategies":strategies,
            })
        return {
            "version":self.version,
            "strategy_interface_catalog_version":self.strategy_interfaces.version,
            "trader_id":_clean_trader_id(trader_id),
            "account_id":account.account_id,
            "strategy_pool_id":pool.pool_id,
            "market_id":market,
            "currency":MARKET_REGISTRY.get(market).currency,
            "strategy_count":interface_catalog["strategy_count"],
            "source_counts":interface_catalog["source_counts"],
            "interfaces":interface_catalog["interfaces"],
            "catalog_policy":interface_catalog["policy"],
            "families":family_rows,
            "interaction_policy":{
                "default":"FULL_POOL_AUTO",
                "manual_requirement":"Choose any currently selectable strategy; strategies outside the built-in pool can be added through the trader custom adapter.",
                "weights_optional":True,
                "weights_default":"EQUAL_WEIGHT",
                "capital_optional":True,
                "extra_pool_configuration_required":False,
                "custom_strategy_registration_fields":["local_strategy_id","name","market_support"],
                "first_observation_auto_enters_shadow":True,
                "shadow_strategy_can_join_shadow_simulation":True,
                "active_promotion_required_for_global_allocation":True,
            },
        }

    @staticmethod
    def _normalize_manual_weights(strategy_ids:list[str],provided:dict[str,float])->tuple[dict[str,float],dict]:
        ids=list(dict.fromkeys(strategy_ids))
        supplied={sid:max(0.0,float(provided.get(sid,0.0))) for sid in ids if sid in provided}
        supplied_total=sum(supplied.values())
        normalized=False
        if supplied_total>1.0+1e-12:
            supplied={sid:value/supplied_total for sid,value in supplied.items()}
            supplied_total=1.0
            normalized=True
        missing=[sid for sid in ids if sid not in supplied]
        if not supplied:
            equal=1.0/len(ids)
            weights={sid:equal for sid in ids}
            mode="EQUAL_WEIGHT_DEFAULT"
        elif missing and supplied_total<1.0-1e-12:
            residual=(1.0-supplied_total)/len(missing)
            weights={**supplied,**{sid:residual for sid in missing}}
            mode="USER_WEIGHTS_PLUS_AUTO_FILL"
        else:
            weights={sid:value for sid,value in supplied.items() if value>1e-12}
            mode="USER_WEIGHTS"
        total=sum(weights.values())
        implicit_cash=max(0.0,1.0-total)
        return weights,{
            "mode":mode,
            "input_weights_normalized":normalized,
            "risky_weight":sum(v for sid,v in weights.items() if sid!="P28_CASH"),
            "implicit_cash_weight":implicit_cash if "P28_CASH" not in weights else 0.0,
        }

    @staticmethod
    def _projected_proxy(weights:dict[str,float],state_map:dict[str,StrategyState])->float:
        return sum(
            float(weight)*(
                0.0 if sid=="P28_CASH"
                else float(state_map[sid].expected_net_return)-max(0.0,float(state_map[sid].estimated_cost))
            )
            for sid,weight in weights.items()
            if sid=="P28_CASH" or sid in state_map
        )

    @staticmethod
    def _route_payload(
        label:str,
        weights:dict[str,float],
        state_map:dict[str,StrategyState],
        capital:float|None,
        currency:str,
        extra:dict|None=None,
    )->dict:
        risky_weight=sum(float(v) for sid,v in weights.items() if sid!="P28_CASH")
        explicit_cash=float(weights.get("P28_CASH",0.0))
        implicit_cash=max(0.0,1.0-sum(float(v) for v in weights.values()))
        notional_by_strategy=(
            {sid:float(capital)*float(weight) for sid,weight in weights.items()}
            if capital is not None
            else None
        )
        return {
            "route":label,
            "weights":{sid:float(v) for sid,v in weights.items()},
            "strategy_ids":[sid for sid,v in weights.items() if v>1e-12],
            "risky_weight":risky_weight,
            "cash_weight":explicit_cash+implicit_cash,
            "projected_net_return_proxy":TraderShadowModule._projected_proxy(weights,state_map),
            "projection_semantics":"CURRENT_STATE_NET_RETURN_PROXY_NOT_A_CALIBRATED_FORECAST",
            "capital":capital,
            "currency":currency,
            "invested_notional":float(capital)*risky_weight if capital is not None else None,
            "cash_notional":float(capital)*(explicit_cash+implicit_cash) if capital is not None else None,
            "notional_by_strategy":notional_by_strategy,
            **dict(extra or {}),
        }

    def submit(
        self,
        submission:TraderShadowDecision,
        market:MarketSnapshot,
        states:list[StrategyState],
        account:AccountProfile,
        pool:StrategyPoolSpec,
    )->dict:
        trader=_clean_trader_id(submission.trader_id)
        market_id=normalize_market_id(submission.market_id)
        available=set(self._available_ids(market_id,account,pool))
        state_map={state.strategy_id:state for state in states}
        selected=list(submission.decision_result.strategy_ids)
        unknown=[sid for sid in selected if sid not in available or sid not in state_map]
        if unknown:
            raise ValueError(f"decision_result contains unavailable strategies: {unknown}")

        manual_weights,manual_meta=self._normalize_manual_weights(
            selected,
            submission.decision_result.weights,
        )
        position_cap=(
            float(account.max_strategy_weight)
            if account.max_strategy_weight is not None
            else float(self.strategy_population.config_for(market_id).max_weight)
        )
        manual_group=StrategyGroup(
            group_version="trader-shadow-manual@1",
            config_version="TRADER_SUBMITTED",
            market_id=market_id,
            members=list(manual_weights),
            weights=dict(manual_weights),
            reasons={},
            diagnostics={
                "max_strategy_weight_constraint":position_cap,
                "route":"TRADER",
                **manual_meta,
            },
        )

        market_copy=market.model_copy(deep=True)
        market_copy.metadata=dict(market_copy.metadata or {})
        market_copy.metadata.update({
            "research_only":True,
            "broker_execution_enabled":False,
            "run_scope":"TRADER_SHADOW_COMPARISON",
            "evidence_eligible":False,
            "account_id":account.account_id,
            "strategy_pool_id":pool.pool_id,
            "account_risk_budget":account.risk_budget,
            "account_max_strategy_weight":account.max_strategy_weight,
        })
        core=self.core_provider()
        assisted_decision=core.decide(
            market_copy,
            manual_group,
            [state_map[sid] for sid in selected],
        )

        full_states=[
            state for state in states
            if state.strategy_id in available
        ]
        auto_group=self.strategy_population.select(
            market_id,
            full_states,
            pool.max_group_size,
            previous_group=None,
            base_cost_bps=float(MARKET_REGISTRY.get(market_id).base_cost_bps),
            experiment_mode="TRADER_SHADOW_AUTO",
            max_weight_override=account.max_strategy_weight,
            allow_shadow_simulation=True,
        )
        auto_decision=core.decide(market_copy,auto_group,full_states)

        currency=MARKET_REGISTRY.get(market_id).currency
        capital=float(submission.capital) if submission.capital is not None else None
        routes={
            "trader":self._route_payload(
                "TRADER",
                manual_weights,
                state_map,
                capital,
                currency,
                {"weight_input":manual_meta},
            ),
            "assisted":self._route_payload(
                "TRIAID_ASSISTED",
                dict(assisted_decision.weights_after),
                state_map,
                capital,
                currency,
                {
                    "same_trader_selected_universe":True,
                    "shadow_simulation_enabled":True,
                    "shadow_strategy_ids":[
                        sid for sid in selected
                        if state_map[sid].lifecycle=="shadow" and state_map[sid].eligible
                    ],
                    "core_version":assisted_decision.core_version,
                    "core_diagnostics":assisted_decision.diagnostics,
                },
            ),
            "auto":self._route_payload(
                "TRIAID_AUTO",
                dict(auto_decision.weights_after),
                state_map,
                capital,
                currency,
                {
                    "full_pool_auto_selection":True,
                    "shadow_simulation_enabled":True,
                    "selected_group_before_core":list(auto_group.members),
                    "core_version":auto_decision.core_version,
                    "core_diagnostics":auto_decision.diagnostics,
                },
            ),
        }

        decision_id=f"TS-{market_id}-{uuid4().hex[:16]}"
        row={
            "decision_id":decision_id,
            "created_at":_utc_now(),
            "status":"PENDING_OUTCOME",
            "trader_id":trader,
            "account_id":account.account_id,
            "strategy_pool_id":pool.pool_id,
            "market_id":market_id,
            "currency":currency,
            "market_as_of":market.as_of,
            "snapshot_id":market.snapshot_id,
            "decision_process":submission.decision_process,
            "decision_result":{
                "strategy_ids":selected,
                "weights":manual_weights,
                "weight_input":manual_meta,
            },
            "capital":capital,
            "routes":routes,
            "outcome":None,
            "partial_realized_returns":{},
            "automation":{
                "auto_account_created":True,
                "full_strategy_pool_default":True,
                "assisted_route_generated":True,
                "auto_route_generated":True,
                "broker_execution_enabled":False,
            },
        }
        self._decisions[decision_id]=row
        self._persist()
        self._event(
            "DECISION_ACCEPTED",
            decision_id=decision_id,
            trader_id=trader,
            market_id=market_id,
            market_as_of=market.as_of,
            capital=capital,
            route_count=3,
        )
        return row

    @staticmethod
    def _route_outcome(
        route:dict,
        realized_returns:dict[str,float],
        capital:float|None,
        currency:str,
        base_cost_bps:float,
    )->dict:
        weights={str(k):float(v) for k,v in (route.get("weights") or {}).items()}
        missing=[
            sid for sid,weight in weights.items()
            if sid!="P28_CASH" and weight>1e-12 and sid not in realized_returns
        ]
        covered_weight=sum(
            weight for sid,weight in weights.items()
            if sid=="P28_CASH" or sid in realized_returns
        )
        gross=sum(
            weight*(0.0 if sid=="P28_CASH" else float(realized_returns[sid]))
            for sid,weight in weights.items()
            if sid=="P28_CASH" or sid in realized_returns
        )
        risky_weight=sum(weight for sid,weight in weights.items() if sid!="P28_CASH")
        modeled_cost=risky_weight*float(base_cost_bps)/10000.0
        net=gross-modeled_cost
        return {
            "status":"COMPLETE" if not missing else "PARTIAL",
            "missing_strategy_ids":missing,
            "covered_weight":covered_weight,
            "gross_return":gross,
            "modeled_base_cost":modeled_cost,
            "net_return":net,
            "capital":capital,
            "currency":currency,
            "net_pnl":float(capital)*net if capital is not None else None,
            "ending_value":float(capital)*(1.0+net) if capital is not None else None,
            "result_semantics":"SHADOW_REALIZED_RETURN_USING_MARKET_STRATEGY_OUTCOMES_AND_MODELED_BASE_COST",
        }

    def _resolve_row(
        self,
        row:dict,
        realized_returns:dict[str,float],
        outcome_as_of:str,
    )->dict:
        merged=dict(row.get("partial_realized_returns") or {})
        merged.update({str(k).upper():float(v) for k,v in realized_returns.items()})
        market_id=str(row["market_id"])
        capital=row.get("capital")
        currency=str(row.get("currency") or MARKET_REGISTRY.get(market_id).currency)
        base_cost_bps=float(MARKET_REGISTRY.get(market_id).base_cost_bps)
        results={
            name:self._route_outcome(route,merged,capital,currency,base_cost_bps)
            for name,route in (row.get("routes") or {}).items()
        }
        complete=all(result.get("status")=="COMPLETE" for result in results.values())
        row["partial_realized_returns"]=merged
        row["status"]="RESOLVED" if complete else "PARTIAL_OUTCOME"
        row["outcome"]={
            "outcome_as_of":outcome_as_of,
            "results":results,
            "comparison":self._comparison(results,capital,currency),
            "all_routes_complete":complete,
        }
        return row

    @staticmethod
    def _comparison(results:dict,capital:float|None,currency:str)->dict:
        def metric(route,key):
            return (results.get(route) or {}).get(key)
        manual=metric("trader","net_return")
        assisted=metric("assisted","net_return")
        auto=metric("auto","net_return")
        return {
            "currency":currency,
            "capital":capital,
            "trader_net_return":manual,
            "assisted_net_return":assisted,
            "auto_net_return":auto,
            "assisted_minus_trader_return":(
                assisted-manual if assisted is not None and manual is not None else None
            ),
            "auto_minus_trader_return":(
                auto-manual if auto is not None and manual is not None else None
            ),
            "auto_minus_assisted_return":(
                auto-assisted if auto is not None and assisted is not None else None
            ),
            "trader_net_pnl":metric("trader","net_pnl"),
            "assisted_net_pnl":metric("assisted","net_pnl"),
            "auto_net_pnl":metric("auto","net_pnl"),
            "assisted_minus_trader_pnl":(
                float(capital)*(assisted-manual)
                if capital is not None and assisted is not None and manual is not None else None
            ),
            "auto_minus_trader_pnl":(
                float(capital)*(auto-manual)
                if capital is not None and auto is not None and manual is not None else None
            ),
            "auto_minus_assisted_pnl":(
                float(capital)*(auto-assisted)
                if capital is not None and auto is not None and assisted is not None else None
            ),
        }

    def resolve_market(
        self,
        market_id:str,
        decision_as_of:str,
        outcome_as_of:str,
        realized_returns:dict[str,float],
    )->dict:
        market=normalize_market_id(market_id)
        resolved=[]
        partial=[]
        for decision_id,row in list(self._decisions.items()):
            if str(row.get("market_id"))!=market:
                continue
            if str(row.get("market_as_of"))!=str(decision_as_of):
                continue
            if str(row.get("status"))=="RESOLVED":
                continue
            updated=self._resolve_row(dict(row),realized_returns,outcome_as_of)
            self._decisions[decision_id]=updated
            (resolved if updated["status"]=="RESOLVED" else partial).append(decision_id)
        if resolved or partial:
            self._persist()
            self._event(
                "OUTCOME_AUTO_RESOLUTION",
                market_id=market,
                decision_as_of=decision_as_of,
                outcome_as_of=outcome_as_of,
                resolved_decision_ids=resolved,
                partial_decision_ids=partial,
            )
        return {
            "market_id":market,
            "decision_as_of":decision_as_of,
            "outcome_as_of":outcome_as_of,
            "resolved_decision_ids":resolved,
            "partial_decision_ids":partial,
        }

    def submit_outcome(self,outcome:TraderShadowOutcome)->dict:
        try:
            row=dict(self._decisions[outcome.decision_id])
        except KeyError as exc:
            raise KeyError(f"unknown trader shadow decision: {outcome.decision_id}") from exc
        updated=self._resolve_row(row,outcome.realized_returns,outcome.outcome_as_of)
        self._decisions[outcome.decision_id]=updated
        self._persist()
        self._event(
            "OUTCOME_SUBMITTED",
            decision_id=outcome.decision_id,
            trader_id=updated.get("trader_id"),
            market_id=updated.get("market_id"),
            status=updated.get("status"),
        )
        return updated

    def history(
        self,
        trader_id:str,
        market_id:str|None=None,
        limit:int=100,
    )->list[dict]:
        trader=_clean_trader_id(trader_id)
        market=normalize_market_id(market_id) if market_id else None
        rows=[
            row for row in self._decisions.values()
            if row.get("trader_id")==trader
            and (market is None or row.get("market_id")==market)
        ]
        rows.sort(key=lambda row:str(row.get("created_at") or ""))
        return rows[-max(1,min(1000,int(limit))):]

    def daily_summary(self,trader_id:str,market_id:str)->dict:
        trader=_clean_trader_id(trader_id)
        market=normalize_market_id(market_id)
        rows=self.history(trader,market,1000)
        latest=rows[-1] if rows else None
        resolved=[row for row in rows if row.get("status")=="RESOLVED"]
        latest_resolved=resolved[-1] if resolved else None
        comparison=(latest_resolved.get("outcome") or {}).get("comparison") if latest_resolved else None
        result_rows=(latest_resolved.get("outcome") or {}).get("results") if latest_resolved else None
        return {
            "report_type":"TRADER_SHADOW_DAILY_COMPARISON",
            "version":self.version,
            "trader_id":trader,
            "market_id":market,
            "currency":MARKET_REGISTRY.get(market).currency,
            "latest_decision":{
                "decision_id":latest.get("decision_id"),
                "market_as_of":latest.get("market_as_of"),
                "status":latest.get("status"),
                "capital":latest.get("capital"),
                "decision_process":latest.get("decision_process"),
                "decision_result":latest.get("decision_result"),
                "routes":latest.get("routes"),
            } if latest else None,
            "latest_resolved":{
                "decision_id":latest_resolved.get("decision_id"),
                "decision_as_of":latest_resolved.get("market_as_of"),
                "outcome_as_of":(latest_resolved.get("outcome") or {}).get("outcome_as_of"),
                "capital":latest_resolved.get("capital"),
                "results":result_rows,
                "comparison":comparison,
            } if latest_resolved else None,
            "simple_view":{
                "trader":{
                    "net_return":(result_rows or {}).get("trader",{}).get("net_return"),
                    "net_pnl":(result_rows or {}).get("trader",{}).get("net_pnl"),
                    "ending_value":(result_rows or {}).get("trader",{}).get("ending_value"),
                },
                "triaid_assisted":{
                    "net_return":(result_rows or {}).get("assisted",{}).get("net_return"),
                    "net_pnl":(result_rows or {}).get("assisted",{}).get("net_pnl"),
                    "ending_value":(result_rows or {}).get("assisted",{}).get("ending_value"),
                },
                "triaid_auto":{
                    "net_return":(result_rows or {}).get("auto",{}).get("net_return"),
                    "net_pnl":(result_rows or {}).get("auto",{}).get("net_pnl"),
                    "ending_value":(result_rows or {}).get("auto",{}).get("ending_value"),
                },
                "difference":comparison,
            } if latest_resolved else None,
            "reporting_policy":{
                "capital_optional":True,
                "without_capital":"SHOW_RETURNS_ONLY",
                "with_capital":"SHOW_INVESTED_NOTIONAL_NET_PNL_AND_ENDING_VALUE",
                "comparison_routes":["TRADER","TRIAID_ASSISTED","TRIAID_AUTO"],
                "broker_execution_enabled":False,
            },
        }

    def status(self)->dict:
        rows=list(self._decisions.values())
        return {
            "version":self.version,
            "decision_count":len(rows),
            "pending_count":sum(1 for row in rows if row.get("status")=="PENDING_OUTCOME"),
            "partial_count":sum(1 for row in rows if row.get("status")=="PARTIAL_OUTCOME"),
            "resolved_count":sum(1 for row in rows if row.get("status")=="RESOLVED"),
            "automation_policy":{
                "minimal_input":"TRADER_ID_MARKET_DECISION_PROCESS_DECISION_RESULT_OPTIONAL_CAPITAL",
                "auto_create_shadow_account":True,
                "full_strategy_pool_default":True,
                "weights_optional_equal_weight_default":True,
                "three_route_comparison_auto_generated":True,
                "formal_next_period_outcome_auto_resolved":True,
                "broker_execution":False,
                "global_evidence_mutation":False,
                "open_strategy_interface_catalog":True,
                "custom_strategy_adapter":True,
                "first_observation_auto_enters_shadow":True,
                "shadow_strategy_simulation":True,
                "active_required_for_global_allocation":True,
            },
        }
