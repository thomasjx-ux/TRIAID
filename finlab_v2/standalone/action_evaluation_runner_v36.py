from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from action_evaluation_v36 import ActionEvaluationStore

FINAL_STATUSES = {"FINAL_EVALUATED", "CANCELLED_INVALIDATED", "EXPIRED_NO_FILL"}


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fraction(position_step: str) -> float:
    text = str(position_step or "").upper()
    if "1_3" in text:
        return 1.0 / 3.0
    if "1_2" in text:
        return 0.5
    if "2_3" in text:
        return 2.0 / 3.0
    if "CLEAR" in text or "FULL" in text:
        return 1.0
    return 1.0


def _series_points(series) -> list[tuple[datetime, float]]:
    return [
        (datetime.fromtimestamp(int(ts), tz=timezone.utc), float(price))
        for ts, price in zip(series.ts, series.close)
        if price is not None and float(price) > 0
    ]


def _same_event(latest: dict | None, status: str, fill_time: str | None, close_time: str | None) -> bool:
    if not latest:
        return False
    return (
        str(latest.get("evaluation_status")) == status
        and str(latest.get("fill_time") or "") == str(fill_time or "")
        and str(latest.get("close_time") or "") == str(close_time or "")
    )


def evaluate_market_actions(
    *,
    data_dir: Path,
    market: str,
    trading_date: str,
    phase: str,
    series_fetcher: Callable[[str, str, str], Any],
) -> dict[str, Any]:
    """Evaluate immutable action levels from VERIFIED intraday close observations.

    This is deliberately conservative: a fill exists only if a subsequent
    verified observation lands inside the sealed execution range. No synthetic
    crossing or hindsight interpolation is used.
    """
    eval_store = ActionEvaluationStore(data_dir)
    appended: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        plans = eval_store.conn.execute(
            """SELECT * FROM preopen_action_levels
               WHERE market=? AND trading_date=? ORDER BY valid_from,symbol""",
            (market, trading_date),
        ).fetchall()
        for plan_row in plans:
            plan = dict(plan_row)
            action_level_id = str(plan["action_level_id"])
            latest = eval_store.latest(action_level_id)
            if latest and str(latest.get("evaluation_status")) in FINAL_STATUSES:
                continue
            symbol = str(plan["symbol"])
            try:
                fetched = series_fetcher(market, symbol, "INTRADAY")
                series = fetched[0] if isinstance(fetched, tuple) else fetched
                points = _series_points(series)
            except Exception as exc:
                errors.append(f"{symbol}:INTRADAY:{type(exc).__name__}:{exc}")
                continue
            valid_from = _dt(str(plan["valid_from"]))
            valid_until = _dt(str(plan["valid_until"]))
            window = [(ts, price) for ts, price in points if valid_from <= ts <= valid_until]
            intent = str(plan["intent"]).upper()
            low = float(plan["execute_low"])
            high = float(plan["execute_high"])
            invalid = float(plan["invalidation_price"])
            fill: tuple[datetime, float] | None = None
            cancelled: tuple[datetime, float] | None = None
            for ts, price in window:
                invalidated = (
                    price >= invalid if intent in {"REDUCE", "AVOID", "SELL"}
                    else price <= invalid
                )
                executable = low <= price <= high
                if invalidated:
                    cancelled = (ts, price)
                    break
                if executable:
                    fill = (ts, price)
                    break

            if fill is None:
                if cancelled is not None:
                    status = "CANCELLED_INVALIDATED"
                    if not _same_event(latest, status, None, cancelled[0].isoformat()):
                        appended.append(eval_store.append(
                            action_level_id=action_level_id,
                            evaluation_status=status,
                            evaluated_at=datetime.now(timezone.utc).isoformat(),
                            close_price=cancelled[1],
                            close_time=cancelled[0].isoformat(),
                            evidence_anchor=f"VERIFIED_INTRADAY|{symbol}|INVALIDATED",
                            notes="Invalidation observation occurred before a verified fill.",
                        ))
                elif phase == "POSTCLOSE" or datetime.now(timezone.utc) > valid_until:
                    status = "EXPIRED_NO_FILL"
                    if not _same_event(latest, status, None, None):
                        appended.append(eval_store.append(
                            action_level_id=action_level_id,
                            evaluation_status=status,
                            evaluated_at=datetime.now(timezone.utc).isoformat(),
                            evidence_anchor=f"VERIFIED_INTRADAY|{symbol}|NO_FILL",
                            notes="No verified price observation entered the sealed execution range before expiry.",
                        ))
                continue

            fill_time, fill_price = fill
            fill_iso = fill_time.isoformat()
            after_fill = [(ts, price) for ts, price in points if ts >= fill_time]
            if not after_fill:
                continue
            if latest is None or not latest.get("fill_time"):
                if not _same_event(latest, "FILLED", fill_iso, None):
                    appended.append(eval_store.append(
                        action_level_id=action_level_id,
                        evaluation_status="FILLED",
                        evaluated_at=datetime.now(timezone.utc).isoformat(),
                        fill_price=fill_price,
                        fill_time=fill_iso,
                        slippage_pct=abs(fill_price - (low + high) / 2.0) / max(1e-12, (low + high) / 2.0),
                        evidence_anchor=f"VERIFIED_INTRADAY|{symbol}|FILL",
                        notes="First verified post-decision observation inside the sealed execution range.",
                    ))
                latest = eval_store.latest(action_level_id)

            if phase != "POSTCLOSE":
                continue

            close_time, close_price = after_fill[-1]
            returns = [price / fill_price - 1.0 for _, price in after_fill if fill_price > 0]
            mae = min(returns) if returns else 0.0
            mfe = max(returns) if returns else 0.0
            hold_return = close_price / fill_price - 1.0 if fill_price else 0.0
            fraction = _fraction(str(plan.get("position_step") or ""))
            action_value = (
                -hold_return * fraction
                if intent in {"REDUCE", "AVOID", "SELL"}
                else hold_return * fraction
            )
            status = "FINAL_EVALUATED"
            close_iso = close_time.isoformat()
            if not _same_event(eval_store.latest(action_level_id), status, fill_iso, close_iso):
                appended.append(eval_store.append(
                    action_level_id=action_level_id,
                    evaluation_status=status,
                    evaluated_at=datetime.now(timezone.utc).isoformat(),
                    fill_price=fill_price,
                    fill_time=fill_iso,
                    close_price=close_price,
                    close_time=close_iso,
                    max_adverse_excursion=mae,
                    max_favorable_excursion=mfe,
                    action_value_vs_no_action=action_value,
                    slippage_pct=abs(fill_price - (low + high) / 2.0) / max(1e-12, (low + high) / 2.0),
                    evidence_anchor=f"VERIFIED_INTRADAY|{symbol}|FINAL",
                    notes="Final shadow evaluation versus holding/no-action counterfactual for the affected position fraction.",
                ))
        return {
            "market": market,
            "trading_date": trading_date,
            "phase": phase,
            "evaluations_appended": len(appended),
            "events": appended,
            "errors": errors,
        }
    finally:
        eval_store.close()
