from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from .market_lab import MARKETS
from .trading_calendar import calendar_status, trading_day_info


VALID_MARKETS={"US","CN"}
VALID_MODES={"DAILY","INTRADAY","PREOPEN","REALTIME"}

def _require_admin_token(x_triaid_admin_token:str|None=Header(default=None))->None:
    expected=os.getenv("TRIAID_ADMIN_TOKEN","").strip()
    if not expected:
        raise HTTPException(status_code=503,detail="admin mutation disabled: TRIAID_ADMIN_TOKEN not configured")
    if not x_triaid_admin_token or not secrets.compare_digest(x_triaid_admin_token,expected):
        raise HTTPException(status_code=403,detail="admin authorization required")


def _market(value:str)->str:
    key=value.upper()
    if key not in VALID_MARKETS:
        raise HTTPException(status_code=400,detail="market_id must be US or CN")
    return key


def _mode(value:str)->str:
    key=value.upper()
    if key not in VALID_MODES:
        raise HTTPException(status_code=400,detail="mode must be DAILY, INTRADAY, PREOPEN or REALTIME")
    return key


def build_market_data_router(engine,automation,calendar_sync=None)->APIRouter:
    router=APIRouter(prefix="/api/market-data",tags=["market-data"])

    @router.get("/status")
    def market_data_status_api()->dict:
        return automation.status()

    @router.get("/capabilities")
    def market_data_capabilities_api(market_id:str|None=None)->dict:
        key=_market(market_id) if market_id else None
        return engine.market_data_capabilities(key)

    @router.get("/providers")
    def market_data_providers_api()->dict:
        return engine.market_data_provider_status()

    @router.get("/live-indicators/{market_id}")
    def market_data_live_indicators_api(market_id:str)->dict:
        key=_market(market_id)
        return automation.live_indicators(key)

    @router.get("/strategy-context/{market_id}")
    def market_data_strategy_context_api(market_id:str)->dict:
        key=_market(market_id)
        try:
            return engine.strategy_market_context(key)
        except Exception as exc:
            raise HTTPException(status_code=503,detail=f"{type(exc).__name__}:{exc}") from exc

    @router.get("/activity/{market_id}")
    def market_data_activity_api(
        market_id:str,
        limit:int=Query(default=80,ge=1,le=500),
    )->dict:
        key=_market(market_id)
        return automation.activity(key,limit)

    @router.get("/products")
    def market_data_products_api(market_id:str|None=None)->dict:
        key=_market(market_id) if market_id else None
        return engine.market_data_product_capabilities(key)

    @router.get("/trading-calendar")
    def trading_calendar_status_api(market_id:str|None=None)->dict:
        key=_market(market_id) if market_id else None
        return calendar_status(key)

    @router.get("/trading-calendar/{market_id}")
    def trading_calendar_day_api(
        market_id:str,
        date:str|None=Query(default=None),
    )->dict:
        key=_market(market_id)
        try:
            return trading_day_info(key,date)
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.get("/trading-calendar-sync")
    def trading_calendar_sync_status_api()->dict:
        if calendar_sync is None:
            return {"enabled":False,"status":"NOT_CONFIGURED"}
        return calendar_sync.status()

    @router.post("/trading-calendar-sync")
    def trading_calendar_sync_now_api(force:bool=Query(default=True),_admin:None=Depends(_require_admin_token))->dict:
        if calendar_sync is None:
            raise HTTPException(status_code=503,detail="calendar sync not configured")
        try:
            return calendar_sync.sync_once(force=force)
        except Exception as exc:
            raise HTTPException(status_code=503,detail=f"{type(exc).__name__}:{exc}") from exc

    @router.post("/automation/tick")
    async def market_data_automation_tick_api(
        market_id:str|None=Query(default=None),
        _admin:None=Depends(_require_admin_token),
    )->dict:
        markets=(_market(market_id),) if market_id else None
        try:
            return await automation.tick_once(markets)
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503,detail=f"{type(exc).__name__}:{exc}") from exc

    @router.get("/frequency-policy")
    def frequency_policy_status_api()->dict:
        return automation.frequency_policy.status()

    @router.post("/frequency-policy/{market_id}/{mode}/set-level")
    def frequency_policy_set_level_api(
        market_id:str,
        mode:str,
        level:int=Query(...,ge=0),
        lock:bool=Query(default=False),
        reason:str=Query(default="manual"),
        _admin:None=Depends(_require_admin_token),
    )->dict:
        key=_market(market_id);freq=_mode(mode)
        return automation.frequency_policy.set_level(key,freq,level,lock=lock,reason=reason)

    @router.post("/frequency-policy/{market_id}/{mode}/set-interval")
    def frequency_policy_set_interval_api(
        market_id:str,
        mode:str,
        seconds:int=Query(...,ge=1),
        lock:bool=Query(default=False),
        reason:str=Query(default="manual"),
        _admin:None=Depends(_require_admin_token),
    )->dict:
        key=_market(market_id);freq=_mode(mode)
        return automation.frequency_policy.set_interval(key,freq,seconds,lock=lock,reason=reason)

    @router.post("/frequency-policy/{market_id}/{mode}/unlock")
    def frequency_policy_unlock_api(market_id:str,mode:str,_admin:None=Depends(_require_admin_token))->dict:
        key=_market(market_id);freq=_mode(mode)
        return automation.frequency_policy.unlock(key,freq)

    @router.post("/frequency-policy/{market_id}/{mode}/evidence")
    def frequency_policy_evidence_api(
        market_id:str,
        mode:str,
        payload:dict=Body(...),
        _admin:None=Depends(_require_admin_token),
    )->dict:
        key=_market(market_id);freq=_mode(mode)
        try:
            return automation.frequency_policy.record_evidence(
                key,
                freq,
                evaluated_samples=int(payload.get("evaluated_samples",0)),
                incremental_net_return=(
                    None if payload.get("incremental_net_return") is None
                    else float(payload.get("incremental_net_return"))
                ),
                incremental_information_gain=(
                    None if payload.get("incremental_information_gain") is None
                    else float(payload.get("incremental_information_gain"))
                ),
                incremental_cost=(
                    None if payload.get("incremental_cost") is None
                    else float(payload.get("incremental_cost"))
                ),
                confidence=(
                    None if payload.get("confidence") is None
                    else float(payload.get("confidence"))
                ),
            )
        except (TypeError,ValueError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.get("/quotes/{market_id}")
    def market_data_quotes_api(
        market_id:str,
        symbols:str|None=Query(default=None),
    )->dict:
        key=_market(market_id)
        requested=[x.strip().upper() for x in (symbols or "").split(",") if x.strip()]
        if not requested:
            requested=list(MARKETS[key].assets)
        try:
            return engine.market_data_latest_quotes(key,requested)
        except Exception as exc:
            raise HTTPException(status_code=503,detail=f"{type(exc).__name__}:{exc}") from exc

    @router.get("/instrument/{market_id}/{symbol}/{mode}")
    def market_data_instrument_api(market_id:str,symbol:str,mode:str)->dict:
        key=_market(market_id);freq=_mode(mode)
        try:
            return engine.market_data_instrument_series(key,symbol.upper(),freq)
        except Exception as exc:
            raise HTTPException(status_code=503,detail=f"{type(exc).__name__}:{exc}") from exc

    @router.get("/observations")
    def market_data_observations_api(
        market_id:str|None=None,
        mode:str|None=None,
        limit:int=Query(default=200,ge=1,le=5000),
    )->list[dict]:
        key=_market(market_id) if market_id else None
        freq=_mode(mode) if mode else None
        return engine.market_observations(key,freq,limit)

    @router.get("/observation-status")
    def market_data_observation_status_api()->dict:
        return engine.market_observation_status()

    @router.get("/transitions")
    def market_data_transitions_api(
        market_id:str|None=None,
        mode:str|None=None,
        limit:int=Query(default=200,ge=1,le=5000),
    )->list[dict]:
        key=_market(market_id) if market_id else None
        freq=_mode(mode) if mode else None
        return engine.market_transitions(key,freq,limit)

    @router.get("/snapshot/{market_id}/{mode}")
    def market_data_snapshot_api(
        market_id:str,
        mode:str,
        refresh:bool=Query(default=False),
    )->dict:
        key=_market(market_id);freq=_mode(mode)
        try:
            return engine.market_data_snapshot(key,freq,refresh)
        except Exception as exc:
            raise HTTPException(status_code=503,detail=f"{type(exc).__name__}:{exc}") from exc

    @router.post("/refresh/{market_id}/{mode}")
    def market_data_refresh_api(market_id:str,mode:str,_admin:None=Depends(_require_admin_token))->dict:
        key=_market(market_id);freq=_mode(mode)
        try:
            result=engine.refresh_market_data(key,freq)
            snapshot=engine.market_data_snapshot(key,freq,False)
            observation=engine.record_market_observation(snapshot)
            automation.record_manual_refresh(key,freq)
            return {**result,"observation":observation}
        except Exception as exc:
            raise HTTPException(status_code=503,detail=f"{type(exc).__name__}:{exc}") from exc

    return router
