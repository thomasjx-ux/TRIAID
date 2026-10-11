from __future__ import annotations

from statistics import mean
from typing import Any


VERSION = "market-opportunity-state@0.1.0"


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _regime_level(opportunity_density: float) -> tuple[int, str]:
    density = _clip01(opportunity_density)
    if density < 0.20:
        return 0, "FROZEN"
    if density < 0.45:
        return 1, "LOW_OPPORTUNITY"
    if density < 0.62:
        return 2, "NORMAL_OPPORTUNITY"
    if density < 0.80:
        return 3, "HIGH_OPPORTUNITY"
    return 4, "EXTREME_OPPORTUNITY"


def build_opportunity_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a market-level opportunity gate from H20 evidence only.

    The gate deliberately excludes H1 and H5 so short-horizon diagnostics cannot
    leak into promotion or market-level exposure. Inputs are the already calibrated
    candidate rows produced by the prediction layer.
    """
    if not rows:
        return {
            "version": VERSION,
            "opportunity_density": 0.0,
            "regime_level": 0,
            "regime": "FROZEN",
            "trade_enabled": False,
            "exposure_multiplier": 0.0,
            "positive_edge_breadth": 0.0,
            "positive_edge_count": 0,
            "admissible_count": 0,
            "edge_quality": 0.0,
            "history_coverage": 0.0,
            "persistence_quality": 0.0,
            "gate_reason": "NO_ADMISSIBLE_STRATEGIES",
            "horizon_isolation": "H20_ONLY",
        }

    positive_rows = [row for row in rows if float(row.get("h20_net_after_switch_cost") or 0.0) > 0.0]
    positive_breadth = len(positive_rows) / float(len(rows))

    edge_quality_values: list[float] = []
    for row in positive_rows:
        h20 = row.get("horizons", {}).get("20", {})
        cap = max(1e-12, float(h20.get("amplitude_cap_abs_return") or 0.0))
        edge = max(0.0, float(row.get("h20_net_after_switch_cost") or 0.0))
        edge_quality_values.append(_clip01(edge / cap))
    edge_quality = mean(edge_quality_values) if edge_quality_values else 0.0

    coverage_values = [
        _clip01(float(row.get("horizons", {}).get("20", {}).get("history_coverage") or 0.0))
        for row in rows
    ]
    history_coverage = mean(coverage_values) if coverage_values else 0.0

    persistence_values = [
        _clip01(float(row.get("persistence_score") or 0.0))
        for row in positive_rows
    ]
    persistence_quality = mean(persistence_values) if persistence_values else 0.0

    if not positive_rows:
        opportunity_density = 0.0
    else:
        opportunity_density = _clip01(
            0.55 * positive_breadth
            + 0.20 * edge_quality
            + 0.15 * history_coverage
            + 0.10 * persistence_quality
        )

    regime_level, regime = _regime_level(opportunity_density)
    trade_enabled = bool(positive_rows) and regime_level >= 2
    exposure_multiplier = {
        0: 0.0,
        1: 0.0,
        2: 0.50,
        3: 0.75,
        4: 1.00,
    }[regime_level]

    if not positive_rows:
        gate_reason = "NO_POSITIVE_H20_EDGE"
    elif not trade_enabled:
        gate_reason = "POSITIVE_EDGE_EXISTS_BUT_MARKET_OPPORTUNITY_DENSITY_IS_TOO_LOW"
    else:
        gate_reason = "OPPORTUNITY_GATE_OPEN"

    return {
        "version": VERSION,
        "opportunity_density": float(opportunity_density),
        "regime_level": int(regime_level),
        "regime": regime,
        "trade_enabled": trade_enabled,
        "exposure_multiplier": float(exposure_multiplier),
        "positive_edge_breadth": float(positive_breadth),
        "positive_edge_count": len(positive_rows),
        "admissible_count": len(rows),
        "edge_quality": float(edge_quality),
        "history_coverage": float(history_coverage),
        "persistence_quality": float(persistence_quality),
        "gate_reason": gate_reason,
        "horizon_isolation": "H20_ONLY",
        "weights": {
            "positive_edge_breadth": 0.55,
            "edge_quality": 0.20,
            "history_coverage": 0.15,
            "persistence_quality": 0.10,
        },
    }
