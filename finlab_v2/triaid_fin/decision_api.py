from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query


VALID_MARKETS={"US","CN","HK"}


def build_decision_router(scheduler)->APIRouter:
    router=APIRouter(prefix="/api/decision-scheduler",tags=["decision-scheduler"])

    @router.get("/status")
    def status()->dict:
        return scheduler.status()

    @router.get("/events")
    def events(
        market_id:str|None=None,
        limit:int=Query(default=200,ge=1,le=5000),
    )->list[dict]:
        key=market_id.upper() if market_id else None
        if key and key not in VALID_MARKETS:
            raise HTTPException(status_code=400,detail="market_id must be US, CN or HK")
        return scheduler.events(key,limit)

    return router
