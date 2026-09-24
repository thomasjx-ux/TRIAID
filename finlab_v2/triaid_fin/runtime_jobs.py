from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from .market_registry import MARKET_REGISTRY, normalize_market_id


RuntimeJobHandler=Callable[["RuntimeJobContext"],Awaitable[dict|None]]


@dataclass
class RuntimeJobContext:
    services: object
    market_id: str
    phase: str
    timeout_seconds: int
    state: dict
    errors: dict

    @property
    def market(self)->str:
        return normalize_market_id(self.market_id)

    @property
    def local_now(self)->datetime:
        tz=ZoneInfo(MARKET_REGISTRY.get(self.market).timezone)
        return datetime.now(tz)

    @property
    def day(self)->str:
        return self.local_now.date().isoformat()

    def job_state(self,job_name:str)->dict:
        return self.state.setdefault(job_name,{})

    def error_key(self,job_name:str)->str:
        return f"{self.market}:{job_name}"

    def clear_error(self,job_name:str)->None:
        self.errors.pop(self.error_key(job_name),None)

    def set_error(self,job_name:str,exc:Exception)->None:
        self.errors[self.error_key(job_name)]=f"{type(exc).__name__}:{exc}"


class RuntimeJobRegistry:
    version="runtime-job-registry@1.0.0"

    def __init__(self)->None:
        self._handlers:dict[str,RuntimeJobHandler]={}
        self._stages:dict[str,str]={}

    def register(
        self,
        name:str,
        handler:RuntimeJobHandler,
        *,
        stage:str="POST_REFRESH",
    )->None:
        key=str(name).strip().upper()
        stage_key=str(stage).strip().upper()
        if not key:
            raise ValueError("runtime job name is required")
        if stage_key not in {"PRE_REFRESH","POST_REFRESH"}:
            raise ValueError("runtime job stage must be PRE_REFRESH or POST_REFRESH")
        if key in self._handlers:
            raise ValueError(f"runtime job already registered:{key}")
        self._handlers[key]=handler
        self._stages[key]=stage_key

    def handler(self,name:str)->RuntimeJobHandler:
        key=str(name).strip().upper()
        try:
            return self._handlers[key]
        except KeyError as exc:
            raise KeyError(f"runtime_job_not_registered:{key}") from exc

    async def run(self,name:str,context:RuntimeJobContext)->dict|None:
        return await self.handler(name)(context)

    def names(self,stage:str|None=None)->tuple[str,...]:
        if stage is None:
            return tuple(self._handlers)
        stage_key=str(stage).strip().upper()
        return tuple(
            name for name in self._handlers
            if self._stages.get(name)==stage_key
        )

    def stage(self,name:str)->str:
        key=str(name).strip().upper()
        if key not in self._handlers:
            raise KeyError(f"runtime_job_not_registered:{key}")
        return self._stages[key]


async def _cn_preopen_auction_shadow(ctx:RuntimeJobContext)->dict|None:
    job="CN_PREOPEN_AUCTION_SHADOW"
    if ctx.phase!="PREOPEN":
        return None
    now=ctx.local_now
    if (now.hour,now.minute)<(9,25):
        return None
    state=ctx.job_state(job)
    if state.get("last_day")==ctx.day:
        return None
    try:
        probe=await asyncio.to_thread(
            ctx.services.market_data_auction_shadow_probe,
            ctx.market,
        )
        event={
            **probe,
            "observed_at":datetime.now(ZoneInfo("UTC")).isoformat(),
            "trade_date":ctx.day,
        }
        ctx.services.journal.append_jsonl("auction_shadow_events.jsonl",event)
        state.update({"last_day":ctx.day,"latest":event})
        ctx.clear_error(job)
        print(
            "TRIAID_ZERO_COST_AUCTION_SHADOW",
            ctx.market,
            probe.get("available_symbols"),
            probe.get("total_symbols"),
            probe.get("all_symbols_available"),
        )
        return event
    except Exception as exc:
        ctx.set_error(job,exc)
        print("TRIAID_ZERO_COST_AUCTION_SHADOW_RECOVERY",ctx.market,ctx.errors[ctx.error_key(job)])
        return None


async def _long_cycle_postclose(ctx:RuntimeJobContext)->dict|None:
    job="LONG_CYCLE_POSTCLOSE"
    if ctx.phase!="POSTCLOSE":
        return None
    state=ctx.job_state(job)
    if state.get("last_day")==ctx.day:
        return None
    try:
        report=await asyncio.wait_for(
            asyncio.to_thread(ctx.services.long_cycle_hypothesis_run,False),
            timeout=max(120,ctx.timeout_seconds),
        )
        latest={
            "experiment_id":report.get("experiment_id"),
            "as_of":report.get("as_of"),
            "downturn_state":(
                (report.get("hypotheses") or {})
                .get("downturn_confirmation",{})
                .get("state")
            ),
            "stretch_state":(
                (report.get("hypotheses") or {})
                .get("stretch_vulnerability",{})
                .get("state")
            ),
        }
        state.update({"last_day":ctx.day,"latest":latest})
        ctx.clear_error(job)
        print(
            "TRIAID_LONG_CYCLE_DAILY",
            ctx.market,
            latest.get("experiment_id"),
            latest.get("as_of"),
            latest.get("downturn_state"),
            latest.get("stretch_state"),
        )
        return latest
    except Exception as exc:
        ctx.set_error(job,exc)
        print("TRIAID_LONG_CYCLE_RECOVERY",ctx.market,ctx.errors[ctx.error_key(job)])
        return None


async def _cross_market_postclose(ctx:RuntimeJobContext)->dict|None:
    job="CROSS_MARKET_POSTCLOSE"
    if ctx.phase!="POSTCLOSE":
        return None
    state=ctx.job_state(job)
    if state.get("last_day")==ctx.day:
        return None
    try:
        report=await asyncio.wait_for(
            asyncio.to_thread(ctx.services.cross_market_crash_run,False),
            timeout=max(180,ctx.timeout_seconds),
        )
        episodes=report.get("canonical_episode_studies") or {}
        latest={
            "experiment_id":report.get("experiment_id"),
            "as_of":report.get("as_of"),
            "paired_event_rows":(
                (report.get("detected_crashes") or {}).get("paired_event_rows")
            ),
            "episodes":{
                key:{
                    "relation":value.get("relation"),
                    "cn_trough_minus_us_trough_calendar_days":value.get(
                        "cn_trough_minus_us_trough_calendar_days"
                    ),
                }
                for key,value in episodes.items()
            },
        }
        state.update({"last_day":ctx.day,"latest":latest})
        ctx.clear_error(job)
        print(
            "TRIAID_N_MARKET_CRASH_LINKAGE_DAILY",
            ctx.market,
            latest.get("experiment_id"),
            latest.get("as_of"),
            latest.get("paired_event_rows"),
        )
        return latest
    except Exception as exc:
        ctx.set_error(job,exc)
        print("TRIAID_N_MARKET_CRASH_LINKAGE_RECOVERY",ctx.market,ctx.errors[ctx.error_key(job)])
        return None


async def _hazard_research_postclose(ctx:RuntimeJobContext)->dict|None:
    job="HAZARD_RESEARCH_POSTCLOSE"
    if ctx.phase!="POSTCLOSE":
        return None
    state=ctx.job_state(job)
    if state.get("last_day")==ctx.day:
        return None
    try:
        latent=await asyncio.wait_for(
            asyncio.to_thread(ctx.services.latent_hazard_run,False),
            timeout=max(240,ctx.timeout_seconds),
        )
        policy=None
        try:
            policy=await asyncio.wait_for(
                asyncio.to_thread(ctx.services.policy_curve_run,False),
                timeout=max(180,ctx.timeout_seconds),
            )
            ctx.clear_error("POLICY_CURVE_POSTCLOSE")
        except Exception as curve_exc:
            ctx.set_error("POLICY_CURVE_POSTCLOSE",curve_exc)
            print(
                "TRIAID_POLICY_CURVE_RECOVERY",
                ctx.market,
                ctx.errors[ctx.error_key("POLICY_CURVE_POSTCLOSE")],
            )
        frozen=await asyncio.wait_for(
            asyncio.to_thread(
                ctx.services.hazard_prospective_freeze,
                latent,
                policy,
            ),
            timeout=max(120,ctx.timeout_seconds),
        )
        resolved=await asyncio.wait_for(
            asyncio.to_thread(ctx.services.hazard_prospective_resolve),
            timeout=max(240,ctx.timeout_seconds),
        )
        risk_warning=await asyncio.wait_for(
            asyncio.to_thread(ctx.services.risk_warning_run,True),
            timeout=max(120,ctx.timeout_seconds),
        )
        risk_control=await asyncio.wait_for(
            asyncio.to_thread(ctx.services.risk_control_run,True),
            timeout=max(120,ctx.timeout_seconds),
        )
        latest={
            "experiment_id":latent.get("experiment_id"),
            "as_of":latent.get("as_of"),
            "current_state":(latent.get("current_state") or {}).get("state_label"),
            "supported_trigger_count":(
                (latent.get("current_state") or {}).get("supported_trigger_count")
            ),
            "policy_curve_snapshot_id":(policy or {}).get("snapshot_id"),
            "policy_curve_usable":(
                ((policy or {}).get("data_quality") or {}).get("term_curve_usable")
            ),
            "prospective_ledger_id":frozen.get("ledger_id"),
            "updated_outcomes":resolved.get("updated_outcomes"),
            "risk_warning_id":risk_warning.get("warning_id"),
            "risk_pressure_index":(
                (risk_warning.get("overall") or {}).get("risk_pressure_index")
            ),
            "risk_band":(risk_warning.get("overall") or {}).get("risk_band"),
            "risk_20d":(
                ((risk_warning.get("horizon_estimates") or {}).get("20") or {})
                .get("risk_pressure_index")
            ),
            "risk_60d":(
                ((risk_warning.get("horizon_estimates") or {}).get("60") or {})
                .get("risk_pressure_index")
            ),
            "risk_120d":(
                ((risk_warning.get("horizon_estimates") or {}).get("120") or {})
                .get("risk_pressure_index")
            ),
            "risk_250d":(
                ((risk_warning.get("horizon_estimates") or {}).get("250") or {})
                .get("risk_pressure_index")
            ),
            "risk_control_experiment_id":risk_control.get("experiment_id"),
            "risk_control_stage":(
                (risk_control.get("risk_control_experiment") or {}).get("stage")
            ),
        }
        state.update({"last_day":ctx.day,"latest":latest})
        ctx.clear_error(job)
        print(
            "TRIAID_HAZARD_RESEARCH_DAILY",
            ctx.market,
            latest.get("experiment_id"),
            latest.get("as_of"),
            latest.get("current_state"),
            latest.get("policy_curve_usable"),
            latest.get("updated_outcomes"),
            latest.get("risk_pressure_index"),
            latest.get("risk_band"),
            latest.get("risk_control_stage"),
        )
        return latest
    except Exception as exc:
        ctx.set_error(job,exc)
        print("TRIAID_HAZARD_PROSPECTIVE_RECOVERY",ctx.market,ctx.errors[ctx.error_key(job)])
        return None


RUNTIME_JOB_REGISTRY=RuntimeJobRegistry()
RUNTIME_JOB_REGISTRY.register(
    "CN_PREOPEN_AUCTION_SHADOW",
    _cn_preopen_auction_shadow,
    stage="PRE_REFRESH",
)
RUNTIME_JOB_REGISTRY.register(
    "LONG_CYCLE_POSTCLOSE",
    _long_cycle_postclose,
    stage="POST_REFRESH",
)
RUNTIME_JOB_REGISTRY.register(
    "CROSS_MARKET_POSTCLOSE",
    _cross_market_postclose,
    stage="POST_REFRESH",
)
RUNTIME_JOB_REGISTRY.register(
    "HAZARD_RESEARCH_POSTCLOSE",
    _hazard_research_postclose,
    stage="POST_REFRESH",
)
