from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep
from types import SimpleNamespace

from triaid_fin.home_brief import HomeBriefProjection
from triaid_fin.market_registry import MARKET_REGISTRY
from triaid_fin.projection_cache import ReadThroughProjectionCache
from triaid_fin.ui_ports import MarketPageReadPort


market=SimpleNamespace(
    market_id="US",
    as_of="2026-09-24",
    regime="risk_on",
    metadata={"experiment_mode":"US_RETURN_MAX_CAPACITY","evidence_eligible":True},
)
evaluation=SimpleNamespace(
    status="EVALUATED",
    baseline_return=0.01,
    triaid_return=0.012,
    excess_return=0.002,
)
group=SimpleNamespace(
    members=["P00_BUY_HOLD","P04_TREND50"],
    weights={"P00_BUY_HOLD":0.5,"P04_TREND50":0.5},
)
decision=SimpleNamespace(
    weights_after={"P00_BUY_HOLD":0.4,"P04_TREND50":0.6},
)
run=SimpleNamespace(
    run_id="US-20260924-001",
    market=market,
    status="VERIFIED",
    strategy_group=group,
    triaid_decision=decision,
    evaluation=evaluation,
)
preview=SimpleNamespace(
    run_id="US-PREVIEW-002",
    market=SimpleNamespace(
        market_id="US",
        as_of="2026-09-25",
        regime="risk_on",
        metadata={"experiment_mode":"US_RETURN_MAX_CAPACITY","run_scope":"MANUAL_PREVIEW"},
    ),
    evaluation=SimpleNamespace(
        status="EVALUATED",
        baseline_return=0.1,
        triaid_return=0.5,
        excess_return=0.4,
    ),
)


class ReadStub(MarketPageReadPort):
    def __init__(self):
        self.history_calls=0

    def all_runs(self):
        self.history_calls+=1
        return [run,preview]

    def latest_decision_run(self,market_id):
        return run if market_id=="US" else None

    def strategy_cards(self,lang,market_id):
        if market_id!="US":
            return []
        return [
            {"strategy_id":"P00_BUY_HOLD","name":"买入持有" if lang=="zh" else "Buy & Hold"},
            {"strategy_id":"P04_TREND50","name":"趋势" if lang=="zh" else "Trend"},
        ]


read=ReadStub()
projection=HomeBriefProjection(read)
brief=projection.full()
assert brief["version"]=="home-brief@1.0.0"
assert brief["integrity"]["passed"] is True
assert set(brief["markets"])==set(MARKET_REGISTRY.market_ids)
assert read.history_calls==1
us=brief["markets"]["US"]
assert us["run_id"]==run.run_id
assert us["status"]=="READY"
assert us["selection"]["selected_count"]==2
assert us["selection"]["changed_count"]==2
assert us["selection"]["selected_names_zh"][0]=="趋势"
assert us["selection"]["selected_names_en"][0]=="Trend"
assert us["latest_evaluated"]["evaluation"]["excess_return"]==0.002
assert us["latest_evaluated"]["run_id"]!="US-PREVIEW-002"
assert us["data_maturity"]=="FORMAL_COMPLETED_SESSION_ONLY"
assert brief["performance_contract"]["no_market_data_fetch"] is True
assert brief["performance_contract"]["no_daily_report_rebuild"] is True

for other in set(MARKET_REGISTRY.market_ids)-{"US"}:
    assert brief["markets"][other]["status"]=="WAITING"
    assert brief["markets"][other]["latest_evaluated"] is None

cache=ReadThroughProjectionCache(max_entries=2)
counter={"builds":0}
count_lock=Lock()
def build():
    with count_lock:
        counter["builds"]+=1
    sleep(0.025)
    return {"integrity":{"passed":True},"sections":{"daily":{"as_of":"2026-09-24"}}}
with ThreadPoolExecutor(max_workers=8) as executor:
    rows=list(executor.map(
        lambda _:cache.read(("page","US","zh"),build,ttl_seconds=0.5),
        range(8),
    ))
assert counter["builds"]==1
assert sum(1 for _,hit in rows if hit) == 7
rows[0][0]["sections"]["daily"]["as_of"]="CORRUPTED_IN_CALLER"
value,hit=cache.read(("page","US","zh"),build,ttl_seconds=0.5)
assert hit is True
assert value["sections"]["daily"]["as_of"]=="2026-09-24"
cache.invalidate(("page","US"))
value,hit=cache.read(("page","US","zh"),build,ttl_seconds=0.5)
assert hit is False and counter["builds"]==2
value,hit=cache.read(("page","US","zh"),build,ttl_seconds=0.5,force=True)
assert hit is False and counter["builds"]==3
failures={"calls":0}
def failure():
    failures["calls"]+=1
    return {"integrity":{"passed":False},"sections":{}}
cache.read(("page","HK","zh"),failure,ttl_seconds=60)
cache.read(("page","HK","zh"),failure,ttl_seconds=60)
assert failures["calls"]==2

print("TRIAID_HOME_FIRST_PAINT_AND_SINGLEFLIGHT_SMOKE_PASS",{
    "markets":sorted(brief["markets"]),
    "memory_history_scans":read.history_calls,
    "singleflight_parallel_callers":8,
    "singleflight_builds":1,
    "preview_as_formal_evidence":False,
    "failed_projections_cached":False,
})
