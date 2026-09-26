"""ASGI integration check: original UI, every local API behind auth, no network needed."""
from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import patch

import httpx


async def check() -> None:
    with tempfile.TemporaryDirectory(prefix="triaid-local-integration-") as tmp:
        with patch.dict(os.environ, {"TRIAID_LOCAL_HOME": tmp}):
            from local_desktop.server import configure
            configure()  # MUST run before importing the original app or the local wrapper.
            from local_desktop.app import app
            from local_desktop.config import HOST, PORT, session_secret

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url=f"http://{HOST}:{PORT}",
                follow_redirects=False,
            ) as c:
                ping = await c.get("/desktop/ping")
                assert ping.status_code == 200, ping.text
                assert ping.json()["service"] == "TRIAID_FIN_LOCAL"

                no_cookie = await c.get("/api/status")
                assert no_cookie.status_code == 401, no_cookie.text

                invalid = await c.get("/desktop/bootstrap?token=wrong")
                assert invalid.status_code == 401, invalid.text

                granted = await c.get(
                    "/desktop/bootstrap", params={"token": session_secret()}
                )
                assert granted.status_code == 303, granted.text
                assert "httponly" in granted.headers["set-cookie"].lower()
                assert "samesite=strict" in granted.headers["set-cookie"].lower()

                disk = await c.get("/desktop/storage-health")
                assert disk.status_code == 200, disk.text
                assert disk.json()["backend"] == "file"
                assert disk.json()["local_disk_probe"]["status"] in (
                    "PENDING_SECOND_START", "VERIFIED_AFTER_RESTART",
                )

                original = await c.get("/")
                assert original.status_code == 200, original.text[:200]
                assert "TRIAID" in original.text
                assert "strategyRows" in original.text

                # Fresh local data has no official decision. HTTP 503 must
                # retain the full, structured BLOCKED contract for the UI,
                # rather than fabricate zero-valued returns or allocations.
                first = await c.get("/api/ui/market-page/US?lang=zh")
                assert first.status_code in (200,503), first.text[:220]
                initial = first.json()
                if first.status_code == 503:
                    assert initial["projection_scope"] == "FULL"
                    assert initial["integrity"]["status"] == "BLOCKED"
                    assert isinstance(initial["sections"], dict)
                    assert initial["sections"]["strategies"]["state"] in ("WAITING","ERROR")
                else:
                    assert initial["integrity"]["passed"] is True

                desktop = await c.get("/desktop/")
                assert desktop.status_code == 200, desktop.text[:200]
                assert 'id="classic" src="/"' in desktop.text

                live = await c.get("/desktop/live")
                assert live.status_code == 200, live.text[:200]
                assert "TRIAID 实时研究实验台" in live.text

                for path in (
                    "/api/status",
                    "/api/ui/market-clocks",
                    "/api/market-data/live-indicators/US",
                    "/api/decision-scheduler/status",
                ):
                    res = await c.get(path)
                    assert res.status_code == 200, (path, res.status_code, res.text[:300])

                csrf = await c.post(
                    "/api/live/run/US", headers={"Origin": "https://malicious.example"}
                )
                assert csrf.status_code == 403, csrf.text

                host = await c.get("/api/status", headers={"Host": "malicious.example"})
                assert host.status_code == 403, host.text

                # An existing admin-only action is callable through the desktop
                # session without ever exposing the server-side token to JS.
                admin = await c.post(
                    "/api/market-data/frequency-policy/US/REALTIME/set-level",
                    params={"level": 1, "reason": "local-integration-test"},
                )
                assert admin.status_code == 200, (admin.status_code, admin.text[:300])

            print("PASS: original UI, local desktop UI, live API, session auth, admin injection, CSRF and Host guard")


if __name__ == "__main__":
    asyncio.run(check())
