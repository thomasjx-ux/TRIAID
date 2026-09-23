from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException

from .contracts import AccountProfile, StrategyPoolSpec
from .market_registry import normalize_market_id
from .strategy_registry import strategy_ids_for_market


def _require_admin_token(x_triaid_admin_token: str | None = Header(default=None)) -> None:
    expected=os.getenv("TRIAID_ADMIN_TOKEN","").strip()
    if not expected:
        raise HTTPException(status_code=503,detail="admin mutation disabled: TRIAID_ADMIN_TOKEN not configured")
    if not x_triaid_admin_token or not secrets.compare_digest(x_triaid_admin_token,expected):
        raise HTTPException(status_code=403,detail="admin authorization required")


def build_account_router(engine) -> APIRouter:
    router=APIRouter(prefix="/api/accounts",tags=["accounts"])

    @router.get("/status")
    def account_status() -> dict:
        return engine.account_registry_status()

    @router.get("/{account_id}/strategy-ids/{market_id}")
    def account_strategy_ids_api(account_id: str, market_id: str) -> dict:
        try:
            market=normalize_market_id(market_id)
            ids=strategy_ids_for_market(market,account_id=account_id)
            account=engine.account_registry.get_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404,detail=str(exc)) from exc
        return {
            "account_id":account_id,
            "market_id":market,
            "strategy_pool_id":account.strategy_pool_id,
            "strategy_ids":list(ids),
        }

    @router.post("/{account_id}/live/run/{market_id}", status_code=202)
    def account_live_preview(
        account_id: str,
        market_id: str,
        background_tasks: BackgroundTasks,
    ) -> dict:
        try:
            market=normalize_market_id(market_id)
            run,scheduled,claim_reason=engine.claim_manual_preview_run(market,account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404,detail=str(exc)) from exc
        if scheduled:
            background_tasks.add_task(engine.execute_live,run.run_id,market,"MANUAL_PREVIEW")
        return {
            "run_id":run.run_id,
            "status":run.status,
            "market_id":market,
            "account_id":account_id,
            "strategy_pool_id":run.strategy_pool_id,
            "run_scope":"MANUAL_PREVIEW",
            "evidence_eligible":False,
            "scheduled":scheduled,
            "claim_reason":claim_reason,
        }

    @router.post("/strategy-pools")
    def upsert_strategy_pool(
        pool: StrategyPoolSpec,
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.upsert_strategy_pool(pool)
        except (KeyError,ValueError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.post("/profiles")
    def upsert_account(
        account: AccountProfile,
        _admin: None = Depends(_require_admin_token),
    ) -> dict:
        try:
            return engine.upsert_account(account)
        except (KeyError,ValueError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    return router
