from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FINLAB = ROOT / "finlab_v2"
for path in (str(HERE), str(FINLAB)):
    if path not in sys.path:
        sys.path.insert(0, path)

import run_triaid_fin as base_runtime
from adaptive_v36 import (
    AdaptiveV36Store,
    MARKET_ADAPTERS,
    VERSION as ADAPTIVE_VERSION,
    score_driver_series,
)
from triaid_fin.market_data import MODE_CONFIGS, get_market_data_hub
from triaid_fin.market_registry import MARKET_REGISTRY
from triaid_fin.trading_calendar import official_session_phase, trading_day_info

VERSION = "triaid-fin-standalone@0.3.0-adaptive-v36"


def _iso_ts(ts: int | float | None) -> str:
    value = float(ts or time.time())
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _fetch_series(market: str, symbol: str, mode: str = "DAILY"):
    mode = mode.upper()
    cfg = MODE_CONFIGS[mode]
    hub = get_market_data_hub()
    providers = hub.registry.providers_for(f"{market}:{mode}")
    errors: list[str] = []
    for provider in providers:
        if not bool(getattr(provider, "configured", True)):
            continue
        try:
            series = provider.fetch_series(
                symbol,
                range_=cfg.range_,
                interval=cfg.interval,
                include_prepost=cfg.include_prepost,
                min_points=(60 if mode == "DAILY" else 2),
            )
            return series, getattr(provider, "version", type(provider).__name__)
        except Exception as exc:
            errors.append(f"{getattr(provider,'version',type(provider).__name__)}:{type(exc).__name__}:{exc}")
    raise RuntimeError(f"driver_series_unavailable:{market}:{symbol}:{mode}:{' | '.join(errors)}")


def _market_driver_policy(config: dict, market: str) -> dict:
    adaptive = dict(config.get("adaptive_v36") or {})
    governance = dict(adaptive.get("driver_governance") or {})
    market_cfg = dict((governance.get("markets") or {}).get(market) or {})
    return {
        "score_weights": dict(governance.get("score_weights") or {}),
        "admission_percentile": float(market_cfg.get("admission_percentile", 0.75)),
        "retention_percentile": float(market_cfg.get("retention_percentile", 0.50)),
        "probation_sessions": int(market_cfg.get("probation_sessions", 3)),
        "exit_reviews": int(market_cfg.get("exit_reviews", 2)),
        "min_average_turnover": float(market_cfg.get("min_average_turnover", 0.0)),
        "candidates": list(market_cfg.get("candidates") or []),
    }


def _live_price_for_symbol(market: str, symbol: str, phase: str, daily_series) -> dict:
    modes: list[str] = []
    if phase == "PREOPEN":
        modes = ["PREOPEN", "REALTIME"]
    elif phase == "OPEN":
        modes = ["REALTIME", "INTRADAY"]
    for mode in modes:
        try:
            series, provider = _fetch_series(market, symbol, mode)
            return {
                "latest_price": float(series.close[-1]),
                "reference_time": _iso_ts(series.ts[-1]),
                "reference_source": f"{provider}:{mode}",
                "price_mode": mode,
            }
        except Exception:
            continue
    return {
        "latest_price": float(daily_series.close[-1]),
        "reference_time": _iso_ts(daily_series.ts[-1]),
        "reference_source": "DAILY_FALLBACK",
        "price_mode": "DAILY",
    }


def collect_driver_observations(config: dict, market: str, phase: str) -> list[dict[str, Any]]:
    policy = _market_driver_policy(config, market)
    candidates = list(policy.get("candidates") or [])
    if not candidates:
        return []
    benchmark = MARKET_REGISTRY.get(market).benchmark
    benchmark_series, benchmark_provider = _fetch_series(market, benchmark, "DAILY")
    rows: list[dict[str, Any]] = []
    for cfg in candidates:
        symbol = str(cfg.get("symbol") or "").strip()
        if not symbol:
            continue
        try:
            daily, provider = _fetch_series(market, symbol, "DAILY")
            scored = score_driver_series(
                symbol=symbol,
                closes=list(daily.close),
                volumes=list(daily.volume),
                benchmark_closes=list(benchmark_series.close),
                index_weight_score=cfg.get("index_weight_score"),
            )
            live = _live_price_for_symbol(market, symbol, phase, daily)
            scored.update(live)
            scored.update({
                "name": str(cfg.get("name") or symbol),
                "sector": str(cfg.get("sector") or "UNKNOWN"),
                "purchase_eligible": bool(cfg.get("purchase_eligible", True)),
                "evidence_anchor": f"{provider}|{benchmark_provider}|{scored.get('reference_time')}",
            })
            min_turnover = float(policy.get("min_average_turnover") or 0.0)
            if min_turnover > 0.0 and math.expm1(float(scored.get("liquidity_raw") or 0.0)) < min_turnover:
                scored["hard_gate_status"] = "FAIL_LIQUIDITY_CAPACITY"
            rows.append(scored)
        except Exception as exc:
            rows.append({
                "symbol": symbol,
                "name": str(cfg.get("name") or symbol),
                "sector": str(cfg.get("sector") or "UNKNOWN"),
                "hard_gate_status": f"FAIL_DATA:{type(exc).__name__}",
                "purchase_eligible": False,
                "liquidity_raw": 0.0,
                "scores": {
                    "index_weight_score": cfg.get("index_weight_score"),
                    "market_impact_score": 0.0,
                    "propagation_score": 0.0,
                    "incremental_info_score": 0.0,
                    "stability_score": 0.0,
                },
                "error": str(exc),
            })
    return rows


def _price_state(market: str, phase: str) -> dict[str, Any] | None:
    benchmark = MARKET_REGISTRY.get(market).benchmark
    daily, daily_provider = _fetch_series(market, benchmark, "DAILY")
    if len(daily.close) < 22:
        return None
    daily_returns = [
        float(daily.close[i] / daily.close[i - 1] - 1.0)
        for i in range(1, len(daily.close))
        if float(daily.close[i - 1]) != 0.0
    ]
    recent = daily_returns[-20:]
    if len(recent) < 5:
        return None
    avg = sum(recent) / len(recent)
    var = sum((x - avg) ** 2 for x in recent) / max(1, len(recent))
    sigma = max(math.sqrt(var), 0.003)
    live = _live_price_for_symbol(market, benchmark, phase, daily)
    current = float(live["latest_price"])
    baseline = float(daily.close[-1])
    change = current / baseline - 1.0 if baseline else 0.0
    z = change / sigma if sigma else 0.0
    if z <= -1.5:
        state = "DOWN_BREAK"
        direction = "DOWN"
    elif z <= -0.5:
        state = "WEAK"
        direction = "DOWN"
    elif z < 0.5:
        state = "RANGE"
        direction = "FLAT"
    elif z < 1.5:
        state = "STRONG"
        direction = "UP"
    else:
        state = "UP_BREAK"
        direction = "UP"
    return {
        "benchmark": benchmark,
        "baseline_price": baseline,
        "price": current,
        "change": change,
        "sigma20": sigma,
        "z": z,
        "state": state,
        "direction": direction,
        "reference_time": live["reference_time"],
        "reference_source": live["reference_source"],
        "price_mode": live["price_mode"],
        "daily_provider": daily_provider,
    }


def _latest_state(store: AdaptiveV36Store, market: str, trading_date: str) -> str | None:
    row = store.conn.execute(
        """SELECT new_price_state FROM adaptive_epochs
           WHERE market=? AND trading_date=? ORDER BY event_time DESC LIMIT 1""",
        (market, trading_date),
    ).fetchone()
    return str(row["new_price_state"]) if row and row["new_price_state"] else None


def _state_direction(state: str | None) -> int:
    if state in {"WEAK", "DOWN_BREAK"}:
        return -1
    if state in {"STRONG", "UP_BREAK"}:
        return 1
    return 0


def _propagation_state(store: AdaptiveV36Store, receiver_market: str, receiver_state: str) -> str:
    receiver_dir = _state_direction(receiver_state)
    if receiver_dir == 0:
        return "ISOLATED"
    directions = [receiver_dir]
    for market in ("CN", "HK", "US"):
        if market == receiver_market:
            continue
        row = store.conn.execute(
            """SELECT new_price_state FROM adaptive_epochs
               WHERE market=? ORDER BY event_time DESC LIMIT 1""",
            (market,),
        ).fetchone()
        if row:
            directions.append(_state_direction(row["new_price_state"]))
    same = sum(1 for x in directions if x == receiver_dir)
    opposite = sum(1 for x in directions if x == -receiver_dir)
    if same >= 3:
        return "SYSTEMIC"
    if same >= 2:
        return "PROPAGATING"
    if opposite >= 1:
        return "DIVERGING"
    return "ISOLATED"


def _review_or_reuse_universe(store: AdaptiveV36Store, config: dict, market: str, trading_date: str, phase: str) -> tuple[dict, list[dict[str, Any]]]:
    binding = store.latest_binding(market, trading_date)
    observations = collect_driver_observations(config, market, phase)
    if binding:
        return {
            "market": market,
            "trading_date": trading_date,
            "universe_version": binding["universe_version"],
            "members": store.market_driver_snapshot(market, trading_date)["rows"],
            "reused": True,
        }, observations
    policy = _market_driver_policy(config, market)
    review = store.review_driver_universe(
        market=market,
        trading_date=trading_date,
        candidates=observations,
        policy=policy,
    )
    return review, observations


def _driver_evidence(store: AdaptiveV36Store, market: str, observations: list[dict[str, Any]], review: dict, observed_at: str) -> dict | None:
    members = {
        str(row.get("symbol")): row
        for row in review.get("members") or []
        if str(row.get("lifecycle_status")) in {"ACTIVE", "WATCH"}
    }
    active = [row for row in observations if row.get("symbol") in members and row.get("one_day_return") is not None]
    if not active:
        return None
    weighted = 0.0
    total_weight = 0.0
    source_times: list[str] = []
    payload_rows = []
    for obs in active:
        member = members[str(obs["symbol"])]
        w = float(member.get("normalized_driver_weight") or 0.0)
        weighted += w * float(obs.get("one_day_return") or 0.0)
        total_weight += w
        if obs.get("reference_time"):
            source_times.append(str(obs["reference_time"]))
        payload_rows.append({
            "symbol": obs["symbol"],
            "status": member.get("lifecycle_status"),
            "driver_weight": w,
            "one_day_return": obs.get("one_day_return"),
            "latest_price": obs.get("latest_price"),
        })
    if total_weight > 1e-12:
        weighted /= total_weight
    direction = "UP" if weighted > 0.001 else ("DOWN" if weighted < -0.001 else "FLAT")
    source_time = max(source_times) if source_times else observed_at
    source_event_id = f"DRIVER|{market}|{review['universe_version']}|{source_time}"
    return store.append_evidence(
        module_id="MARKET_DRIVER_GOVERNANCE",
        market_scope=market,
        evidence_type="DRIVER_STATE",
        source_time=source_time,
        observed_at=observed_at,
        direction=direction,
        strength=min(1.0, abs(weighted) / 0.03),
        confidence=min(1.0, len(active) / max(1, len(members))),
        valid_until=None,
        source_status="VERIFIED",
        source_event_id=source_event_id,
        payload={"weighted_return": weighted, "drivers": payload_rows},
        core_eligible=True,
    )


def _price_evidence_and_epoch(store: AdaptiveV36Store, binding: dict, market: str, trading_date: str, phase: str, observed_at: str) -> dict | None:
    if phase not in {"PREOPEN", "OPEN"}:
        return None
    price = _price_state(market, phase)
    if not price:
        return None
    source_event_id = f"PRICE|{market}|{price['benchmark']}|{price['reference_time']}|{price['state']}"
    evidence = store.append_evidence(
        module_id="PRICE_CORE_LOCAL",
        market_scope=market,
        evidence_type="LOCAL_PRICE_STATE",
        source_time=price["reference_time"],
        observed_at=observed_at,
        direction=price["direction"],
        strength=min(1.0, abs(float(price["z"])) / 2.0),
        confidence=1.0 if price["price_mode"] in {"PREOPEN", "REALTIME", "INTRADAY"} else 0.5,
        valid_until=None,
        source_status="VERIFIED",
        source_event_id=source_event_id,
        payload=price,
        core_eligible=True,
    )
    prior = _latest_state(store, market, trading_date)
    if prior == price["state"]:
        return {"price": price, "evidence": evidence, "epoch": None, "noop": True}
    propagation = _propagation_state(store, market, price["state"])
    certification = (
        "PREOPEN_CERTIFIED_SHADOW"
        if phase == "PREOPEN" and price["price_mode"] in {"PREOPEN", "REALTIME"}
        else ("CERTIFIED_SHADOW" if phase == "OPEN" and price["price_mode"] in {"REALTIME", "INTRADAY"} else "WATCH")
    )
    epoch = store.append_epoch(
        session_policy_id=binding["session_policy_id"],
        market=market,
        trading_date=trading_date,
        epoch_type="PREOPEN" if phase == "PREOPEN" else "INTRADAY",
        event_time=observed_at,
        evidence_event_ids=[evidence["evidence_event_id"]],
        prior_price_state=prior,
        new_price_state=price["state"],
        propagation_state=propagation,
        certification_status=certification,
        certification_score=float(evidence.get("confidence") or 0.0),
        action_status="NOOP",
        price_anchor=float(price["price"]),
        notes="Price transition recorded. Driver evidence may inform certification but cannot directly write action.",
    )
    return {"price": price, "evidence": evidence, "epoch": epoch, "noop": False}


def _brief_objects(review: dict, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol = {str(row.get("symbol")): row for row in observations}
    eligible = []
    for member in review.get("members") or []:
        if str(member.get("lifecycle_status")) not in {"ACTIVE", "WATCH"}:
            continue
        obs = by_symbol.get(str(member.get("symbol")))
        if not obs or obs.get("latest_price") is None or float(obs.get("sigma20") or 0.0) <= 0:
            continue
        ret = float(obs.get("one_day_return") or 0.0)
        sigma = max(float(obs.get("sigma20") or 0.0), 1e-9)
        score = float(member.get("normalized_driver_weight") or 0.0) * abs(ret)
        eligible.append((member, obs, ret, sigma, score))
    risks = sorted([x for x in eligible if x[2] < 0], key=lambda x: -x[4])[:3]
    opportunities = sorted(
        [x for x in eligible if x[2] > 0 and bool(x[0].get("purchase_eligible", 1))],
        key=lambda x: -x[4],
    )[:3]
    objects: list[dict[str, Any]] = []
    for member, obs, ret, sigma, _ in risks:
        step = "REDUCE_1_2" if abs(ret) >= 1.5 * sigma else "REDUCE_1_3"
        objects.append({
            **obs,
            "intent": "REDUCE",
            "position_step": step,
            "object_type": "STOCK",
            "evidence_anchor": member.get("evidence_anchor") or obs.get("evidence_anchor"),
        })
    for member, obs, ret, sigma, _ in opportunities:
        objects.append({
            **obs,
            "intent": "BUILD",
            "position_step": "BUILD_1_3",
            "object_type": "STOCK",
            "evidence_anchor": member.get("evidence_anchor") or obs.get("evidence_anchor"),
        })
    return objects


def _maybe_preopen_brief(store: AdaptiveV36Store, config: dict, binding: dict, market: str, trading_date: str, phase: str, review: dict, observations: list[dict[str, Any]], evidence_ids: list[str], observed_at: str) -> dict | None:
    if phase != "PREOPEN":
        return None
    objects = _brief_objects(review, observations)
    anchor_raw = "|".join(sorted(evidence_ids)) + "|" + review["universe_version"]
    evidence_anchor = hashlib.sha256(anchor_raw.encode("utf-8")).hexdigest()[:20]
    latest = store.conn.execute(
        """SELECT evidence_anchor,brief_id,brief_version,publish_status FROM preopen_briefs
           WHERE market=? AND trading_date=? ORDER BY brief_version DESC LIMIT 1""",
        (market, trading_date),
    ).fetchone()
    if latest and str(latest["evidence_anchor"] or "") == evidence_anchor:
        return {"brief_id": latest["brief_id"], "brief_version": latest["brief_version"], "publish_status": latest["publish_status"], "reused": True}
    risk_count = sum(1 for x in objects if x.get("intent") == "REDUCE")
    opp_count = sum(1 for x in objects if x.get("intent") == "BUILD")
    current_view = f"{market}: {risk_count} risk driver(s), {opp_count} opportunity driver(s) passed current universe rules."
    if risk_count and opp_count:
        action = "Reduce confirmed weak drivers; only build confirmed strong drivers."
    elif risk_count:
        action = "Reduce confirmed weak drivers; do not broaden the cut without market confirmation."
    elif opp_count:
        action = "Build only the confirmed leaders in steps; keep invalidation prices active."
    else:
        action = "No executable single-stock action yet; keep observing verified prices."
    level_rule = dict((config.get("adaptive_v36") or {}).get("action_levels") or {})
    return store.create_preopen_brief(
        session_policy_id=binding["session_policy_id"],
        market=market,
        trading_date=trading_date,
        current_view=current_view,
        one_line_action=action,
        broad_market_guard="Single-stock weakness alone does not authorize a broad-market cut.",
        objects=objects,
        level_rule=level_rule,
        evidence_anchor=evidence_anchor,
        generated_at=observed_at,
    )


def run_adaptive_market(store: AdaptiveV36Store, config: dict, market: str) -> dict[str, Any]:
    phase = official_session_phase(market)
    info = trading_day_info(market)
    if not bool(info.get("calendar_known")):
        return {"market": market, "status": "V2_DISABLED_SHADOW", "reason": "CALENDAR_UNAVAILABLE", "phase": phase}
    if not bool(info.get("is_trading_day")):
        return {"market": market, "status": "CLOSED", "reason": info.get("reason"), "phase": phase}
    trading_date = str(info["date"])
    if not store.activation_probe():
        return {"market": market, "status": "V2_DISABLED_SHADOW", "reason": "LEDGER_WRITE_PROBE_FAILED", "phase": phase}
    review, observations = _review_or_reuse_universe(store, config, market, trading_date, phase)
    binding = store.latest_binding(market, trading_date)
    if binding is None:
        binding = store.bind_session(
            market=market,
            trading_date=trading_date,
            universe_version=review["universe_version"],
            binding_status="PASS",
        )
    observed_at = datetime.now(timezone.utc).isoformat()
    evidence_ids: list[str] = []
    driver_evidence = _driver_evidence(store, market, observations, review, observed_at)
    if driver_evidence:
        evidence_ids.append(driver_evidence["evidence_event_id"])
    price_event = _price_evidence_and_epoch(store, binding, market, trading_date, phase, observed_at)
    if price_event and price_event.get("evidence"):
        evidence_ids.append(price_event["evidence"]["evidence_event_id"])
    brief = _maybe_preopen_brief(
        store,
        config,
        binding,
        market,
        trading_date,
        phase,
        review,
        observations,
        evidence_ids,
        observed_at,
    )
    active = [
        row for row in review.get("members") or []
        if str(row.get("lifecycle_status")) in {"ACTIVE", "WATCH"}
    ]
    return {
        "market": market,
        "status": "V2_LIVE_ALLOWED",
        "phase": phase,
        "trading_date": trading_date,
        "session_policy_id": binding["session_policy_id"],
        "universe_version": review["universe_version"],
        "driver_active_count": len(active),
        "driver_observation_count": len(observations),
        "price_event": price_event,
        "brief": brief,
    }


def run_adaptive_once(config: dict) -> dict[str, Any]:
    data_dir, report_dir = base_runtime.ensure_local_storage(config)
    adaptive_cfg = dict(config.get("adaptive_v36") or {})
    if not bool(adaptive_cfg.get("enabled", True)):
        return {"version": ADAPTIVE_VERSION, "status": "DISABLED_BY_CONFIG", "markets": []}
    store = AdaptiveV36Store(data_dir)
    rows: list[dict[str, Any]] = []
    for market in [str(x).upper() for x in (config.get("markets") or base_runtime.DEFAULT_MARKETS)]:
        try:
            rows.append(run_adaptive_market(store, config, market))
        except Exception as exc:
            rows.append({
                "market": market,
                "status": "V2_DISABLED_SHADOW",
                "reason": f"{type(exc).__name__}:{exc}",
                "core_impact": "NONE",
            })
    payload = {
        "version": ADAPTIVE_VERSION,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "research_only": True,
        "broker_execution_enabled": False,
        "status": "HEALTHY" if all(row.get("status") in {"V2_LIVE_ALLOWED", "CLOSED"} for row in rows) else "DEGRADED_SHADOW",
        "store": store.status(),
        "markets": rows,
    }
    latest = report_dir / "adaptive_v36_latest_status.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (report_dir / "adaptive_v36_runtime.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(HERE / "standalone_config.json"))
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    config = base_runtime.load_config(Path(args.config))
    adaptive_cfg = dict(config.get("adaptive_v36") or {})
    core_interval = max(300, int(config.get("poll_minutes") or 30) * 60)
    active_interval = max(30, int(adaptive_cfg.get("poll_seconds_active") or 60))
    idle_interval = max(active_interval, int(adaptive_cfg.get("poll_seconds_idle") or 300))
    last_core = 0.0
    base_payload: dict | None = None

    while True:
        now = time.monotonic()
        if base_payload is None or now - last_core >= core_interval:
            base_payload = base_runtime.run_once(config)
            last_core = now
        try:
            adaptive_payload = run_adaptive_once(config)
        except Exception as exc:
            adaptive_payload = {
                "version": ADAPTIVE_VERSION,
                "status": "DISABLED_SHADOW",
                "error": f"{type(exc).__name__}:{exc}",
                "core_impact": "NONE",
                "markets": [],
            }
        output = {
            "version": VERSION,
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "core": {
                "status": (base_payload.get("overall") or {}).get("status"),
                "failed_markets": (base_payload.get("overall") or {}).get("failed_markets"),
                "report_files": base_payload.get("report_files"),
            },
            "adaptive": adaptive_payload,
        }
        print(json.dumps(output, ensure_ascii=False))
        if not args.watch:
            return {"HEALTHY": 0, "DEGRADED": 2, "FAILED": 3}.get(
                str((base_payload.get("overall") or {}).get("status")), 3
            )
        active_phase = any(
            row.get("phase") in {"PREOPEN", "OPEN"}
            for row in adaptive_payload.get("markets") or []
        )
        time.sleep(active_interval if active_phase else idle_interval)


if __name__ == "__main__":
    raise SystemExit(main())
