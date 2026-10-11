from __future__ import annotations

from statistics import mean
from typing import Any


VERSION = "market-opportunity-state@0.2.0"

MARKET_PROFILES: dict[str, dict[str, Any]] = {
    "US": {
        "profile_version": "opportunity-profile-us@1",
        "thresholds": (0.20, 0.45, 0.62, 0.80),
        "exposure_by_level": {0: 0.0, 1: 0.0, 2: 0.50, 3: 0.75, 4: 1.00},
        "market_structure_note": "T+0 equity/ETF market; intraday reversal is executable, so normal/high opportunity states can carry more exposure.",
    },
    "CN": {
        "profile_version": "opportunity-profile-cn@1",
        "thresholds": (0.22, 0.50, 0.67, 0.83),
        "exposure_by_level": {0: 0.0, 1: 0.0, 2: 0.40, 3: 0.65, 4: 1.00},
        "market_structure_note": "A-share T+1 and price-limit structure require a more conservative opportunity gate and lower normal/high exposure.",
    },
    "HK": {
        "profile_version": "opportunity-profile-hk@1",
        "thresholds": (0.21, 0.48, 0.65, 0.82),
        "exposure_by_level": {0: 0.0, 1: 0.0, 2: 0.45, 3: 0.70, 4: 1.00},
        "market_structure_note": "HK has T+0 trading but thinner cross-sectional liquidity than the US route, so the normal/high gate remains more conservative.",
    },
}


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def opportunity_profile(market_id: str) -> dict[str, Any]:
    key = str(market_id or "").upper()
    if key not in MARKET_PROFILES:
        raise ValueError(f"unsupported opportunity market:{market_id}")
    profile = MARKET_PROFILES[key]
    return {
        "market_id": key,
        "profile_version": str(profile["profile_version"]),
        "thresholds": tuple(float(x) for x in profile["thresholds"]),
        "exposure_by_level": {int(k): float(v) for k, v in profile["exposure_by_level"].items()},
        "market_structure_note": str(profile["market_structure_note"]),
        "parameter_provenance": "MARKET_SPECIFIC_FIXED_DESIGN_PRIOR_NOT_OUTCOME_RETUNED",
    }


def _regime_level(opportunity_density: float, thresholds: tuple[float, float, float, float]) -> tuple[int, str]:
    density = _clip01(opportunity_density)
    t0, t1, t2, t3 = thresholds
    if density < t0:
        return 0, "FROZEN"
    if density < t1:
        return 1, "LOW_OPPORTUNITY"
    if density < t2:
        return 2, "NORMAL_OPPORTUNITY"
    if density < t3:
        return 3, "HIGH_OPPORTUNITY"
    return 4, "EXTREME_OPPORTUNITY"


def build_opportunity_state(rows: list[dict[str, Any]], market_id: str = "US") -> dict[str, Any]:
    """Build a market-level opportunity gate from H20 evidence only.

    H1 and H5 are excluded by construction. Market-specific thresholds and
    exposure multipliers encode execution structure without retuning against
    posterior outcomes.
    """
    profile = opportunity_profile(market_id)
    thresholds = profile["thresholds"]
    exposure_by_level = profile["exposure_by_level"]

    if not rows:
        return {
            "version": VERSION,
            "market_id": profile["market_id"],
            "profile_version": profile["profile_version"],
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
            "thresholds": list(thresholds),
            "exposure_by_level": exposure_by_level,
            "parameter_provenance": profile["parameter_provenance"],
            "market_structure_note": profile["market_structure_note"],
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

    regime_level, regime = _regime_level(opportunity_density, thresholds)
    trade_enabled = bool(positive_rows) and regime_level >= 2
    exposure_multiplier = float(exposure_by_level[regime_level])

    if not positive_rows:
        gate_reason = "NO_POSITIVE_H20_EDGE"
    elif not trade_enabled:
        gate_reason = "POSITIVE_EDGE_EXISTS_BUT_MARKET_OPPORTUNITY_DENSITY_IS_TOO_LOW"
    else:
        gate_reason = "OPPORTUNITY_GATE_OPEN"

    return {
        "version": VERSION,
        "market_id": profile["market_id"],
        "profile_version": profile["profile_version"],
        "opportunity_density": float(opportunity_density),
        "regime_level": int(regime_level),
        "regime": regime,
        "trade_enabled": trade_enabled,
        "exposure_multiplier": exposure_multiplier,
        "positive_edge_breadth": float(positive_breadth),
        "positive_edge_count": len(positive_rows),
        "admissible_count": len(rows),
        "edge_quality": float(edge_quality),
        "history_coverage": float(history_coverage),
        "persistence_quality": float(persistence_quality),
        "gate_reason": gate_reason,
        "horizon_isolation": "H20_ONLY",
        "thresholds": list(thresholds),
        "exposure_by_level": exposure_by_level,
        "parameter_provenance": profile["parameter_provenance"],
        "market_structure_note": profile["market_structure_note"],
        "weights": {
            "positive_edge_breadth": 0.55,
            "edge_quality": 0.20,
            "history_coverage": 0.15,
            "persistence_quality": 0.10,
        },
    }
