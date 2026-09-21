from __future__ import annotations

from math import prod
from typing import Any


FAST_WINDOWS=(1,3,5)


def _compound(values:list[float])->float:
    return prod(1.0+float(x) for x in values)-1.0 if values else 0.0


def _window_return(values:list[float],window:int)->float|None:
    if len(values)<window:
        return None
    return _compound(values[-window:])


def us_fast_challenger(states:list[Any],primary_strategy_id:str)->dict:
    """Shadow-only short-horizon challenger.

    Uses only returns already present in each StrategyState at freeze time.
    It does not modify production weights. The purpose is to measure whether
    a 1/3/5-day lane adds prospective value over the existing 21/63/126/252-day
    selector without hindsight.
    """
    rows=[]
    for state in states:
        if (
            str(getattr(state,"lifecycle","") or "").lower()!="active"
            or not bool(getattr(state,"eligible",False))
            or bool(getattr(state,"hard_failure",False))
            or not bool(getattr(state,"liquidity_ok",False))
            or not bool(getattr(state,"capacity_ok",False))
            or not bool(getattr(state,"risk_ok",False))
            or not bool(getattr(state,"concentration_ok",False))
        ):
            continue
        recent=[float(x) for x in (getattr(state,"recent_returns",None) or [])]
        windows={str(w):_window_return(recent,w) for w in FAST_WINDOWS}
        available=[x for x in windows.values() if x is not None]
        positive=sum(1 for x in available if x>0)
        fast_score=(sum(available)/len(available)) if available else None
        rows.append({
            "strategy_id":str(getattr(state,"strategy_id","")),
            "fast_windows":windows,
            "positive_window_count":positive,
            "available_window_count":len(available),
            "fast_score":fast_score,
            "risk":float(getattr(state,"risk",0.0) or 0.0),
            "uncertainty":float(getattr(state,"uncertainty",0.0) or 0.0),
        })
    eligible=[
        row for row in rows
        if row["available_window_count"]==len(FAST_WINDOWS)
        and row["positive_window_count"]>=2
        and row["fast_score"] is not None
        and row["fast_score"]>0
    ]
    eligible.sort(key=lambda x:(-float(x["fast_score"]),float(x["risk"]),float(x["uncertainty"]),x["strategy_id"]))
    primary=next((x for x in rows if x["strategy_id"]==primary_strategy_id),None)
    challenger=eligible[0] if eligible else None
    incremental=(
        float(challenger["fast_score"])-float(primary["fast_score"])
        if challenger and primary and primary.get("fast_score") is not None else None
    )
    return {
        "version":"us-fast-challenger@0.1.0",
        "shadow_only":True,
        "applied_to_weights":False,
        "windows_days":list(FAST_WINDOWS),
        "confirmation_rule":"AT_LEAST_2_OF_3_FAST_WINDOWS_POSITIVE_AND_POSITIVE_MEAN_COMPOUNDED_RETURN",
        "primary_strategy_id":primary_strategy_id,
        "primary_fast_score":None if not primary else primary.get("fast_score"),
        "challenger_strategy_id":None if not challenger else challenger["strategy_id"],
        "challenger_fast_score":None if not challenger else challenger["fast_score"],
        "challenger_incremental_fast_score":incremental,
        "candidate_rows":rows,
        "promotion_rule":"PROSPECTIVE_ONLY: challenger may replace or receive risk only after independent forward evidence; no same-day hindsight promotion",
    }


def cn_soft_recovery_shadow(first_order:dict[str,dict],regime:str|None)->dict:
    """Shadow-only graduated CN recovery evidence.

    Production RecoveryWaveCore keeps its strict hard gate. This diagnostic
    records whether improving products have partial, but not fully confirmed,
    recovery evidence so opportunity cost can be measured prospectively.
    """
    risk_off=any(t in str(regime or "").lower() for t in ("risk_off","stress","bear","shock","high_vol"))
    rows=[]
    for symbol,state in first_order.items():
        direction=str(state.get("state_direction") or "MIXED")
        horizons=(state.get("analog_forecast") or {}).get("horizons") or {}
        best=None
        for _,h in horizons.items():
            sel_hit=float(h.get("selection_positive_rate_edge") or 0.0)
            val_hit=float(h.get("validation_positive_rate_edge") or 0.0)
            sel_mean=float(h.get("selection_mean_return_edge") or 0.0)
            val_mean=float(h.get("validation_mean_return_edge") or 0.0)
            sel_med=float(h.get("selection_median_forward_return") or 0.0)
            val_med=float(h.get("validation_median_forward_return") or 0.0)
            # Conservative soft evidence: validation receives 2x weight.
            soft=(sel_hit+2*val_hit)/3.0 + 20.0*((sel_mean+2*val_mean)/3.0) + 10.0*((sel_med+2*val_med)/3.0)
            support=sum(1 for x in (sel_hit,val_hit,sel_mean,val_mean,sel_med,val_med) if x>0)
            row={
                "horizon_days":int(h.get("horizon_days") or 0),
                "soft_score":soft,
                "positive_components":support,
                "selection_positive_rate_edge":sel_hit,
                "validation_positive_rate_edge":val_hit,
                "selection_mean_return_edge":sel_mean,
                "validation_mean_return_edge":val_mean,
                "selection_median_forward_return":sel_med,
                "validation_median_forward_return":val_med,
            }
            if best is None or (row["soft_score"],row["positive_components"],-row["horizon_days"])>(best["soft_score"],best["positive_components"],-best["horizon_days"]):
                best=row
        rows.append({
            "symbol":symbol,
            "state_direction":direction,
            "best_partial_evidence":best,
            "shadow_probe_eligible":bool(
                direction in {"IMPROVING","IMPROVING_FAST"}
                and best is not None
                and best["positive_components"]>=4
                and best["soft_score"]>0
            ),
        })
    eligible=[r for r in rows if r["shadow_probe_eligible"]]
    eligible.sort(key=lambda r:(-float(r["best_partial_evidence"]["soft_score"]),r["symbol"]))
    max_shadow_budget=0.03 if risk_off else 0.05
    return {
        "version":"cn-soft-recovery-shadow@0.1.0",
        "shadow_only":True,
        "applied_to_weights":False,
        "max_shadow_probe_budget":max_shadow_budget,
        "eligible_symbols":[r["symbol"] for r in eligible],
        "rows":rows,
        "promotion_rule":"PROSPECTIVE_ONLY: partial evidence can be promoted only after independent forward validation; strict production recovery gate remains authoritative",
    }


def market_algorithm_attribution(benchmark_return:float|None,portfolio_return:float|None)->dict:
    if benchmark_return is None or portfolio_return is None:
        return {
            "benchmark_return":benchmark_return,
            "portfolio_return":portfolio_return,
            "algorithm_excess_return":None,
        }
    return {
        "benchmark_return":float(benchmark_return),
        "portfolio_return":float(portfolio_return),
        "algorithm_excess_return":float(portfolio_return)-float(benchmark_return),
    }
