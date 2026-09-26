"""Static packaging contract for a self-contained per-user Windows setup."""
from pathlib import Path

root=Path(__file__).resolve().parent
source=(root/"local_desktop/frozen_entry.py").read_text(encoding="utf-8")
iss=(root/"local_desktop/setup.iss").read_text(encoding="utf-8")
client=(root/"local_desktop/client.py").read_text(encoding="utf-8")
supervisor=(root/"local_desktop/supervisor.py").read_text(encoding="utf-8")
for token in ('"--server"','"--supervisor"','"--self-test"','TRIAID_LOCAL_TEST_MODE'):
    assert token in source, token
assert 'sys._MEIPASS' in source
assert 'PrivilegesRequired=lowest' in iss
assert 'TRIAID-FIN-Desktop.exe' in iss
assert 'HasWebView2' in iss
assert '"--supervisor"' in iss
assert 'AppId=' in iss
assert 'getattr(sys, "frozen", False)' in client
assert 'getattr(sys, "frozen", False)' in supervisor
print("TRIAID_DESKTOP_ONE_CLICK_PACKAGING_CONTRACT_PASS")
