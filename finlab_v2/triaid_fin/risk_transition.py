from __future__ import annotations

import math
from statistics import pstdev
from typing import Any

VERSION = "state-break-fast-brake@0.1.0"
EXPOSURE_VERSION = "underlying-exposure-guard@0.1.0"


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _returns(values: list[float]) -> list[float]:
    if not values:
        return []
    out = [0.0]
    for prev, cur in zip(values[:-1], values[1:]):
        out.append((float(cur) / float(prev) - 1.0) if float(prev) else 0.0)
    return out


def _window_return(values: list[float], days: int) -> float:
    if len(values) <= days:
        return 0.0
    base = float(values[-1 - days])
    return float(values[-1]) / base - 1.0 if base else 0.0


def _window_vol(returns: list[float], days: int) -> float:
    sample = [float(x) for x in returns[-days:]]
    return pstdev(sample) if len(sample) > 1 else 0.0


def detect_state_break(panel: Any) -> dict:
    """Detect whether the current completed market state no longer explains fast evidence.

    The detector is deliberately brake-only. It may reduce risk but never flips
    portfolio direction or promotes a new strategy by itself.
    """
    benchmark = str(panel.spec.benchmark)
    close = [float(x) for x in panel.close.get(benchmark, [])]
    if len(close) < 25:
        return {
            "version": VERSION,
            "status": "INSUFFICIENT_HISTORY",
            "score": 0.0,
            "level": "STABLE",
            "risk_cap": 1.0,
            "brake_only": True,
            "direction_change_allowed": False,
            "confirmed_component_count": 0,
            "components": {},
        }

    benchmark_returns = _returns(close)
    r1 = float(benchmark_returns[-1])
    r3 = _window_return(close, 3)
    r5 = _window_return(close, 5)
    vol20 = _window_vol(benchmark_returns, 20)
    vol5 = _window_vol(benchmark_returns, 5)
    vol_ratio = (vol5 / vol20) if vol20 > 1e-12 else 1.0
    sma20 = sum(close[-20:]) / 20.0
    below_sma20 = close[-1] < sma20

    daily_sigma = max(vol20, 0.003)
    shock = _clip((-r1) / max(2.5 * daily_sigma, 0.0075))
    fast_loss = _clip((-r3) / max(2.0 * daily_sigma * math.sqrt(3.0), 0.012))
    volatility_jump = _clip((vol_ratio - 1.0) / 1.0)

    risk_assets = [
        str(asset)
        for asset in getattr(panel.spec, "risk_assets", ())
        if str(asset) in panel.close and len(panel.close[str(asset)]) > 5
    ]
    if not risk_assets:
        risk_assets = [benchmark]
    positive = 0
    observed = 0
    for asset in risk_assets:
        series = [float(x) for x in panel.close.get(asset, [])]
        if len(series) <= 5:
            continue
        observed += 1
        if _window_return(series, 5) > 0.0:
            positive += 1
    positive_breadth = positive / observed if observed else 0.5
    breadth_break = _clip(1.0 - positive_breadth)

    if below_sma20 and r5 < 0.0:
        trend_break = 1.0
    elif below_sma20 or r5 < 0.0:
        trend_break = 0.5
    else:
        trend_break = 0.0

    components = {
        "one_day_shock": shock,
        "three_day_loss": fast_loss,
        "volatility_jump": volatility_jump,
        "breadth_break": breadth_break,
        "short_trend_break": trend_break,
    }
    score = (
        0.30 * shock
        + 0.25 * fast_loss
        + 0.20 * volatility_jump
        + 0.15 * breadth_break
        + 0.10 * trend_break
    )
    confirmations = sum(1 for value in components.values() if float(value) >= 0.50)

    if score >= 0.72 and confirmations >= 3:
        level = "STRONG_BRAKE"
        risk_cap = 0.45
    elif score >= 0.55 and confirmations >= 2:
        level = "BRAKE"
        risk_cap = 0.70
    elif score >= 0.38:
        level = "WATCH"
        risk_cap = 0.85
    else:
        level = "STABLE"
        risk_cap = 1.0

    return {
        "version": VERSION,
        "status": "READY",
        "score": float(score),
        "level": level,
        "risk_cap": float(risk_cap),
        "brake_only": True,
        "direction_change_allowed": False,
        "confirmed_component_count": int(confirmations),
        "components": {k: float(v) for k, v in components.items()},
        "evidence": {
            "benchmark": benchmark,
            "benchmark_return_1d": r1,
            "benchmark_return_3d": r3,
            "benchmark_return_5d": r5,
            "benchmark_vol_5d_daily": vol5,
            "benchmark_vol_20d_daily": vol20,
            "volatility_ratio_5d_to_20d": vol_ratio,
            "benchmark_below_sma20": bool(below_sma20),
            "positive_risk_asset_breadth_5d": positive_breadth,
            "observed_risk_asset_count": observed,
        },
        "semantics": (
            "FAST EVIDENCE MAY REDUCE RISK ONLY; IT MAY NOT FLIP DIRECTION, "
            "PROMOTE A STRATEGY, OR OVERRIDE PROSPECTIVE EVIDENCE DISCIPLINE."
        ),
    }


def apply_fast_brake(strategy_weights: dict[str, float], risk_cap: float) -> tuple[dict[str, float], dict]:
    cap = _clip(risk_cap)
    before = {str(k): max(0.0, float(v)) for k, v in strategy_weights.items()}
    risky = {k: v for k, v in before.items() if k != "P28_CASH"}
    risky_total = sum(risky.values())
    after = dict(before)
    applied = risky_total > cap + 1e-12 and risky_total > 0.0
    if applied:
        scale = cap / risky_total
        for key in risky:
            after[key] = risky[key] * scale
        after["P28_CASH"] = max(0.0, 1.0 - sum(v for k, v in after.items() if k != "P28_CASH"))
    else:
        total = sum(after.values())
        if total < 1.0 - 1e-12:
            after["P28_CASH"] = after.get("P28_CASH", 0.0) + (1.0 - total)
    after = {k: v for k, v in after.items() if v > 1e-12}
    return after, {
        "version": VERSION,
        "risk_cap": cap,
        "applied": applied,
        "risky_weight_before": risky_total,
        "risky_weight_after": sum(v for k, v in after.items() if k != "P28_CASH"),
        "cash_weight_after": after.get("P28_CASH", 0.0),
    }


def _redistribute_with_cap(weights: dict[str, float], cap: float) -> dict[str, float]:
    cap = _clip(cap)
    if cap <= 0.0:
        return {k: 0.0 for k in weights}
    out = {k: 0.0 for k in weights}
    remaining = sum(max(0.0, float(v)) for v in weights.values())
    active = {k for k, v in weights.items() if float(v) > 1e-12}
    raw = {k: max(0.0, float(v)) for k, v in weights.items()}
    while active and remaining > 1e-12:
        total_raw = sum(raw[k] for k in active)
        if total_raw <= 1e-12:
            break
        proposed = {k: remaining * raw[k] / total_raw for k in active}
        hit = [k for k, v in proposed.items() if v > cap + 1e-12]
        if not hit:
            for k, v in proposed.items():
                out[k] += v
            remaining = 0.0
            break
        for k in hit:
            room = max(0.0, cap - out[k])
            out[k] += room
            remaining -= room
            active.remove(k)
    return out


def enforce_asset_concentration(
    asset_weights: dict[str, float],
    *,
    risk_assets: list[str] | tuple[str, ...] | set[str] | None = None,
    max_single_asset_weight: float = 0.60,
    max_risk_cluster_weight: float = 0.90,
) -> tuple[dict[str, float], dict]:
    before = {str(k): max(0.0, float(v)) for k, v in asset_weights.items()}
    total_before = sum(before.values())
    if total_before > 1.0 + 1e-12:
        before = {k: v / total_before for k, v in before.items()}
        total_before = 1.0

    capped = _redistribute_with_cap(before, max_single_asset_weight)
    risky_set = {str(x) for x in (risk_assets or [])}
    cluster_before = sum(before.get(a, 0.0) for a in risky_set) if risky_set else None
    cluster_after = sum(capped.get(a, 0.0) for a in risky_set) if risky_set else None
    cluster_scaled = False
    cluster_cap = _clip(max_risk_cluster_weight)
    if risky_set and cluster_after is not None and cluster_after > cluster_cap + 1e-12:
        scale = cluster_cap / cluster_after
        for asset in risky_set:
            if asset in capped:
                capped[asset] *= scale
        cluster_after = sum(capped.get(a, 0.0) for a in risky_set)
        cluster_scaled = True

    total_after = sum(capped.values())
    cash_after = max(0.0, 1.0 - total_after)
    top_before = max(before.values()) if before else 0.0
    top_after = max(capped.values()) if capped else 0.0
    hhi_before = sum(v * v for v in before.values())
    hhi_after = sum(v * v for v in capped.values())

    return {k: v for k, v in capped.items() if v > 1e-12}, {
        "version": EXPOSURE_VERSION,
        "max_single_asset_weight": _clip(max_single_asset_weight),
        "max_risk_cluster_weight": cluster_cap if risky_set else None,
        "top_asset_weight_before": top_before,
        "top_asset_weight_after": top_after,
        "risk_cluster_weight_before": cluster_before,
        "risk_cluster_weight_after": cluster_after,
        "single_asset_cap_applied": top_after + 1e-12 < top_before,
        "risk_cluster_cap_applied": cluster_scaled,
        "hhi_before": hhi_before,
        "hhi_after": hhi_after,
        "effective_asset_count_before": (1.0 / hhi_before) if hhi_before > 1e-12 else 0.0,
        "effective_asset_count_after": (1.0 / hhi_after) if hhi_after > 1e-12 else 0.0,
        "cash_residual_weight": cash_after,
        "semantics": (
            "STRATEGY DIVERSIFICATION DOES NOT COUNT AS UNDERLYING DIVERSIFICATION. "
            "THE OVERLAY IS A HARD EXECUTION CONSTRAINT, NOT A SECONDARY RETURN OBJECTIVE."
        ),
    }
