from __future__ import annotations

import json
import re
from pathlib import Path

root=Path(__file__).resolve().parent
app=(root/"app.py").read_text(encoding="utf-8")
manifest=json.loads((root/"local_desktop_parity_manifest.json").read_text(encoding="utf-8"))

current_ids=set(re.findall(r'\bid=["\']([^"\']+)["\']',app))
current_routes=set(re.findall(r'@app\.(?:get|post|put|patch|delete)\(["\']([^"\']+)["\']',app))

required_ids=set(manifest["ui_ids"])
required_routes=set(manifest["api_routes"])

missing_ids=sorted(required_ids-current_ids)
missing_routes=sorted(required_routes-current_routes)

assert not missing_ids, f"LOCAL_DESKTOP_UI_PARITY_REGRESSION missing_ids={missing_ids}"
assert not missing_routes, f"LOCAL_DESKTOP_API_PARITY_REGRESSION missing_routes={missing_routes}"

print(
    "TRIAID_LOCAL_DESKTOP_PARITY_SMOKE_PASS",
    json.dumps(
        {
            "required_ui_ids":len(required_ids),
            "current_ui_ids":len(current_ids),
            "required_api_routes":len(required_routes),
            "current_api_routes":len(current_routes),
        },
        separators=(",",":"),
        sort_keys=True,
    ),
)
