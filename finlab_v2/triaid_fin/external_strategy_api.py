from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .external_strategy import (
    ExternalStrategyIsolationRequest,
    ExternalStrategyObservation,
    ExternalStrategySpec,
)


def _require_admin_token(x_triaid_admin_token: str | None = Header(default=None)) -> None:
    expected=os.getenv("TRIAID_ADMIN_TOKEN","").strip()
    if not expected:
        raise HTTPException(status_code=503,detail="admin mutation disabled: TRIAID_ADMIN_TOKEN not configured")
    if not x_triaid_admin_token or not secrets.compare_digest(x_triaid_admin_token,expected):
        raise HTTPException(status_code=403,detail="admin authorization required")


def build_external_strategy_router(engine) -> APIRouter:
    router=APIRouter(prefix="/api/external-strategies",tags=["external-strategies"])

    @router.get("/status")
    def status(account_id: str | None = Query(default=None)) -> dict:
        return engine.external_strategy_status(account_id)

    @router.get("/feedback")
    def feedback(
        limit: int = Query(default=100,ge=1,le=1000),
        account_id: str | None = Query(default=None),
    ) -> dict:
        rows=engine.external_strategy_feedback(limit,account_id)
        return {
            "count":len(rows),
            "account_id":account_id,
            "feedback":rows,
        }

    @router.post("/register")
    def register(
        spec: ExternalStrategySpec,
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.register_external_strategy(spec)
        except (KeyError,ValueError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.post("/observe")
    def observe(
        observation: ExternalStrategyObservation,
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.observe_external_strategy(observation)
        except KeyError as exc:
            raise HTTPException(status_code=404,detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.post("/isolation")
    def isolation(
        request: ExternalStrategyIsolationRequest,
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.set_external_strategy_isolation(
                request.strategy_id,
                request.target_state,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404,detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    return router
