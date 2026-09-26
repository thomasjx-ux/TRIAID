"""The desktop keeps the COMPLETE legacy UI and refuses unsafe launch modes."""
from pathlib import Path

root=Path(__file__).resolve().parent
arch=(root/"LOCAL_DESKTOP_ARCHITECTURE.md").read_text(encoding="utf-8")
launcher=(root/"local_desktop/client.py").read_text(encoding="utf-8")
server=(root/"local_desktop/server.py").read_text(encoding="utf-8")
guard=(root/"local_desktop/app.py").read_text(encoding="utf-8")
shell=(root/"local_desktop/ui/shell.html").read_text(encoding="utf-8")
live=(root/"local_desktop/ui/live.html").read_text(encoding="utf-8")
supervisor=(root/"local_desktop/supervisor.py").read_text(encoding="utf-8")
installer=(root/"local_desktop/install.ps1").read_text(encoding="utf-8")
requirements=(root/"requirements-desktop.txt").read_text(encoding="utf-8")

assert "full-function, single-user research workstation" in arch
assert "Desktop shell never imports TRIAID domain internals" in arch
assert "127.0.0.1" in server
assert "host=HOST" in server
assert "workers=1" in server
assert "TRIAID_LOCAL_DESKTOP_MODE" in server
assert "TRIAID_STORAGE_BACKEND" in server
assert "TRIAID_SUPABASE_TOKEN" in server
assert "TRIAID_DEPLOY_REVISION" in server
assert "DESKTOP_SESSION_REQUIRED" in guard
assert "CROSS_SITE_REQUEST_REJECTED" in guard
assert "Content-Security-Policy" in guard
assert '"/desktop/events"' in guard
assert '"/desktop/storage-health"' in guard
assert "private_mode=True" in launcher
assert 'id="classic" src="/"' in shell
assert 'id="live"' in shell
assert 'new EventSource("/desktop/events")' in live
assert "source_latest_ts" in live
assert "local_desktop.server" in supervisor
assert "local_desktop.smoke" in installer
assert "local_desktop.integration_smoke" in installer
assert "pywebview==6.2.1" in requirements
assert "httpx==0.28.1" in requirements
assert not (root/"desktop_app.py").exists(), "Unsafe legacy desktop launcher must be removed"
assert not (root/"desktop_requirements.txt").exists(), "Duplicate desktop requirements must be removed"

print("TRIAID_LOCAL_DESKTOP_CONTRACT_SMOKE_PASS")
