from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
FINLAB = ROOT / "finlab_v2"
if str(FINLAB) not in sys.path:
    sys.path.insert(0, str(FINLAB))

from triaid_fin.contracts import AccountProfile, StrategyPoolSpec
from triaid_fin.market_lab import prepare_live_market
from triaid_fin.risk_aware_engine import RiskAwareEvolutionLabEngine

DEFAULT_MARKETS = ("US", "CN", "HK")


def load_config(path: Path) -> dict:
    if not path.exists():
        return {
            "markets": list(DEFAULT_MARKETS),
            "poll_minutes": 30,
            "data_dir": "./standalone_runtime/data",
            "report_dir": "./standalone_runtime/reports",
            "strategy_pool": {},
            "account": {},
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("standalone_config_must_be_json_object")
    return value


def ensure_local_storage(config: dict) -> tuple[Path, Path]:
    data_dir = Path(str(config.get("data_dir") or "./standalone_runtime/data")).expanduser().resolve()
    report_dir = Path(str(config.get("report_dir") or "./standalone_runtime/reports")).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TRIAID_STORAGE_BACKEND"] = "file"
    os.environ["TRIAID_DATA_DIR"] = str(data_dir)
    os.environ["TRIAID_RUNTIME_ROLE"] = "LOCAL"
    os.environ["TRIAID_PERSISTENCE_SCOPE"] = "LOCAL"
    return data_dir, report_dir


def configure_local_profile(engine: RiskAwareEvolutionLabEngine, config: dict) -> dict:
    """Apply standalone customization to the local GLOBAL route only.

    Keeping the identifiers GLOBAL preserves the full market-specific US/CN/HK
    research routes while the storage boundary keeps these choices local.
    """
    markets = [str(x).upper() for x in (config.get("markets") or DEFAULT_MARKETS)]
    pool_cfg = dict(config.get("strategy_pool") or {})
    account_cfg = dict(config.get("account") or {})

    market_strategy_ids = {
        str(market).upper(): [str(x) for x in ids]
        for market, ids in dict(pool_cfg.get("market_strategy_ids") or {}).items()
        if isinstance(ids, list)
    }
    pool = StrategyPoolSpec(
        pool_id="GLOBAL",
        allowed_strategy_ids=[str(x) for x in pool_cfg.get("allowed_strategy_ids", [])],
        denied_strategy_ids=[str(x) for x in pool_cfg.get("denied_strategy_ids", [])],
        market_strategy_ids=market_strategy_ids,
        max_group_size=int(pool_cfg.get("max_group_size") or 10),
        metadata={
            "standalone_local_override": True,
            **dict(pool_cfg.get("metadata") or {}),
        },
    )
    engine.upsert_strategy_pool(pool)

    max_weight = account_cfg.get("max_strategy_weight")
    max_drawdown = account_cfg.get("max_drawdown_constraint")
    capital = account_cfg.get("capital")
    account = AccountProfile(
        account_id="GLOBAL",
        strategy_pool_id="GLOBAL",
        base_currency=str(account_cfg.get("base_currency") or "USD"),
        capital=float(capital) if capital is not None else None,
        allowed_markets=markets,
        risk_budget=float(account_cfg.get("risk_budget") or 1.0),
        max_strategy_weight=float(max_weight) if max_weight is not None else None,
        max_drawdown_constraint=float(max_drawdown) if max_drawdown is not None else None,
        objective="MAXIMIZE_NET_RETURN",
        execution_profile=dict(account_cfg.get("execution_profile") or {}),
        metadata={
            "standalone_local_override": True,
            **dict(account_cfg.get("metadata") or {}),
        },
    )
    engine.upsert_account(account)
    return {
        "markets": markets,
        "strategy_pool": pool.model_dump(mode="json"),
        "account": account.model_dump(mode="json"),
    }


def panel_dates(panel) -> list[str]:
    return [
        datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        for ts in panel.ts
    ]


def prospective_gap(existing_run, prepared: dict) -> dict:
    latest = str(prepared["latest_as_of"])
    previous = str(prepared["previous_as_of"])
    if existing_run is None:
        return {
            "status": "NO_PRIOR_LOCAL_RUN",
            "missed_complete_bars": 0,
            "latest_complete_bar": latest,
            "previous_complete_bar": previous,
        }
    existing_date = str(existing_run.market.as_of)
    dates = panel_dates(prepared["panel"])
    if existing_date not in dates:
        return {
            "status": "PRIOR_RUN_OUTSIDE_CURRENT_PANEL",
            "missed_complete_bars": None,
            "existing_run_date": existing_date,
            "latest_complete_bar": latest,
        }
    start = dates.index(existing_date)
    end = dates.index(latest)
    missed = max(0, end - start - 1)
    return {
        "status": "GAP_DETECTED" if missed else "CONTIGUOUS",
        "missed_complete_bars": missed,
        "existing_run_date": existing_date,
        "latest_complete_bar": latest,
        "discipline": (
            "MISSED PROSPECTIVE DECISIONS ARE NOT RECONSTRUCTED RETROSPECTIVELY. "
            "MARKET DATA MAY BE REFRESHED, BUT NEW FREEZES RESUME ONLY FROM CURRENT EVIDENCE."
        ),
    }


def summarize_run(run) -> dict | None:
    if run is None:
        return None
    payload = run.model_dump(mode="json")
    decision = payload.get("triaid_decision") or {}
    return {
        "run_id": payload.get("run_id"),
        "status": payload.get("status"),
        "market": (payload.get("market") or {}).get("market_id"),
        "as_of": (payload.get("market") or {}).get("as_of"),
        "snapshot_id": (payload.get("market") or {}).get("snapshot_id"),
        "regime": (payload.get("market") or {}).get("regime"),
        "weights_after": decision.get("weights_after"),
        "audit_passed": (payload.get("audit") or {}).get("passed"),
        "diagnostic_summary": payload.get("diagnostic_summary"),
    }


def latest_route(engine: RiskAwareEvolutionLabEngine, market: str) -> dict | None:
    if market == "US":
        return engine.latest_us_return_max_decision()
    if market == "HK" and hasattr(engine, "latest_hk_return_max_decision"):
        return engine.latest_hk_return_max_decision()
    if market == "CN":
        return engine.latest_recovery_wave_decision("CN")
    return None


def run_market(engine: RiskAwareEvolutionLabEngine, market: str) -> dict:
    existing = engine.latest_run(market)
    prepared = prepare_live_market(market)
    snapshot = prepared["snapshot"]
    daily_complete = bool((snapshot.metadata or {}).get("daily_bar_complete"))
    gap = prospective_gap(existing, prepared)

    if not daily_complete:
        return {
            "market": market,
            "action": "NO_FREEZE",
            "reason": "LATEST_DAILY_BAR_INCOMPLETE",
            "gap": gap,
            "latest_run": summarize_run(existing),
        }

    same_snapshot = bool(existing and existing.market.snapshot_id == snapshot.snapshot_id)
    healthy_status = bool(existing and existing.status in {"DECISION_READY_AWAITING_OUTCOME", "VERIFIED", "NO_NEW_DATA"})
    if same_snapshot and healthy_status:
        return {
            "market": market,
            "action": "ALREADY_CURRENT",
            "gap": gap,
            "latest_run": summarize_run(existing),
            "route": latest_route(engine, market),
        }

    pending = engine.create_pending_live_run(market, "OFFICIAL_EVIDENCE", account_id="GLOBAL")
    engine.execute_live(pending.run_id, market, "OFFICIAL_EVIDENCE")
    finished = engine.get_run(pending.run_id)
    action = "FAILED" if finished.status == "FAILED" else "EXECUTED"
    return {
        "market": market,
        "action": action,
        "gap": gap,
        "latest_run": summarize_run(finished),
        "route": latest_route(engine, market),
    }


def classify_overall_status(rows: list[dict], storage: dict) -> dict:
    storage_ok = bool(storage.get("integrity_ok"))
    failed = [row for row in rows if row.get("action") == "FAILED"]
    no_freeze = [row for row in rows if row.get("action") == "NO_FREEZE"]
    requested = len(rows)

    if not storage_ok or (requested > 0 and len(failed) == requested):
        status = "FAILED"
    elif failed:
        status = "DEGRADED"
    else:
        status = "HEALTHY"

    return {
        "status": status,
        "storage_integrity_ok": storage_ok,
        "requested_market_count": requested,
        "failed_market_count": len(failed),
        "failed_markets": [str(row.get("market")) for row in failed],
        "no_freeze_market_count": len(no_freeze),
        "no_freeze_markets": [str(row.get("market")) for row in no_freeze],
        "semantics": (
            "NO_FREEZE is not a failure when the latest daily bar is incomplete. "
            "DEGRADED means at least one requested market failed while local storage remains usable."
        ),
    }


def write_report(report_dir: Path, payload: dict) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"TRIAID_FIN_{stamp}.json"
    txt_path = report_dir / f"TRIAID_FIN_{stamp}.txt"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    overall = dict(payload.get("overall") or {})
    lines = [
        f"TRIAID FIN standalone report {payload['generated_at']}",
        f"overall_status={overall.get('status')}",
        f"failed_markets={overall.get('failed_markets')}",
        f"storage_integrity_ok={payload['storage'].get('integrity_ok')}",
        f"recovered_stale_runs={payload['recovery'].get('recovered_count')}",
        f"configured_markets={payload['profile'].get('markets')}",
        "",
    ]
    for row in payload["markets"]:
        run = row.get("latest_run") or {}
        route = row.get("route") or {}
        lines.extend([
            f"[{row['market']}] action={row.get('action')} status={run.get('status')} as_of={run.get('as_of')}",
            f"regime={run.get('regime')} gap={row.get('gap')}",
        ])
        if row["market"] == "US" and route:
            state_break = route.get("state_break") or {}
            exposure = route.get("underlying_exposure_guard") or {}
            lines.append(
                f"state_break={state_break.get('level')} score={state_break.get('score')} "
                f"risk_cap={state_break.get('risk_cap')}"
            )
            lines.append(
                f"asset_top_before={exposure.get('top_asset_weight_before')} "
                f"asset_top_after={exposure.get('top_asset_weight_after')} "
                f"cash_residual={route.get('cash_residual_weight')}"
            )
        lines.append("")
    txt_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, txt_path


def run_once(config: dict) -> dict:
    data_dir, report_dir = ensure_local_storage(config)
    engine = RiskAwareEvolutionLabEngine()

    initial_storage = engine.store.status()
    if not bool(initial_storage.get("integrity_ok")):
        raise RuntimeError(f"standalone_store_integrity_failed:{initial_storage}")

    profile = configure_local_profile(engine, config)
    recovery = engine.recover_stale_runs()
    markets = profile["markets"]
    rows = []
    for market in markets:
        try:
            rows.append(run_market(engine, market))
        except Exception as exc:
            rows.append({
                "market": market,
                "action": "FAILED",
                "error": f"{type(exc).__name__}:{exc}",
                "discipline": "FAIL ONE MARKET WITHOUT CORRUPTING OR BLOCKING THE OTHERS",
                "latest_run": summarize_run(engine.latest_run(market)),
            })

    storage = engine.store.status()
    overall = classify_overall_status(rows, storage)
    payload = {
        "version": "triaid-fin-standalone@0.2.1",
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "research_only": True,
        "broker_execution_enabled": False,
        "data_dir": str(data_dir),
        "report_dir": str(report_dir),
        "profile": profile,
        "recovery": recovery,
        "storage": storage,
        "overall": overall,
        "markets": rows,
    }
    json_path, txt_path = write_report(report_dir, payload)
    payload["report_files"] = [str(json_path), str(txt_path)]
    status_path = report_dir / "latest_status.json"
    status_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path(__file__).with_name("standalone_config.json")))
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    poll_minutes = max(5, int(config.get("poll_minutes") or 30))

    while True:
        payload = run_once(config)
        print(json.dumps({
            "status": payload["overall"]["status"],
            "generated_at": payload["generated_at"],
            "report_files": payload["report_files"],
            "failed_markets": payload["overall"]["failed_markets"],
            "markets": [
                {
                    "market": row.get("market"),
                    "action": row.get("action"),
                    "status": (row.get("latest_run") or {}).get("status"),
                }
                for row in payload["markets"]
            ],
        }, ensure_ascii=False))
        if not args.watch:
            return {
                "HEALTHY": 0,
                "DEGRADED": 2,
                "FAILED": 3,
            }.get(str(payload["overall"]["status"]), 3)
        time.sleep(poll_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
