from __future__ import annotations

from triaid_fin.account_registry import ACCOUNT_REGISTRY, register_account, register_strategy_pool
from triaid_fin.contracts import AccountProfile, StrategyPoolSpec
from triaid_fin.market_data import get_market_data_hub
from triaid_fin.market_registry import (
    MarketSpec,
    evidence_market_ids,
    market_ids,
    normalize_market_id,
    register_market,
)
from triaid_fin.risk_control import CrossMarketRiskControlExperiment
from triaid_fin.risk_graph import RISK_GRAPH
from triaid_fin.risk_warning import RiskWarningSystem
from triaid_fin.strategy_evolution import StrategyEvolutionModule
from triaid_fin.strategy_registry import POLICY_IDS, strategy_ids_for_market
from triaid_fin.trading_calendar import trading_day_info


class FakeStore:
    def __init__(self):
        self.json={}
    def load_json(self,name,default=None):
        return self.json.get(name,default)
    def save_json(self,name,payload):
        self.json[name]=payload
        return payload


register_market(
    MarketSpec(
        market_id="JP",
        benchmark="1306.T",
        assets=("1306.T","1321.T","2510.T"),
        risk_assets=("1306.T","1321.T"),
        defensive_assets=("2510.T",),
        currency="JPY",
        reference_capital=1_000_000_000.0,
        base_cost_bps=2.0,
        impact_coefficient_bps=50.0,
        max_participation_adv=0.02,
        timezone="Asia/Tokyo",
        aliases=("JPX","JAPAN"),
        balanced_risk_weight=0.65,
        research_indexes=(("NIKKEI_225","^N225"),),
        primary_index_label="NIKKEI_225",
        session_schedule={
            "PREOPEN":(("08:00","09:00"),),
            "OPEN":(("09:00","11:30"),("12:30","15:30")),
            "BREAK":(("11:30","12:30"),),
            "POSTCLOSE":(("15:30","18:00"),),
        },
        metadata={},
    ),
    persist=False,
)

assert normalize_market_id("JPX")=="JP"
assert "JP" in market_ids()
assert "JP" not in evidence_market_ids(), "registration must not imply evidence readiness"

# Portable base strategies should automatically be available to a new market.
assert strategy_ids_for_market("JP")==POLICY_IDS

register_strategy_pool(
    StrategyPoolSpec(
        pool_id="JP_ACCOUNT_POOL",
        allowed_strategy_ids=["P00_BUY_HOLD","P28_CASH"],
        denied_strategy_ids=["P00_BUY_HOLD"],
        max_group_size=1,
    ),
    replace=True,
    persist=False,
)
register_account(
    AccountProfile(
        account_id="JP_ACCOUNT_001",
        strategy_pool_id="JP_ACCOUNT_POOL",
        base_currency="JPY",
        capital=250_000_000.0,
        allowed_markets=["JPX"],
        risk_budget=0.70,
        max_drawdown_constraint=-0.15,
        objective="MAXIMIZE_NET_RETURN",
    ),
    replace=True,
    persist=False,
)

account=ACCOUNT_REGISTRY.get_account("JP_ACCOUNT_001")
assert account.allowed_markets==["JP"]
assert strategy_ids_for_market("JP",account_id="JP_ACCOUNT_001")==("P28_CASH",)
assert strategy_ids_for_market("US",account_id="JP_ACCOUNT_001")==()

# A new registered market keeps its identity in strategy evolution and is not
# silently treated as US.
evolution=StrategyEvolutionModule(FakeStore())
jp_profile=evolution.active("JP")
assert jp_profile.market_id=="JP"
assert jp_profile.version.startswith("strategy-rules-jp@")

# Registration alone must not invent a market-data source.
caps=get_market_data_hub().capabilities("JP")["JP"]
assert caps
assert all(not row["supported"] for row in caps.values())

# Registration alone must not invent an official trading calendar.
calendar=trading_day_info("JP","2026-09-23")
assert calendar["calendar_known"] is False
assert calendar["reason"]=="CALENDAR_YEAR_UNAVAILABLE"

# The risk graph expands automatically, but an unobserved market must not
# dilute the risk score or be interpreted as safe.
pairs=set(RISK_GRAPH.market_pairs())
assert ("JP","US") in pairs
latent={
    "current_state":{
        "features":{
            "JP_DRAWDOWN_STRESS_252":0.10,
            "JP_NEGATIVE_MOMENTUM_63":-0.04,
        },
        "point_in_time_percentiles":{
            "JP_NEGATIVE_MOMENTUM_63":0.80,
        },
    }
}
score,drivers=RiskWarningSystem._market_deterioration_score(latent)
assert abs(score-(0.60*0.50+0.40*0.80))<1e-12
assert any(row["id"]=="JP_DRAWDOWN_STRESS_252" for row in drivers)

jp_unavailable=CrossMarketRiskControlExperiment._market_row(
    "JP",
    {"features":{},"point_in_time_percentiles":{},"composites":{}},
    {"overall":{"risk_pressure_index":0.0}},
)
assert jp_unavailable["data_available"] is False
assert jp_unavailable["risk_control_stage"]=="DATA_UNAVAILABLE"
assert jp_unavailable["drawdown_stress_252"] is None

print("TRIAID_N_MARKET_MULTI_ACCOUNT_SMOKE_PASS",{
    "registered_markets":market_ids(),
    "evidence_markets":evidence_market_ids(),
    "jp_profile":jp_profile.version,
    "jp_account_strategies":strategy_ids_for_market("JP",account_id="JP_ACCOUNT_001"),
    "jp_data_supported":{k:v["supported"] for k,v in caps.items()},
    "jp_calendar_known":calendar["calendar_known"],
    "risk_score":score,
})
