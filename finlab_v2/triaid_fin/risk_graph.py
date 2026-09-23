from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .market_registry import market_ids, normalize_market_id


@dataclass(frozen=True)
class RiskGraphNode:
    node_id: str
    node_type: str
    label: str
    metadata: dict


@dataclass(frozen=True)
class RiskGraphEdge:
    source: str
    target: str
    relation: str = "OBSERVATIONAL_LINKAGE"
    directed: bool = False
    enabled: bool = True
    metadata: dict | None = None


class RiskGraphRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, RiskGraphNode] = {}
        self._edges: dict[tuple[str, str, str], RiskGraphEdge] = {}

    def register_market(self, market_id: str, *, metadata: dict | None = None) -> RiskGraphNode:
        key = normalize_market_id(market_id)
        node = RiskGraphNode(
            node_id=key,
            node_type="MARKET",
            label=key,
            metadata=dict(metadata or {}),
        )
        self._nodes[key] = node
        return node

    def register_factor(self, factor_id: str, *, label: str | None = None, metadata: dict | None = None) -> RiskGraphNode:
        key = str(factor_id).strip().upper()
        if not key:
            raise ValueError("factor_id cannot be empty")
        node = RiskGraphNode(
            node_id=key,
            node_type="FACTOR",
            label=label or key,
            metadata=dict(metadata or {}),
        )
        self._nodes[key] = node
        return node

    def register_edge(
        self,
        source: str,
        target: str,
        *,
        relation: str = "OBSERVATIONAL_LINKAGE",
        directed: bool = False,
        metadata: dict | None = None,
    ) -> RiskGraphEdge:
        if source == target:
            raise ValueError("risk graph self-edge is not allowed")
        edge = RiskGraphEdge(
            source=source,
            target=target,
            relation=relation,
            directed=directed,
            metadata=dict(metadata or {}),
        )
        key = (source, target, relation) if directed else tuple(sorted((source, target))) + (relation,)
        self._edges[key] = edge
        return edge

    def ensure_registered_markets(self) -> None:
        for market in market_ids():
            if market not in self._nodes:
                self.register_market(market)

    def market_pairs(self, markets: list[str] | tuple[str, ...] | None = None) -> tuple[tuple[str, str], ...]:
        self.ensure_registered_markets()
        rows = tuple(normalize_market_id(x) for x in markets) if markets else market_ids()
        return tuple(combinations(sorted(dict.fromkeys(rows)), 2))

    def snapshot(self) -> dict:
        self.ensure_registered_markets()
        return {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "label": n.label,
                    "metadata": n.metadata,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "relation": e.relation,
                    "directed": e.directed,
                    "enabled": e.enabled,
                    "metadata": e.metadata or {},
                }
                for e in self._edges.values()
            ],
            "candidate_market_pairs": [list(x) for x in self.market_pairs()],
        }


RISK_GRAPH = RiskGraphRegistry()
for _market in market_ids():
    RISK_GRAPH.register_market(_market)
for _a, _b in RISK_GRAPH.market_pairs():
    RISK_GRAPH.register_edge(_a, _b)
