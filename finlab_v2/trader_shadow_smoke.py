from __future__ import annotations

from triaid_fin.account_registry import AccountRegistry
from triaid_fin.contracts import MarketSnapshot, StrategyState
from triaid_fin.core import TriaidCoreModule
from triaid_fin.external_strategy import ExternalStrategyModule
from triaid_fin.strategy_population import StrategyPopulationModule
from triaid_fin.trader_shadow import (
    TraderCustomStrategyObservation,
    TraderCustomStrategyRegistration,
    TraderDecisionResult,
    TraderShadowDecision,
    TraderShadowModule,
)


class FakeStore:
    def __init__(self):
        self.json={}
        self.jsonl={}
    def load_json(self,name,default=None):
        return self.json.get(name,default)
    def save_json(self,name,payload):
        self.json[name]=payload
        return payload
    def append_jsonl(self,name,payload):
        self.jsonl.setdefault(name,[]).append(dict(payload))
        return payload
    def read_jsonl(self,name,limit=100):
        return list(self.jsonl.get(name,[]))[-limit:]


class Params:
    version="trader-shadow-test-core"
    intervention_strength=1.0


store=FakeStore()
accounts=AccountRegistry()
population=StrategyPopulationModule()
external=ExternalStrategyModule(store,population,accounts)
core=TriaidCoreModule(Params())
module=TraderShadowModule(
    store,
    accounts,
    population,
    external,
    core_provider=lambda:core,
)

catalog=module.catalog("ALICE","US")
assert catalog["strategy_count"]>=29
assert catalog["interaction_policy"]["default"]=="FULL_POOL_AUTO"
assert catalog["interaction_policy"]["weights_optional"] is True
assert catalog["interaction_policy"]["capital_optional"] is True
assert any(row["family"]=="time_series_momentum" for row in catalog["families"])
assert catalog["strategy_interface_catalog_version"]=="strategy-interface-catalog@1.0.0"
assert catalog["catalog_policy"]["fixed_pool_is_not_the_boundary"] is True
custom_interface=next(row for row in catalog["interfaces"] if row["interface_id"]=="TRADER_CUSTOM")
assert custom_interface["status"]=="OPEN"
assert custom_interface["accepts_unseen_strategy"] is True

account,pool=module.ensure_trader("ALICE")
assert account.metadata["shadow_trading"] is True
assert pool.metadata["selection_mode"]=="FULL_MARKET_AUTO_DEFAULT"
assert pool.allowed_strategy_ids==[]

states=[
    StrategyState(
        strategy_id="P00_BUY_HOLD",
        eligible=True,
        lifecycle="active",
        expected_net_return=0.08,
        estimated_cost=0.001,
        risk=0.15,
        oos_marginal_value=0.01,
        recent_returns=[0.001]*30,
    ),
    StrategyState(
        strategy_id="P04_TREND50",
        eligible=True,
        lifecycle="active",
        expected_net_return=0.12,
        estimated_cost=0.001,
        risk=0.10,
        oos_marginal_value=0.02,
        recent_returns=[0.002]*30,
    ),
    StrategyState(
        strategy_id="P16_REV5",
        eligible=True,
        lifecycle="active",
        expected_net_return=0.10,
        estimated_cost=0.001,
        risk=0.11,
        oos_marginal_value=0.015,
        recent_returns=[-0.001,0.002]*15,
    ),
    StrategyState(
        strategy_id="P28_CASH",
        eligible=True,
        lifecycle="active",
        expected_net_return=0.0,
        estimated_cost=0.0,
        risk=0.0,
        oos_marginal_value=0.0,
        recent_returns=[0.0]*30,
    ),
]
custom_registered=module.register_custom_strategy(
    TraderCustomStrategyRegistration(
        trader_id="ALICE",
        local_strategy_id="DISCRETIONARY_BREAKOUT",
        market_support=["US"],
        name="主观突破策略",
        name_en="Discretionary Breakout",
        summary="交易员自己的突破判断，不属于固定 P00-P28 池。",
    )
)
custom_sid=custom_registered["strategy"]["strategy_id"]
assert custom_registered["strategy"]["isolation_state"]=="QUARANTINE"

catalog_pre_obs=module.catalog("ALICE","US")
custom_pre=next(
    item
    for family in catalog_pre_obs["families"]
    for item in family["strategies"]
    if item["strategy_id"]==custom_sid
)
assert custom_pre["source"]=="TRADER_CUSTOM"
assert custom_pre["readiness"]=="NEEDS_OBSERVATION"
assert custom_pre["trader_route_selectable"] is False

custom_observed=module.observe_custom_strategy(
    TraderCustomStrategyObservation(
        trader_id="ALICE",
        local_strategy_id="DISCRETIONARY_BREAKOUT",
        market_id="US",
        as_of="2026-09-24",
        expected_net_return=0.30,
        risk=0.14,
        uncertainty=0.04,
        estimated_cost=0.002,
        recent_returns=[0.003,-0.001,0.004,0.002]*8,
        max_drawdown=-0.06,
    )
)
assert custom_observed["isolation_state"]=="SHADOW"
assert custom_observed["shadow_simulation_eligible"] is True
assert custom_observed["global_allocation_eligible"] is False
custom_state=StrategyState.model_validate(custom_observed["normalized_state"])
assert custom_state.lifecycle=="shadow"
assert custom_state.eligible is True
states.append(custom_state)

catalog_post_obs=module.catalog("ALICE","US")
custom_post=next(
    item
    for family in catalog_post_obs["families"]
    for item in family["strategies"]
    if item["strategy_id"]==custom_sid
)
assert custom_post["readiness"]=="SHADOW_READY"
assert custom_post["trader_route_selectable"] is True
assert custom_post["triaid_shadow_simulation_eligible"] is True
assert custom_post["global_allocation_eligible"] is False

market=MarketSnapshot(
    market_id="US",
    as_of="2026-09-24",
    snapshot_id="US:2026-09-24:DAILY",
    regime="risk_on",
    metadata={},
)

submission=TraderShadowDecision(
    trader_id="ALICE",
    market_id="US",
    decision_process="Trend remains positive; keep market exposure but combine medium-term trend with reversal.",
    decision_result=TraderDecisionResult(
        strategy_ids=["P00_BUY_HOLD","P04_TREND50"],
    ),
    capital=1_000_000.0,
)
row=module.submit(submission,market,states,account,pool)
assert row["status"]=="PENDING_OUTCOME"
assert set(row["routes"])=={"trader","assisted","auto"}
assert abs(row["routes"]["trader"]["weights"]["P00_BUY_HOLD"]-0.5)<1e-12
assert abs(row["routes"]["trader"]["weights"]["P04_TREND50"]-0.5)<1e-12
assert row["routes"]["trader"]["invested_notional"]==1_000_000.0
assert row["automation"]["broker_execution_enabled"] is False
assert row["automation"]["full_strategy_pool_default"] is True
assert "P16_REV5" in row["routes"]["auto"]["selected_group_before_core"]

custom_row=module.submit(
    TraderShadowDecision(
        trader_id="ALICE",
        market_id="US",
        decision_process="Use my discretionary breakout together with trend.",
        decision_result=TraderDecisionResult(
            strategy_ids=[custom_sid,"P04_TREND50"],
            weights={custom_sid:0.6,"P04_TREND50":0.4},
        ),
        capital=1_000_000.0,
    ),
    market,
    states,
    account,
    pool,
)
assert custom_sid in custom_row["routes"]["trader"]["strategy_ids"]
assert custom_sid in custom_row["routes"]["assisted"]["shadow_strategy_ids"]
assert custom_row["routes"]["assisted"]["shadow_simulation_enabled"] is True
assert custom_row["routes"]["auto"]["shadow_simulation_enabled"] is True
assert custom_sid in custom_row["routes"]["auto"]["selected_group_before_core"]

resolved=module.resolve_market(
    "US",
    "2026-09-24",
    "2026-09-25",
    {
        "P00_BUY_HOLD":0.01,
        "P04_TREND50":0.02,
        "P16_REV5":-0.005,
        "P28_CASH":0.0,
        custom_sid:0.03,
    },
)
assert row["decision_id"] in resolved["resolved_decision_ids"]
summary=module.daily_summary("ALICE","US")
assert summary["latest_resolved"]["decision_id"]==row["decision_id"]
simple=summary["simple_view"]
assert simple["trader"]["net_pnl"] is not None
assert simple["triaid_assisted"]["net_pnl"] is not None
assert simple["triaid_auto"]["net_pnl"] is not None
assert simple["difference"]["assisted_minus_trader_pnl"] is not None
assert simple["difference"]["auto_minus_trader_pnl"] is not None

# Optional capital means the same comparison can run without inventing a notional.
no_cap=module.submit(
    TraderShadowDecision(
        trader_id="BOB",
        market_id="US",
        decision_process="Use trend only.",
        decision_result=TraderDecisionResult(strategy_ids=["P04_TREND50"]),
    ),
    market,
    states,
    *module.ensure_trader("BOB"),
)
assert no_cap["capital"] is None
assert no_cap["routes"]["trader"]["invested_notional"] is None

# User-entered weights are accepted and normalized rather than adding form choices.
weighted=module.submit(
    TraderShadowDecision(
        trader_id="CAROL",
        market_id="US",
        decision_process="Prefer trend over beta.",
        decision_result=TraderDecisionResult(
            strategy_ids=["P00_BUY_HOLD","P04_TREND50"],
            weights={"P00_BUY_HOLD":0.4,"P04_TREND50":0.8},
        ),
        capital=500_000.0,
    ),
    market,
    states,
    *module.ensure_trader("CAROL"),
)
assert weighted["decision_result"]["weight_input"]["input_weights_normalized"] is True
assert abs(sum(weighted["decision_result"]["weights"].values())-1.0)<1e-12

status=module.status()
assert status["automation_policy"]["three_route_comparison_auto_generated"] is True
assert status["automation_policy"]["formal_next_period_outcome_auto_resolved"] is True
assert status["automation_policy"]["global_evidence_mutation"] is False
assert status["automation_policy"]["open_strategy_interface_catalog"] is True
assert status["automation_policy"]["custom_strategy_adapter"] is True
assert status["automation_policy"]["first_observation_auto_enters_shadow"] is True
assert status["automation_policy"]["shadow_strategy_simulation"] is True
assert status["automation_policy"]["active_required_for_global_allocation"] is True

print("TRIAID_TRADER_SHADOW_SMOKE_PASS",{
    "catalog_count":catalog["strategy_count"],
    "decision_id":row["decision_id"],
    "manual_pnl":simple["trader"]["net_pnl"],
    "assisted_pnl":simple["triaid_assisted"]["net_pnl"],
    "auto_pnl":simple["triaid_auto"]["net_pnl"],
    "no_capital_mode":no_cap["routes"]["trader"]["invested_notional"],
    "custom_strategy_id":custom_sid,
    "custom_strategy_readiness":custom_post["readiness"],
    "custom_in_auto_shadow_group":custom_sid in custom_row["routes"]["auto"]["selected_group_before_core"],
})
