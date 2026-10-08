from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from .kernel_contract import CORE_KERNEL_CONTRACT_VERSION


KERNEL_IDENTITY_VERSION = "triaid-fin-kernel-identity@1.0.0"
KERNEL_FILES = (
    "triaid_constitution.py",
    "triaid_fin/objective.py",
    "triaid_fin/contracts.py",
    "triaid_fin/kernel_contract.py",
    "triaid_fin/core.py",
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def kernel_file_hashes() -> dict[str, str]:
    base = _root()
    out: dict[str, str] = {}
    for name in KERNEL_FILES:
        path = base / name
        out[name] = _sha256(path.read_bytes())
    return out


def kernel_fingerprint() -> str:
    payload = {
        "contract": CORE_KERNEL_CONTRACT_VERSION,
        "files": kernel_file_hashes(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(canonical)


def core_decide_logic_fingerprint() -> str:
    """Hash only the AST of TriaidCoreModule.decide.

    This separates decision-logic identity from harmless packaging/import/comment
    changes around the method.
    """
    tree = ast.parse((_root() / "triaid_fin/core.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TriaidCoreModule":
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == "decide":
                    dumped = ast.dump(member, annotate_fields=True, include_attributes=False)
                    return _sha256(dumped.encode("utf-8"))
    raise RuntimeError("TriaidCoreModule.decide not found")


def kernel_identity() -> dict:
    return {
        "identity_version": KERNEL_IDENTITY_VERSION,
        "contract_version": CORE_KERNEL_CONTRACT_VERSION,
        "kernel_fingerprint": kernel_fingerprint(),
        "core_decide_logic_fingerprint": core_decide_logic_fingerprint(),
        "files": kernel_file_hashes(),
    }
