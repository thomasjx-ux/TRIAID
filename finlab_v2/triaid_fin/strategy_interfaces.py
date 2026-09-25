from __future__ import annotations

from .market_registry import normalize_market_id
from .strategy_registry import FAMILIES, POLICY_IDS


SOURCE_LABELS={
    "BUILTIN_CORE":("TRIAID 内置策略","TRIAID Built-in"),
    "MARKET_EXTENSION":("市场扩展策略","Market Extension"),
    "TRADER_CUSTOM":("交易员自定义策略","Trader Custom"),
    "EXTERNAL_ADAPTER":("外部策略接口","External Adapter"),
}


class StrategyInterfaceCatalog:
    """Discovery layer over strategy sources.

    This module does not calculate strategy states or returns. It only exposes
    every strategy/interface the current trader can reach, with explicit
    readiness and eligibility semantics.
    """

    version="strategy-interface-catalog@1.0.0"

    def __init__(self,strategy_population,external_strategies)->None:
        self.strategy_population=strategy_population
        self.external_strategies=external_strategies

    @staticmethod
    def _source_for(strategy_id:str,provider_id:str|None=None)->str:
        if strategy_id in POLICY_IDS:
            return "BUILTIN_CORE"
        if strategy_id.startswith("EXT::"):
            if str(provider_id or "").startswith("TRADER_"):
                return "TRADER_CUSTOM"
            return "EXTERNAL_ADAPTER"
        return "MARKET_EXTENSION"

    @staticmethod
    def _family_for(strategy_id:str,source:str)->str:
        if strategy_id in FAMILIES:
            return FAMILIES[strategy_id]
        if source=="MARKET_EXTENSION":
            return "market_extension"
        if source=="TRADER_CUSTOM":
            return "trader_custom"
        return "external"

    @staticmethod
    def _hard_ok(normalized_state:dict|None)->bool:
        state=dict(normalized_state or {})
        if not state:
            return False
        return bool(
            state.get("eligible",True)
            and not state.get("hard_failure",False)
            and state.get("liquidity_ok",True)
            and state.get("capacity_ok",True)
            and state.get("risk_ok",True)
            and state.get("concentration_ok",True)
        )

    def catalog(
        self,
        market_id:str,
        account_id:str,
        available_strategy_ids:tuple[str,...]|list[str],
    )->dict:
        market=normalize_market_id(market_id)
        available=set(str(x) for x in available_strategy_ids)
        external_rows={
            str(row.get("strategy_id")):row
            for row in (self.external_strategies.status(account_id).get("strategies") or [])
            if market in {str(x).upper() for x in (row.get("market_support") or [])}
        }

        strategies=[]
        for definition in self.strategy_population.definitions():
            sid=definition.strategy_id
            ext=external_rows.get(sid)
            if sid.startswith("EXT::"):
                if ext is None:
                    continue
            else:
                support={str(x).upper() for x in definition.market_support}
                if sid not in POLICY_IDS and "*" not in support and market not in support:
                    continue
                if sid not in available:
                    continue

            source=self._source_for(sid,(ext or {}).get("provider_id"))
            family=self._family_for(sid,source)
            latest=(ext or {}).get("latest_observation") or {}
            normalized=latest.get("normalized_state") or {}
            isolation=(ext or {}).get("isolation_state")
            has_observation=bool(latest)
            hard_ok=self._hard_ok(normalized) if sid.startswith("EXT::") else True
            shadow_simulation_eligible=(
                not sid.startswith("EXT::")
                or (
                    has_observation
                    and isolation in {"SHADOW","ACTIVE"}
                    and hard_ok
                )
            )
            global_allocation_eligible=(
                True
                if not sid.startswith("EXT::")
                else bool((ext or {}).get("allocation_eligible")) and hard_ok
            )
            if not sid.startswith("EXT::"):
                readiness="READY"
            elif not has_observation:
                readiness="NEEDS_OBSERVATION"
            elif isolation=="QUARANTINE":
                readiness="QUARANTINE"
            elif isolation=="SHADOW":
                readiness="SHADOW_READY"
            elif isolation=="ACTIVE":
                readiness="ACTIVE"
            else:
                readiness="FROZEN"

            label=SOURCE_LABELS[source]
            strategies.append({
                "strategy_id":sid,
                "name_zh":definition.name.zh,
                "name_en":definition.name.en,
                "summary_zh":definition.summary.zh,
                "summary_en":definition.summary.en,
                "family":family,
                "source":source,
                "source_zh":label[0],
                "source_en":label[1],
                "readiness":readiness,
                "isolation_state":isolation,
                "has_standardized_observation":has_observation,
                "trader_route_selectable":(
                    True if not sid.startswith("EXT::") else has_observation and hard_ok
                ),
                "triaid_shadow_simulation_eligible":shadow_simulation_eligible,
                "global_allocation_eligible":global_allocation_eligible,
            })

        counts={}
        for row in strategies:
            counts[row["source"]]=counts.get(row["source"],0)+1

        interfaces=[
            {
                "interface_id":"BUILTIN_CORE",
                "name_zh":"TRIAID 内置策略",
                "name_en":"TRIAID Built-in Strategies",
                "status":"READY",
                "strategy_count":counts.get("BUILTIN_CORE",0),
                "accepts_unseen_strategy":False,
            },
            {
                "interface_id":"MARKET_EXTENSION",
                "name_zh":"市场扩展策略包",
                "name_en":"Market Extension Strategy Packs",
                "status":"READY" if counts.get("MARKET_EXTENSION",0) else "EMPTY_FOR_MARKET",
                "strategy_count":counts.get("MARKET_EXTENSION",0),
                "accepts_unseen_strategy":False,
            },
            {
                "interface_id":"TRADER_CUSTOM",
                "name_zh":"交易员自定义策略接口",
                "name_en":"Trader Custom Strategy Adapter",
                "status":"OPEN",
                "strategy_count":counts.get("TRADER_CUSTOM",0),
                "accepts_unseen_strategy":True,
                "onboarding":"REGISTER_ONCE -> FIRST_STANDARDIZED_OBSERVATION_AUTO_ENTERS_SHADOW -> SHADOW_SIMULATION",
                "required_for_registration":["local_strategy_id","name","market_support"],
                "required_for_shadow_model":["as_of","expected_net_return"],
                "optional_state_fields":[
                    "risk","uncertainty","estimated_cost","recent_returns",
                    "max_drawdown","liquidity_ok","capacity_ok","risk_ok","concentration_ok",
                ],
            },
            {
                "interface_id":"EXTERNAL_ADAPTER",
                "name_zh":"其他外部策略适配器",
                "name_en":"Other External Strategy Adapters",
                "status":"OPEN",
                "strategy_count":counts.get("EXTERNAL_ADAPTER",0),
                "accepts_unseen_strategy":True,
                "onboarding":"STANDARDIZED_EXTERNAL_STRATEGY_SPEC_AND_OBSERVATION",
            },
        ]
        return {
            "version":self.version,
            "market_id":market,
            "account_id":account_id,
            "strategy_count":len(strategies),
            "source_counts":counts,
            "interfaces":interfaces,
            "strategies":sorted(strategies,key=lambda row:(row["source"],row["family"],row["strategy_id"])),
            "policy":{
                "fixed_pool_is_not_the_boundary":True,
                "all_registered_and_registerable_interfaces_are_exposed":True,
                "unknown_strategy_can_enter_via_trader_custom_adapter":True,
                "shadow_simulation_does_not_imply_global_allocation":True,
                "no_fabricated_strategy_state":True,
            },
        }
