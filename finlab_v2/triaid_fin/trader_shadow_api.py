from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .trader_shadow import TraderShadowDecision, TraderShadowOutcome


def _require_admin_token(x_triaid_admin_token: str | None = Header(default=None)) -> None:
    expected=os.getenv("TRIAID_ADMIN_TOKEN","").strip()
    if not expected:
        raise HTTPException(status_code=503,detail="admin mutation disabled: TRIAID_ADMIN_TOKEN not configured")
    if not x_triaid_admin_token or not secrets.compare_digest(x_triaid_admin_token,expected):
        raise HTTPException(status_code=403,detail="admin authorization required")


def build_trader_shadow_router(engine) -> APIRouter:
    router=APIRouter(prefix="/api/trader-shadow",tags=["trader-shadow"])

    @router.get("/status")
    def status() -> dict:
        return engine.trader_shadow_status()

    @router.get("/catalog")
    def catalog(
        trader_id: str = Query(...),
        market_id: str = Query(...),
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.trader_shadow_catalog(trader_id,market_id)
        except (KeyError,ValueError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.post("/decisions")
    def submit_decision(
        submission: TraderShadowDecision,
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.submit_trader_shadow_decision(submission)
        except KeyError as exc:
            raise HTTPException(status_code=404,detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.post("/outcomes")
    def submit_outcome(
        outcome: TraderShadowOutcome,
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.submit_trader_shadow_outcome(outcome)
        except KeyError as exc:
            raise HTTPException(status_code=404,detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.get("/{trader_id}/daily")
    def daily_summary(
        trader_id: str,
        market_id: str = Query(...),
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.trader_shadow_daily_summary(trader_id,market_id)
        except (KeyError,ValueError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.get("/{trader_id}/history")
    def history(
        trader_id: str,
        market_id: str | None = Query(default=None),
        limit: int = Query(default=100,ge=1,le=1000),
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            rows=engine.trader_shadow_history(trader_id,market_id,limit)
            return {"count":len(rows),"rows":rows}
        except (KeyError,ValueError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    return router
