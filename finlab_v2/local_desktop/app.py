"""Desktop-only FastAPI composition. The original dashboard and APIs are untouched."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from .config import COOKIE_NAME, HOST, PORT, ping_proof, session_secret, valid_session

if os.environ.get("TRIAID_LOCAL_DESKTOP_MODE") != "1":
    raise RuntimeError("Local desktop routes must never be imported by the cloud runtime.")

from app import app, decision_scheduler, engine, market_automation  # noqa: E402

UI_ROOT = Path(__file__).parent / "ui"
EXPECTED_ORIGIN = f"http://{HOST}:{PORT}"


def _reject(message: str, status: int = 403) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.middleware("http")
async def desktop_security(request: Request, call_next):
    # Host validation also blocks DNS rebinding attacks against loopback servers.
    if request.headers.get("host", "") != f"{HOST}:{PORT}":
        return _reject("LOCAL_HOST_REQUIRED")
    if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
        return _reject("LOCAL_CLIENT_REQUIRED")
    origin = request.headers.get("origin")
    if origin and origin != EXPECTED_ORIGIN:
        return _reject("FOREIGN_ORIGIN_REJECTED")
    if request.headers.get("sec-fetch-site", "") in ("cross-site", "same-site"):
        return _reject("CROSS_SITE_REQUEST_REJECTED")

    path = request.url.path
    if path == "/desktop/ping":
        response = await call_next(request)
    elif path == "/desktop/bootstrap" and request.method == "GET":
        supplied = request.query_params.get("token")
        if not valid_session(supplied):
            return _reject("DESKTOP_SESSION_REQUIRED", 401)
        response = RedirectResponse(url="/desktop/", status_code=303)
        response.set_cookie(
            COOKIE_NAME, session_secret(),
            secure=False, httponly=True, samesite="strict", path="/",
        )
    elif not valid_session(request.cookies.get(COOKIE_NAME)):
        return _reject("DESKTOP_SESSION_REQUIRED", 401)
    else:
        # A single trusted local OS user can operate all legacy admin UI actions.
        # Inject the admin header server-side, never expose it to JavaScript.
        if not request.headers.get("x-triaid-admin-token"):
            admin = os.environ.get("TRIAID_ADMIN_TOKEN", "")
            if admin:
                request.scope["headers"].append((b"x-triaid-admin-token", admin.encode("ascii")))
        response = await call_next(request)

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'; base-uri 'self'"
    return response


@app.get("/desktop/ping", include_in_schema=False)
def desktop_ping() -> dict:
    return {"service": "TRIAID_FIN_LOCAL", "proof": ping_proof(), "version": 1}


@app.get("/desktop/", response_class=HTMLResponse, include_in_schema=False)
def desktop_shell() -> str:
    return (UI_ROOT / "shell.html").read_text(encoding="utf-8")


@app.get("/desktop/live", response_class=HTMLResponse, include_in_schema=False)
def desktop_live() -> str:
    return (UI_ROOT / "live.html").read_text(encoding="utf-8")


@app.get("/desktop/storage-health", include_in_schema=False)
def local_storage_health() -> dict:
    state = engine.store.backend.status()
    return {
        "backend": state.get("backend"),
        "durability": state.get("durability"),
        "root": state.get("root"),
        "local_disk_probe": state.get("local_disk_probe"),
    }


@app.get("/desktop/events", include_in_schema=False)
async def local_research_events():
    """Low-latency invalidation signals; existing UI projection APIs remain authoritative."""
    async def events():
        fingerprint = None
        last_heartbeat = time.monotonic()
        while True:
            refreshes = tuple(sorted(
                (str(k), float(v)) for k, v in market_automation.last_refresh.items()
            ))
            summary = decision_scheduler.status().get("markets") or {}
            decisions = tuple(sorted(
                (str(m), int(row.get("decision_count") or 0))
                for m, row in summary.items()
            ))
            updated = (refreshes, decisions)
            if updated != fingerprint:
                fingerprint = updated
                payload = {
                    "type": "DATA_OR_DECISION_UPDATE",
                    "server_utc": datetime.now(timezone.utc).isoformat(),
                }
                yield "event: update\ndata: " + json.dumps(payload) + "\n\n"
                last_heartbeat = time.monotonic()
            elif time.monotonic() - last_heartbeat >= 15:
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(1.0)
    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
