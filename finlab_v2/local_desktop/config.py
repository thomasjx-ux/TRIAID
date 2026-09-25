"""Single-user paths and local-session credentials; never commit generated credentials."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("TRIAID_DESKTOP_PORT", "8765"))
COOKIE_NAME = "triaid_local_session"


def home() -> Path:
    override = os.environ.get("TRIAID_LOCAL_HOME")
    if override:
        root = Path(override).expanduser().resolve()
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "TRIAID_FIN_Local"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "triaid-fin-local"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        root.chmod(0o700)
    return root


def data_dir() -> Path:
    p = home() / "data"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        p.chmod(0o700)
    return p


def log_dir() -> Path:
    p = home() / "logs"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        p.chmod(0o700)
    return p


def session_file() -> Path:
    return home() / ".desktop-session"


def session_secret() -> str:
    """Persist one random token in the logged-in user's protected profile."""
    path = session_file()
    try:
        value = path.read_text(encoding="ascii").strip()
        if len(value) < 48:
            raise RuntimeError("Invalid local session file; revoke it and restart the server.")
        return value
    except FileNotFoundError:
        value = secrets.token_urlsafe(48)
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return session_secret()
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        return value


def admin_secret() -> str:
    """Separate administrator header value; never transmit this to the browser."""
    return hmac.new(session_secret().encode(), b"TRIAID_LOCAL_ADMIN_V1", hashlib.sha256).hexdigest()


def ping_proof() -> str:
    return hmac.new(session_secret().encode(), b"TRIAID_LOCAL_PING_V1", hashlib.sha256).hexdigest()


def valid_session(value: str | None) -> bool:
    return bool(value and secrets.compare_digest(value, session_secret()))
