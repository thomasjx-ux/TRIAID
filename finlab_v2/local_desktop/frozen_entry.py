"""Self-contained Windows entrypoint: desktop GUI, background server, supervisor, diagnostics."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


def configure_frozen_paths() -> None:
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS)
        # Preserve the source deployment's relative resource paths without requiring Python.
        sys.path.insert(0, str(root))
        os.chdir(root)
        # A windowed executable has no Windows console; keep diagnostics in the private user profile.
        if sys.stdout is None or sys.stderr is None:
            from local_desktop.config import log_dir
            stream = (log_dir() / "frozen-launch.log").open("a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = stream
            if sys.stderr is None:
                sys.stderr = stream


def main() -> int:
    configure_frozen_paths()
    mode = sys.argv[1] if len(sys.argv) > 1 else "--desktop"
    if mode == "--desktop":
        from local_desktop.client import main as run
        return run()
    if mode == "--server":
        from local_desktop.server import main as run
        return run()
    if mode == "--supervisor":
        from local_desktop.supervisor import main as run
        return run()
    if mode == "--self-test":
        # Offline end-to-end check of the frozen distribution, not a simulated build.
        from local_desktop.integration_smoke import check
        os.environ["TRIAID_LOCAL_TEST_MODE"] = "1"
        os.environ["TRIAID_DATA_AUTOMATION"] = "0"
        os.environ["TRIAID_DECISION_AUTOMATION"] = "0"
        os.environ["TRIAID_CALENDAR_SYNC"] = "0"
        asyncio.run(check())
        from local_desktop.app import UI_ROOT
        assert (UI_ROOT / "shell.html").is_file()
        assert (UI_ROOT / "live.html").is_file()
        import webview  # ensure native UI package was bundled
        print("TRIAID_DESKTOP_FROZEN_SELF_TEST_PASS", flush=True)
        return 0
    raise SystemExit(f"Unknown desktop command: {mode}")


if __name__ == "__main__":
    sys.exit(main())
