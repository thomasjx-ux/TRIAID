from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


APP_NAME = "TRIAID FIN Research Lab"
HOST = "127.0.0.1"


def _user_data_root() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    root = base / "TRIAID-FIN"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _pick_port() -> int:
    configured = (os.environ.get("TRIAID_DESKTOP_PORT") or "").strip()
    if configured:
        port = int(configured)
        if not 1024 <= port <= 65535:
            raise ValueError("TRIAID_DESKTOP_PORT must be between 1024 and 65535")
        return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    data_root = _user_data_root()
    env.setdefault("TRIAID_RUNTIME_PROFILE", "local_desktop")
    env.setdefault("TRIAID_STORAGE_BACKEND", "file")
    env.setdefault("TRIAID_DATA_DIR", str(data_root / "data"))
    env.setdefault("TRIAID_STARTUP_MAINTENANCE", "1")
    env.setdefault("TRIAID_LONG_RESEARCH_BOOTSTRAP", "1")
    env.setdefault("TRIAID_DATA_AUTOMATION", "1")
    env.setdefault("TRIAID_DECISION_AUTOMATION", "1")
    env.setdefault("TRIAID_RELEASE_AUDIT_REQUIRED", "0")
    env.setdefault("TRIAID_RUNTIME_ID", "local-desktop")
    return env


def _wait_for_runtime(base: str, process: subprocess.Popen, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "runtime did not respond"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"TRIAID runtime exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(base + "/health/live", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise RuntimeError(f"TRIAID runtime startup timeout: {last_error}")


def main() -> int:
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Desktop dependency missing. Install desktop_requirements.txt before launching."
        ) from exc

    port = _pick_port()
    base = f"http://{HOST}:{port}"
    root = Path(__file__).resolve().parent
    env = _runtime_env()

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            HOST,
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=str(root),
        env=env,
    )

    try:
        _wait_for_runtime(base, process)
        window = webview.create_window(
            APP_NAME,
            base + "/",
            min_size=(1120, 720),
            width=1440,
            height=980,
            resizable=True,
            text_select=True,
        )
        webview.start(debug=False)
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
