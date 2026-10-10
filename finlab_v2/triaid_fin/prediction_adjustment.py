from __future__ import annotations

import math
from math import prod, sqrt
from statistics import pstdev
from typing import Any, Iterable

from .contracts import StrategyState


VERSION = "us-prediction-adjustment@0.1.0"
HORIZONS = (1, 5, 20)
TRADING_DAYS = 252.0
SIGMA_CAP_MULTIPLE = 2.0
MIN_DAILY_NOISE_FLOOR = 0.0025


def _compound(values: Iterable[float]) -> float:
    vals = [float(x) for x in values]
    if not vals:
        return 0.0
    return prod(1.0 + x for x in vals) - 1.0


def _annualized_state_to_horizon(value: float, horizon: int) -> float:
    """Convert the legacy annualized state estimate onto the requested horizon scale.

    The source field is a historical state-return estimate, not a calibrated forecast.
    Linear horizon scaling is intentionally simple and avoids inventing compounding
    precision that the source metric does not contain.
    """
    return float(value) * float(horizon) / TRADING_DAYS


def _trailing_daily_vol(recent_returns: list[float], lookback: int = 20) -> float:
    sample = [float(x) for x in recent_returns[-lookback:]]
    return pstdev(sample) if len(sample) > 1 else 0.0


def _amplitude_cap(recent_returns: list[float], horizon: int) -> float:
    sigma = max(_trailing_daily_vol(recent_returns), MIN_DAILY_NOISE_FLOOR)
    return SIGMA_CAP_MULTIPLE * sigma * sqrt(float(horizon))


def _clip(value: float, cap: float) -> float:
    return max(-float(cap), min(float(cap), float(value)))


def _horizon_forecast(state: StrategyState, horizon: int) -> dict:
    recent = [float(x) for x in state.recent_returns]
    anchor = _annualized_state_to_horizon(float(state.expected_net_return), horizon)

    if horizon == 1:
        coverage = 1.0 if recent else 0.0
        momentum = float(recent[-1]) if recent else 0.0
        raw = momentum if recent else anchor
        blend = "H1_LAST_COMPLETE_RETURN_ONLY_WITH_ANCHOR_FALLBACK"
    else:
        sample = recent[-horizon:]
        coverage = min(1.0, len(sample) / float(horizon))
        momentum = _compound(sample) if sample else 0.0
        raw = (anchor + coverage * momentum) / (1.0 + coverage)
        blend = "LEGACY_STATE_ANCHOR_PLUS_SAME_HORIZON_REALIZED_MOMENTUM"

    cap = _amplitude_cap(recent, horizon)
    calibrated = _clip(raw, cap)
    return {
        "horizon_days": int(horizon),
        "legacy_state_anchor_return": float(anchor),
        "same_horizon_realized_momentum": float(momentum),
        "history_coverage": float(coverage),
        "raw_horizon_return": float(raw),
        "amplitude_cap_abs_return": float(cap),
        "calibrated_horizon_return": float(calibrated),
        "cap_applied": bool(abs(raw) > cap + 1e-15),
        "blend_semantics": blend,
    }


def _price_window_return(values: list[float], horizon: int) -> float | None:
    if len(values) <= horizon:
        return None
    base = float(values[-1 - horizon])
    return (float(values[-1]) / base - 1.0) if base else None


def _sign_state(value: float | None) -> str:
    if value is None:
        return "INSUFFICIENT_HISTORY"
    if value > 0.0:
        return "POSITIVE"
    if value < 0.0:
        return "NEGATIVE"
    return "NEUTRAL"


def build_market_horizon_state(panel: Any) -> dict:
    """Return independent sign-only H1/H5/H20 market state diagnostics.

    Each horizon is computed from its own return window. No shorter horizon is
    allowed to rewrite a longer-horizon state. These are state diagnostics, not
    forecast probabilities and not promotion signals.
    """
    benchmark = str(panel.spec.benchmark)
    benchmark_close = [float(x) for x in panel.close.get(benchmark, [])]
    risk_assets = [
        str(asset) for asset in getattr(panel.spec, "risk_assets", ())
        if str(asset) in panel.close
    ] or [benchmark]

    horizons = {}
    for horizon in HORIZONS:
        ret = _price_window_return(benchmark_close, horizon)
        observed = 0
        positive = 0
        for asset in risk_assets:
            asset_ret = _price_window_return([float(x) for x in panel.close.get(asset, [])], horizon)
            if asset_ret is None:
                continue
            observed += 1
            if asset_ret > 0.0:
                positive += 1
        breadth = (positive / observed) if observed else None
        horizons[f"H{horizon}"] = {
            "horizon_days": horizon,
            "benchmark_return": ret,
            "state": _sign_state(ret),
            "positive_risk_asset_breadth": breadth,
            "observed_risk_asset_count": observed,
            "independent_from_other_horizons": True,
        }

    return {
        "version": "market-horizon-state@0.1.0",
        "benchmark": benchmark,
        "horizons": horizons,
        "semantics": (
            "SIGN-ONLY INDEPENDENT HORIZON STATE. H1 CANNOT REWRITE H5 OR H20; "
            "H5 CANNOT REWRITE H20. NOT A FORECAST PROBABILITY OR PROMOTION SIGNAL."
        ),
    }


def _is_admissible(state: StrategyState) -> bool:
    return (
        str(state.lifecycle or "").lower() == "active"
        and bool(state.eligible)
        and not bool(state.hard_failure)
        and bool(state.liquidity_ok)
        and bool(state.capacity_ok)
        and bool(state.risk_ok)
        and bool(state.concentration_ok)
    )


def build_us_prediction_adjustment(
    states: list[StrategyState],
    candidate_selection_scores: list[dict],
    max_strategy_weight: float,
) -> dict:
    """Build an isolated H1/H5/H20 prediction layer for the US internal route.

    H20 is the only horizon allowed to promote or demote strategies. H5 is used
    only as an exact tie-break after H20. H1 is diagnostic-only and can never
    reverse H20 ordering. Immediate switching cost is subtracted in horizon-return
    units rather than annualized and mixed with the legacy state estimate.
    """
    state_map = {str(s.strategy_id): s for s in states}
    cost_map = {
        str(row.get("strategy_id")): max(0.0, float(row.get("meta_switch_cost_fraction") or 0.0))
        for row in candidate_selection_scores
    }
    admissible_ids = [
        sid for sid, state in state_map.items()
        if sid in cost_map and _is_admissible(state)
    ]
    if not admissible_ids:
        return {
            "version": VERSION,
            "status": "NO_ADMISSIBLE_STRATEGIES",
            "target_strategy_weights": {"P28_CASH": 1.0},
            "selected_strategy_id": "P28_CASH",
            "selected_strategy_ids": [],
            "rows": [],
        }

    rows = []
    for sid in admissible_ids:
        state = state_map[sid]
        horizons = {str(h): _horizon_forecast(state, h) for h in HORIZONS}
        switch_cost = float(cost_map.get(sid, 0.0))
        h20_net = float(horizons["20"]["calibrated_horizon_return"]) - switch_cost
        h5_net = float(horizons["5"]["calibrated_horizon_return"]) - switch_cost
        rows.append({
            "strategy_id": sid,
            "switch_cost_fraction": switch_cost,
            "horizons": horizons,
            "h20_net_after_switch_cost": h20_net,
            "h5_net_after_switch_cost": h5_net,
            "h1_used_for_selection": False,
        })

    ranked = sorted(
        rows,
        key=lambda row: (
            -round(float(row["h20_net_after_switch_cost"]), 12),
            -round(float(row["h5_net_after_switch_cost"]), 12),
            float(row["switch_cost_fraction"]),
            str(row["strategy_id"]),
        ),
    )
    for rank, row in enumerate(ranked, start=1):
        row["h20_cross_section_rank"] = rank

    hard_cap = max(1e-12, min(1.0, float(max_strategy_weight or 1.0)))
    weights: dict[str, float] = {}
    remaining = 1.0
    for row in ranked:
        if remaining <= 1e-12:
            break
        if float(row["h20_net_after_switch_cost"]) <= 0.0:
            break
        weight = min(hard_cap, remaining)
        weights[str(row["strategy_id"])] = weight
        remaining -= weight
    if remaining > 1e-12:
        weights["P28_CASH"] = remaining

    selected = [sid for sid, weight in weights.items() if sid != "P28_CASH" and float(weight) > 1e-12]
    return {
        "version": VERSION,
        "status": "READY",
        "primary_horizon_days": 20,
        "secondary_tie_break_horizon_days": 5,
        "diagnostic_only_horizon_days": 1,
        "horizon_isolation": {
            "H1": "DIAGNOSTIC_ONLY_NO_PROMOTION_NO_DIRECTION_OVERRIDE",
            "H5": "EXACT_H20_TIE_BREAK_ONLY",
            "H20": "PRIMARY_PROMOTION_AND_CROSS_SECTIONAL_RANKING",
        },
        "amplitude_calibration": {
            "method": "TWO_SIGMA_TRAILING_DAILY_VOLATILITY_CAP",
            "sigma_multiple": SIGMA_CAP_MULTIPLE,
            "minimum_daily_noise_floor": MIN_DAILY_NOISE_FLOOR,
            "outcome_specific_retuning": False,
        },
        "switch_cost_semantics": "IMMEDIATE_META_SWITCH_COST_SUBTRACTED_DIRECTLY_FROM_HORIZON_RETURN; NOT ANNUALIZED",
        "max_strategy_weight_constraint": hard_cap,
        "selected_strategy_id": selected[0] if selected else "P28_CASH",
        "selected_strategy_ids": selected,
        "target_strategy_weights": weights,
        "rows": ranked,
    }
