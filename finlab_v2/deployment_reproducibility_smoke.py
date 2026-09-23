from __future__ import annotations

import os
from pathlib import Path

# Local/CI reproducibility must not accidentally inherit Railway's injected
# source identity from the outer build environment.
os.environ.pop("RAILWAY_GIT_COMMIT_SHA",None)
os.environ["TRIAID_DEPLOY_REVISION"]="ci-source-revision"
os.environ["TRIAID_V2_REV"]="ci-runtime-revision"
os.environ["TRIAID_RELEASE_AUDIT_REQUIRED"]="0"

import app as app_module

health=app_module.health()
assert health["ok"] is True
identity=health["deployment"]
assert identity["source_revision"]=="ci-source-revision"
assert identity["railway_git_commit_sha"] is None
assert identity["declared_source_revision"]=="ci-source-revision"
assert identity["runtime_revision"]=="ci-runtime-revision"
assert identity["declared_matches_railway"] is True
assert identity["runtime_matches_railway"] is True
assert identity["identity_verified"] is False

# A Railway-provided source SHA must take precedence and expose mismatches
# instead of trusting a manually declared revision.
os.environ["RAILWAY_GIT_COMMIT_SHA"]="ci-railway-revision"
mismatch=app_module.health()["deployment"]
assert mismatch["source_revision"]=="ci-railway-revision"
assert mismatch["declared_source_revision"]=="ci-source-revision"
assert mismatch["declared_matches_railway"] is False
assert mismatch["runtime_matches_railway"] is False
assert mismatch["identity_verified"] is False

# Production identity is verified only when Railway, declared source, and
# runtime revision all identify the same source.
os.environ["TRIAID_DEPLOY_REVISION"]="ci-railway-revision"
os.environ["TRIAID_V2_REV"]="ci-railway-revision"
verified=app_module.health()["deployment"]
assert verified["source_revision"]=="ci-railway-revision"
assert verified["declared_source_revision"]=="ci-railway-revision"
assert verified["runtime_revision"]=="ci-railway-revision"
assert verified["declared_matches_railway"] is True
assert verified["runtime_matches_railway"] is True
assert verified["identity_verified"] is True

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
