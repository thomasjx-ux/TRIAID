from __future__ import annotations

from statistics import pstdev
from typing import Any


VERSION = "cross-sectional-risk-brake@0.1.0"
INCOMPLETE_DAILY_PHASES = {"OPEN", "BREAK"}
MIN_DAILY_SIGMA = 0.003


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _completed_prices(values: list[float], input_phase: str | None) -> list[float]:
    prices = [float(x) for x in values]
    if prices and str(input_phase or "").upper() in INCOMPLETE_DAILY_PHASES:
        prices = prices[:-1]
    return prices


def _returns(values: list[float]) -> list[float]:
    if len(values) < 2:
        return []
    return [
        (cur / prev - 1.0) if prev else 0.0
        for prev, cur in zip(values[:-1], values[1:])
    ]


def build_cross_sectional_risk_brake(panel: Any, input_phase: str | None = None) -> dict:
    """Detect concentrated risk-asset downside that a broad benchmark may understate.

    The signal is deliberately brake-only. It cannot promote a strategy, create a
    short position, or reverse portfolio direction. It uses only completed price
    observations and fixed prospective thresholds.
    """
    benchmark = str(panel.spec.benchmark)
    benchmark_prices = _completed_prices(panel.close.get(benchmark, []), input_phase)
    benchmark_returns = _returns(benchmark_prices)
    if len(benchmark_returns) < 20:
        return {
            "version": VERSION,
            "status": "INSUFFICIENT_HISTORY",
            "risk_cap": 1.0,
            "brake_only": True,
            "direction_change_allowed": False,
            "observed_risk_asset_count": 0,
            "risk_asset_returns_1d": {},
        }

    benchmark_r1 = float(benchmark_returns[-1])
    vol20 = pstdev(benchmark_returns[-20:]) if len(benchmark_returns[-20:]) > 1 else 0.0
    daily_sigma = max(float(vol20), MIN_DAILY_SIGMA)

    risk_asset_returns: dict[str, float] = {}
    for asset in getattr(panel.spec, "risk_assets", ()):
        symbol = str(asset)
        prices = _completed_prices(panel.close.get(symbol, []), input_phase)
        returns = _returns(prices)
        if returns:
            risk_asset_returns[symbol] = float(returns[-1])

    if len(risk_asset_returns) < 2:
        return {
            "version": VERSION,
            "status": "INSUFFICIENT_CROSS_SECTION",
            "risk_cap": 1.0,
            "brake_only": True,
            "direction_change_allowed": False,
            "benchmark_return_1d": benchmark_r1,
            "benchmark_vol_20d_daily": vol20,
            "observed_risk_asset_count": len(risk_asset_returns),
            "risk_asset_returns_1d": risk_asset_returns,
        }

    values = list(risk_asset_returns.values())
    worst_symbol, worst_return = min(risk_asset_returns.items(), key=lambda item: item[1])
    dispersion = pstdev(values) if len(values) > 1 else 0.0
    downside_gap = max(0.0, benchmark_r1 - float(worst_return))

    downside_gap_score = _clip(downside_gap / max(1.5 * daily_sigma, 0.0075))
    worst_asset_shock_score = _clip((-float(worst_return)) / max(2.5 * daily_sigma, 0.0075))
    dispersion_score = _clip(dispersion / max(1.25 * daily_sigma, 0.0060))

    structural_break = (
        downside_gap_score >= 0.65
        and worst_asset_shock_score >= 0.75
        and dispersion_score >= 0.35
    )
    risk_cap = 0.85 if structural_break else 1.0

    return {
        "version": VERSION,
        "status": "STRUCTURAL_WATCH" if structural_break else "STABLE",
        "risk_cap": float(risk_cap),
        "brake_only": True,
        "direction_change_allowed": False,
        "input_phase": str(input_phase or "").upper() or None,
        "benchmark": benchmark,
        "benchmark_return_1d": benchmark_r1,
        "benchmark_vol_20d_daily": float(vol20),
        "daily_sigma_floor_applied": bool(vol20 < MIN_DAILY_SIGMA),
        "observed_risk_asset_count": len(risk_asset_returns),
        "risk_asset_returns_1d": risk_asset_returns,
        "worst_risk_asset": worst_symbol,
        "worst_risk_asset_return_1d": float(worst_return),
        "cross_sectional_dispersion_1d": float(dispersion),
        "worst_asset_downside_gap_to_benchmark": float(downside_gap),
        "scores": {
            "downside_gap": float(downside_gap_score),
            "worst_asset_shock": float(worst_asset_shock_score),
            "dispersion": float(dispersion_score),
        },
        "fixed_thresholds": {
            "downside_gap_score": 0.65,
            "worst_asset_shock_score": 0.75,
            "dispersion_score": 0.35,
            "structural_watch_risk_cap": 0.85,
        },
        "outcome_specific_retuning": False,
        "semantics": (
            "A CONCENTRATED RISK-ASSET SELLOFF MAY REDUCE RISK BEFORE THE BROAD BENCHMARK FULLY REFLECTS IT. "
            "THIS LAYER IS BRAKE-ONLY AND CANNOT PROMOTE, SHORT, OR REVERSE DIRECTION."
        ),
    }
