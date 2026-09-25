from __future__ import annotations

from triaid_fin.account_registry import AccountRegistry
from triaid_fin.contracts import AccountProfile, MarketSnapshot, StrategyGroup, StrategyPoolSpec, StrategyState
from triaid_fin.core import TriaidCoreModule
from triaid_fin.external_strategy import (
    ExternalStrategyModule,
    ExternalStrategyObservation,
    ExternalStrategySpec,
)
from triaid_fin.strategy_population import StrategyPopulationModule


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


store=FakeStore()
accounts=AccountRegistry()
accounts.register_pool(
    StrategyPoolSpec(pool_id="TRADER_A_POOL",max_group_size=5),
    replace=True,
)
accounts.register_pool(
    StrategyPoolSpec(pool_id="TRADER_B_POOL",max_group_size=5),
    replace=True,
)
accounts.register_account(
    AccountProfile(
        account_id="TRADER_A",
        strategy_pool_id="TRADER_A_POOL",
        allowed_markets=["US"],
        capital=5_000_000.0,
        risk_budget=0.60,
        max_strategy_weight=0.55,
        max_drawdown_constraint=-0.15,
    ),
    replace=True,
)
accounts.register_account(
    AccountProfile(
        account_id="TRADER_B",
        strategy_pool_id="TRADER_B_POOL",
        allowed_markets=["US"],
        capital=3_000_000.0,
    ),
    replace=True,
)

population=StrategyPopulationModule()
module=ExternalStrategyModule(store,population,accounts)

spec=ExternalStrategySpec(
    provider_id="ALPHA_DESK",
    local_strategy_id="MOMENTUM_01",
    account_id="TRADER_A",
    strategy_pool_id="TRADER_A_POOL",
    market_support=["US"],
    name_zh="交易员A动量策略",
    name_en="Trader A Momentum",
)
registered=module.register(spec)
sid=registered["strategy"]["strategy_id"]
assert sid=="EXT::ALPHA_DESK::MOMENTUM_01"
assert registered["strategy"]["isolation_state"]=="QUARANTINE"
assert registered["strategy"]["allocation_eligible"] is False

good=ExternalStrategyObservation(
    provider_id="ALPHA_DESK",
    local_strategy_id="MOMENTUM_01",
    market_id="US",
    as_of="2026-09-24",
    expected_net_return=0.18,
    risk=0.12,
    uncertainty=0.03,
    estimated_cost=0.01,
    oos_marginal_value=0.05,
    recent_returns=[0.01,0.02,-0.01,0.03],
    metrics={"max_drawdown":-0.08},
)
q=module.ingest(good)
assert q["normalized_state"]["lifecycle"]=="shadow"
assert q["normalized_state"]["eligible"] is False
assert q["allocation_eligible"] is False
assert population.select("US",[StrategyState.model_validate(q["normalized_state"])],5).members==[]

shadow=module.promote(sid,"SHADOW")
assert shadow["strategy"]["isolation_state"]=="SHADOW"
s=module.ingest(good)
assert s["normalized_state"]["lifecycle"]=="shadow"
assert s["normalized_state"]["eligible"] is True
assert population.select("US",[StrategyState.model_validate(s["normalized_state"])],5).members==[]

active=module.promote(sid,"ACTIVE")
assert active["strategy"]["allocation_eligible"] is True
a=module.ingest(good)
active_state=StrategyState.model_validate(a["normalized_state"])
selected=population.select(
    "US",
    [active_state],
    5,
    max_weight_override=accounts.get_account("TRADER_A").max_strategy_weight,
)
assert sid in selected.members
assert abs(selected.weights[sid]-0.55)<1e-12
assert selected.diagnostics["max_strategy_weight_constraint"]==0.55

# Pool/account isolation: another trader cannot see Trader A's external state.
assert module.states_for_account("US","TRADER_B","TRADER_B_POOL")==[]
assert module.strategy_ids_for_account("US","TRADER_B","TRADER_B_POOL")==()

# Account hard constraint is applied at the adapter boundary.
breach=good.model_copy(update={"metrics":{"max_drawdown":-0.25}})
b=module.ingest(breach)
breach_state=StrategyState.model_validate(b["normalized_state"])
assert breach_state.risk_ok is False
assert sid not in population.select(
    "US",
    [breach_state],
    5,
    max_weight_override=0.55,
).members

# Freezing is local and immediate; no other pool is affected.
frozen=module.promote(sid,"FROZEN")
assert frozen["strategy"]["isolation_state"]=="FROZEN"
f=module.ingest(good)
assert f["normalized_state"]["lifecycle"]=="frozen"
assert f["allocation_eligible"] is False

# Core consumes the account-aware group cap and total risk budget without
# knowing anything about trader/provider implementations.
class Params:
    version="test-core"
    intervention_strength=1.0

state_2=active_state.model_copy(update={
    "strategy_id":"EXT::ALPHA_DESK::MOMENTUM_02",
    "expected_net_return":0.17,
})
group=StrategyGroup(
    group_version="test",
    config_version="test",
    market_id="US",
    members=[active_state.strategy_id,state_2.strategy_id],
    weights={active_state.strategy_id:0.5,state_2.strategy_id:0.5},
    reasons={},
    diagnostics={"max_strategy_weight_constraint":0.55},
)
decision=TriaidCoreModule(Params()).decide(
    MarketSnapshot(
        market_id="US",
        as_of="2026-09-24",
        snapshot_id="test",
        regime="risk_on",
        metadata={"account_risk_budget":0.60},
    ),
    group,
    [active_state,state_2],
)
risky_total=sum(decision.weights_after.values())
assert risky_total<=0.6000000001
assert max(decision.weights_after.values())<=0.55+1e-12
assert decision.diagnostics["account_risk_budget"]==0.60
assert decision.diagnostics["risk_budget_scaled"] is True

feedback=module.feedback(100,"TRADER_A")
events=[row["event"] for row in feedback]
assert "REGISTERED" in events
assert "OBSERVATION_ACCEPTED" in events
assert "ISOLATION_CHANGED" in events
assert module.status("TRADER_A")["isolation_policy"]["fault_isolation"]=="provider/account/pool scoped"

print("TRIAID_EXTERNAL_STRATEGY_MODULE_SMOKE_PASS",{
    "strategy_id":sid,
    "events":events,
    "account_cap":selected.diagnostics["max_strategy_weight_constraint"],
    "trader_b_visible":module.strategy_ids_for_account("US","TRADER_B","TRADER_B_POOL"),
})
