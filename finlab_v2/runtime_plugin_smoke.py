from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from triaid_fin.runtime_jobs import (
    RUNTIME_JOB_REGISTRY,
    RuntimeJobContext,
)


class FakeJournal:
    def __init__(self)->None:
        self.lines={}
    def append_jsonl(self,name,payload)->None:
        self.lines.setdefault(name,[]).append(payload)


class FakeMarketDataPort:
    def auction_shadow_probe(self,market):
        return {
            "market_id":market,
            "available_symbols":5,
            "total_symbols":5,
            "all_symbols_available":True,
            "symbols":{},
        }


class FakeResearchPort:
    def long_cycle_hypothesis_run(self,force=False):
        return {
            "experiment_id":"LONG-1",
            "as_of":"2026-09-24",
            "hypotheses":{
                "downturn_confirmation":{"state":"NOT_CONFIRMED"},
                "stretch_vulnerability":{"state":"HIGH_STRETCH_EVIDENCE"},
            },
        }
    def cross_market_crash_run(self,force=False):
        return {
            "experiment_id":"CRASH-1",
            "as_of":"2026-09-24",
            "detected_crashes":{"paired_event_rows":12},
            "canonical_episode_studies":{},
        }
    def latent_hazard_run(self,force=False):
        return {
            "experiment_id":"HAZARD-1",
            "as_of":"2026-09-24",
            "current_state":{
                "state_label":"WATCH",
                "supported_trigger_count":1,
            },
        }
    def policy_curve_run(self,force=False):
        return {
            "snapshot_id":"POLICY-1",
            "data_quality":{"term_curve_usable":True},
        }
    def hazard_prospective_freeze(self,latent,policy):
        return {"ledger_id":"LEDGER-1"}
    def hazard_prospective_resolve(self):
        return {"updated_outcomes":2}
    def risk_warning_run(self,force=False):
        return {
            "warning_id":"RISK-1",
            "overall":{"risk_pressure_index":40.0,"risk_band":"ELEVATED"},
            "horizon_estimates":{
                "20":{"risk_pressure_index":30.0},
                "60":{"risk_pressure_index":35.0},
                "120":{"risk_pressure_index":45.0},
                "250":{"risk_pressure_index":50.0},
            },
        }
    def risk_control_run(self,force=False):
        return {
            "experiment_id":"CONTROL-1",
            "risk_control_experiment":{"stage":"WATCH_ONLY"},
        }


class FakeServices:
    def __init__(self)->None:
        self.journal=FakeJournal()
        self.market_data=FakeMarketDataPort()
        self.research=FakeResearchPort()


async def main()->None:
    services=FakeServices()
    state={}
    errors={}

    cn=RuntimeJobContext(
        services=services,
        market_id="CN",
        phase="PREOPEN",
        timeout_seconds=30,
        state=state,
        errors=errors,
        now_utc=datetime(2026,9,24,1,30,tzinfo=timezone.utc),
    )
    assert RUNTIME_JOB_REGISTRY.stage("CN_PREOPEN_AUCTION_SHADOW")=="PRE_REFRESH"
    await RUNTIME_JOB_REGISTRY.run("CN_PREOPEN_AUCTION_SHADOW",cn)
    auction=state["CN_PREOPEN_AUCTION_SHADOW"]
    assert auction["last_day"]=="2026-09-24"
    assert auction["latest"]["market_id"]=="CN"
    assert len(services.journal.lines["auction_shadow_events.jsonl"])==1

    # Once-per-day gate must make the second invocation idempotent.
    await RUNTIME_JOB_REGISTRY.run("CN_PREOPEN_AUCTION_SHADOW",cn)
    assert len(services.journal.lines["auction_shadow_events.jsonl"])==1

    us=RuntimeJobContext(
        services=services,
        market_id="US",
        phase="POSTCLOSE",
        timeout_seconds=30,
        state=state,
        errors=errors,
        now_utc=datetime(2026,9,24,21,0,tzinfo=timezone.utc),
    )
    for job in (
        "LONG_CYCLE_POSTCLOSE",
        "CROSS_MARKET_POSTCLOSE",
        "HAZARD_RESEARCH_POSTCLOSE",
    ):
        assert RUNTIME_JOB_REGISTRY.stage(job)=="POST_REFRESH"
        await RUNTIME_JOB_REGISTRY.run(job,us)

    assert state["LONG_CYCLE_POSTCLOSE"]["latest"]["experiment_id"]=="LONG-1"
    assert state["CROSS_MARKET_POSTCLOSE"]["latest"]["paired_event_rows"]==12
    assert state["HAZARD_RESEARCH_POSTCLOSE"]["latest"]["risk_warning_id"]=="RISK-1"
    assert not errors

    print(
        "TRIAID_RUNTIME_PLUGIN_SMOKE_PASS",
        {
            "jobs":list(RUNTIME_JOB_REGISTRY.names()),
            "state_keys":sorted(state),
        },
    )


asyncio.run(main())
