from __future__ import annotations

import os
from pathlib import Path

os.environ["TRIAID_DEPLOY_REVISION"]="ci-source-revision"
os.environ["TRIAID_V2_REV"]="ci-runtime-revision"
os.environ["TRIAID_RELEASE_AUDIT_REQUIRED"]="0"

import app as app_module

health=app_module.health()
assert health["ok"] is True
assert health["deployment"]["source_revision"]=="ci-source-revision"
assert health["deployment"]["runtime_revision"]=="ci-runtime-revision"
assert app_module.app.version==app_module.engine.architecture_version.split("@",1)[-1]

requirements=Path("requirements.txt").read_text(encoding="utf-8").splitlines()
assert requirements==[
    "fastapi==0.141.1",
    "uvicorn[standard]==0.53.0",
    "pydantic==2.13.5",
]

infra=Path("INFRASTRUCTURE.md").read_text(encoding="utf-8")
assert "supabase: current production backend" in infra
assert "Railway local volumes are not required for the current production service" in infra

print("TRIAID_DEPLOYMENT_REPRODUCIBILITY_SMOKE_PASS")
