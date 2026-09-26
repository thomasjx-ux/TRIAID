from pathlib import Path

arch=Path("LOCAL_DESKTOP_ARCHITECTURE.md").read_text(encoding="utf-8")
desktop=Path("desktop_app.py").read_text(encoding="utf-8")
requirements=Path("desktop_requirements.txt").read_text(encoding="utf-8")

for needle in (
    "full-function, single-user research workstation",
    "every currently supported market",
    "UI preservation contract",
    "127.0.0.1",
    "Desktop shell never imports TRIAID domain internals",
    "Local-only realtime workspace",
):
    assert needle in arch,needle

for needle in (
    'HOST = "127.0.0.1"',
    '"TRIAID_RUNTIME_PROFILE", "local_desktop"',
    '"TRIAID_STORAGE_BACKEND", "file"',
    '"TRIAID_DATA_AUTOMATION", "1"',
    '"TRIAID_DECISION_AUTOMATION", "1"',
    '"app:app"',
    'base + "/"',
):
    assert needle in desktop,needle

assert "0.0.0.0" not in desktop
assert "Railway" not in desktop
assert "Supabase" not in desktop
assert "pywebview==" in requirements

print("TRIAID_LOCAL_DESKTOP_CONTRACT_SMOKE_PASS")
