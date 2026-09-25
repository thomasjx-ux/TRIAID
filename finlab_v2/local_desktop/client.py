"""Native desktop window. Closing this window does not stop the backend."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import HOST, PORT, log_dir, ping_proof, session_secret

BASE = f"http://{HOST}:{PORT}"


def is_our_server() -> bool:
    try:
        req = urllib.request.Request(
            BASE + "/desktop/ping",
            headers={"Host": f"{HOST}:{PORT}"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as res:
            if res.status != 200:
                return False
            data = json.loads(res.read(4096))
            return (
                data.get("service") == "TRIAID_FIN_LOCAL"
                and secrets.compare_digest(data.get("proof", ""), ping_proof())
            )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def start_background_server() -> None:
    log_path = log_dir() / "server.log"
    with log_path.open("ab") as logfile:
        kwargs = {
            "cwd": str(Path(__file__).resolve().parent.parent),
            "stdin": subprocess.DEVNULL,
            "stdout": logfile,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(
            [sys.executable, "-m", "local_desktop.server"], **kwargs
        )


def ensure_backend() -> None:
    if is_our_server():
        return
    # If the port is occupied by a different service, do not send it our token.
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        if sock.connect_ex((HOST, PORT)) == 0:
            raise RuntimeError("Desktop port belongs to an unexpected process.")
    start_background_server()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if is_our_server():
            return
        time.sleep(0.3)
    raise RuntimeError(f"Research backend did not start. See {log_dir() / 'server.log'}")


def main() -> int:
    # Required for an existing Microsoft Edge WebView2 runtime on Windows.
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Install desktop dependencies: pip install -r requirements-desktop.txt"
        ) from exc

    ensure_backend()
    token = session_secret()
    url = BASE + "/desktop/bootstrap?token=" + token
    webview.create_window(
        "TRIAID FIN Research Lab",
        url,
        width=1580,
        height=980,
        min_size=(1120, 720),
    )
    if os.name == "nt":
        webview.start(gui="edgechromium", private_mode=True)
    else:
        webview.start(private_mode=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
