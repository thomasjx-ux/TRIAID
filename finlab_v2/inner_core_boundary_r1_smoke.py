from __future__ import annotations

import ast
import re
from pathlib import Path

from triaid_fin.contracts import BilingualText, MarketSnapshot, StrategyGroup, StrategyState
from triaid_fin.core import TriaidCoreModule
from triaid_fin.kernel_contract import CORE_KERNEL_CONTRACT_VERSION, CoreDecisionPort, CoreParameters
from triaid_fin.kernel_identity import core_decide_logic_fingerprint, kernel_fingerprint, kernel_identity

ROOT = Path(__file__).resolve().parent
EXPECTED_PRE_R1_DECIDE_LOGIC = "e8913dc1c986c02d2fe4551ad24795d7521151401709e9b37a3de45ad386cd18"

INNER = {
    "triaid_constitution.py",
    "triaid_fin/objective.py",
    "triaid_fin/contracts.py",
    "triaid_fin/kernel_contract.py",
    "triaid_fin/core.py",
}
FORBIDDEN_PREFIXES = (
    "triaid_fin.store",
    "triaid_fin.storage_backend",
    "triaid_fin.market_data",
    "triaid_fin.market_lab",
    "triaid_fin.provider",
    "triaid_fin.runtime",
    "local_desktop",
    "app",
)


def _resolved_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = "triaid_fin" if path.parent.name == "triaid_fin" else ""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and package:
                base = package.split(".")
                keep = max(0, len(base) - node.level + 1)
                prefix = ".".join(base[:keep])
                name = ".".join(x for x in (prefix, node.module or "") if x)
            else:
                name = node.module or ""
            out.append(name)
    return out


def main() -> None:
    assert CORE_KERNEL_CONTRACT_VERSION == "triaid-fin-kernel-contract@1.0.0"
    assert CoreParameters.__module__ == "triaid_fin.kernel_contract"

    for rel in INNER:
        path = ROOT / rel
        assert path.is_file(), rel
        for name in _resolved_imports(path):
            assert not any(name == p or name.startswith(p + ".") for p in FORBIDDEN_PREFIXES), (rel, name)

    # R1 is structural: the decision method's AST must remain semantic-identical
    # to the pre-R1 package captured before the extraction.
    assert core_decide_logic_fingerprint() == EXPECTED_PRE_R1_DECIDE_LOGIC
    assert re.fullmatch(r"[0-9a-f]{64}", kernel_fingerprint())
    ident = kernel_identity()
    assert set(ident["files"]) == INNER

    params = CoreParameters(version="r1-smoke", intervention_strength=0.55)
    core = TriaidCoreModule(params)
    assert isinstance(core, CoreDecisionPort)
    market = MarketSnapshot(market_id="TEST", as_of="2026-01-01T00:00:00+00:00", snapshot_id="s1", regime="mixed")
    group = StrategyGroup(
        group_version="g1", config_version="c1", market_id="TEST",
        members=["A", "B", "P28_CASH"],
        weights={"A":0.35,"B":0.35,"P28_CASH":0.30},
        reasons={
            "A":BilingualText(zh="a",en="a"),
            "B":BilingualText(zh="b",en="b"),
            "P28_CASH":BilingualText(zh="c",en="c"),
        },
    )
    states=[
        StrategyState(strategy_id="A", expected_net_return=0.03),
        StrategyState(strategy_id="B", expected_net_return=0.01),
        StrategyState(strategy_id="P28_CASH", expected_net_return=0.0),
    ]
    first = core.decide(market, group, states)
    second = core.decide(market, group, states)
    # decided_at is intentionally time-varying; scientific decision content is deterministic.
    assert first.weights_before == second.weights_before
    assert first.weights_after == second.weights_after
    assert first.diagnostics == second.diagnostics

    print("TRIAID_INNER_CORE_BOUNDARY_R1_PASS", {
        "kernel_fingerprint": ident["kernel_fingerprint"],
        "core_decide_logic_fingerprint": ident["core_decide_logic_fingerprint"],
    })


if __name__ == "__main__":
    main()
