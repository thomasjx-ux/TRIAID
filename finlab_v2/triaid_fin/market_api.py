from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .market_lab import MARKETS


VALID_MARKETS={"US","CN"}
VALID_MODES={"DAILY","INTRADAY","PREOPEN","REALTIME"}


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


def build_market_data_router(engine,automation)->APIRouter:
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

    @router.get("/products")
    def market_data_products_api(market_id:str|None=None)->dict:
        key=_market(market_id) if market_id else None
        return engine.market_data_product_capabilities(key)

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
    def market_data_refresh_api(market_id:str,mode:str)->dict:
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
