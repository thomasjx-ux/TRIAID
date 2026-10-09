from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from triaid_fin.engine import EvolutionLabEngine
from triaid_fin.market_lab import prepare_live_market
from triaid_fin.us_route_guard import StateAwareUSReturnMaxRoute


VERSION = "triaid-fin-standalone-runtime@0.2.0"
DEFAULT_MARKETS = ("US", "CN", "HK")
VALID_COMPLETE_STATUSES = {
    "DECISION_READY_AWAITING_OUTCOME",
    "VERIFIED",
}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _parse_markets(value: str) -> tuple[str, ...]:
    if str(value).strip().lower() == "all":
        return DEFAULT_MARKETS
    rows = tuple(dict.fromkeys(x.strip().upper() for x in str(value).split(",") if x.strip()))
    unsupported = [x for x in rows if x not in DEFAULT_MARKETS]
    if unsupported:
        raise ValueError(f"unsupported_markets:{unsupported}")
    return rows or DEFAULT_MARKETS


def _latest_formal_run(engine: EvolutionLabEngine, market_id: str):
    rows = [
        row
        for row in engine.all_runs()
        if row.market.market_id.upper() == market_id.upper()
        and row.market.snapshot_id != "PENDING"
        and (row.market.metadata or {}).get("evidence_eligible") is not False
        and (row.market.metadata or {}).get("daily_bar_complete") is not False
        and row.status in VALID_COMPLETE_STATUSES
    ]
    return rows[-1] if rows else None


def _render_text(payload: dict) -> str:
    lines = [
        "TRIAID FIN 单机运行摘要",
        f"runtime_version: {payload['runtime_version']}",
        f"generated_at: {payload['generated_at']}",
        f"data_dir: {payload['data_dir']}",
        f"report_dir: {payload['report_dir']}",
        "",
    ]
    for market_id, row in payload["markets"].items():
        lines.extend([
            f"[{market_id}]",
            f"latest_market_date: {row.get('latest_market_date')}",
            f"action: {row.get('action')}",
            f"run_id: {row.get('run_id')}",
            f"run_status: {row.get('run_status')}",
            f"regime: {row.get('regime')}",
            f"state_break_level: {row.get('state_break_level')}",
            f"state_break_score: {row.get('state_break_score')}",
            f"fast_brake_applied: {row.get('fast_brake_applied')}",
            f"effective_risk_budget: {row.get('effective_risk_budget')}",
            f"underlying_exposure_guard: {row.get('underlying_exposure_guard')}",
            "",
        ])
    lines.extend([
        "运行纪律:",
        "1. 单机版与云端共用同一 triaid_fin 核心，不维护第二套算法。",
        "2. 关机期间不伪造历史决策；重新启动后拉取最新市场历史并恢复到当前可验证状态。",
        "3. 所有正式决策仍遵守下一完整可交易周期生效，禁止同一根K线回看污染。",
        "4. 快速状态断裂层只有降风险权限，没有反向或临时追涨杀跌权限。",
        "5. 美股主路线在策略层之后继续检查真实资产与风险簇集中度。",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="TRIAID FIN single-machine canonical runtime")
    parser.add_argument("--data-dir", default="./TRIAID_FIN_LOCAL_DATA")
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--markets", default="all")
    parser.add_argument("--force", action="store_true", help="force a fresh same-day research run")
    args = parser.parse_args()

    markets = _parse_markets(args.markets)
    data_dir = Path(args.data_dir).expanduser().resolve()
    report_dir = (
        Path(args.report_dir).expanduser().resolve()
        if args.report_dir
        else data_dir / "TRIAID_FIN_OUTPUT"
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    backend = (os.getenv("TRIAID_STORAGE_BACKEND", "file").strip().lower() or "file")
    if backend != "file":
        raise RuntimeError(
            "Standalone runtime refuses remote storage. Unset TRIAID_STORAGE_BACKEND or set it to file."
        )
    os.environ["TRIAID_STORAGE_BACKEND"] = "file"
    os.environ["TRIAID_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("TRIAID_RUNTIME_ROLE", "LOCAL_STANDALONE")
    os.environ.setdefault("TRIAID_PERSISTENCE_SCOPE", "LOCAL_ONLY")

    engine = EvolutionLabEngine()
    # Keep the single-machine runtime on the same state-aware US route used by
    # the independent prospective validator without forking the underlying core.
    engine.us_return_max = StateAwareUSReturnMaxRoute()
    recovery = engine.recover_stale_runs()
    market_results: dict[str, dict] = {}

    for market_id in markets:
        profile = engine.strategy_evolution.active(market_id)
        prepared = prepare_live_market(market_id, profile.window_weights)
        latest_market_date = str(prepared["latest_as_of"])
        previous = _latest_formal_run(engine, market_id)
        previous_date = str(previous.market.as_of) if previous is not None else None

        if (
            not args.force
            and previous is not None
            and previous_date == latest_market_date
            and previous.status in VALID_COMPLETE_STATUSES
        ):
            run = previous
            action = "REUSED_VERIFIED_SAME_DATE"
        else:
            pending = engine.create_pending_live_run(market_id, "OFFICIAL_EVIDENCE")
            engine.execute_live(pending.run_id, market_id, "OFFICIAL_EVIDENCE")
            run = engine.get_run(pending.run_id)
            action = "EXECUTED_CURRENT_FORMAL_STATE"

        diagnostics = dict((run.triaid_decision.diagnostics if run.triaid_decision else {}) or {})
        state_break = dict(diagnostics.get("state_break") or {})
        us_route = None
        if market_id == "US":
            try:
                us_route = engine.latest_us_return_max_decision()
            except Exception:
                us_route = None
        market_results[market_id] = {
            "latest_market_date": latest_market_date,
            "previous_local_formal_date": previous_date,
            "offline_gap_detected": bool(previous_date and previous_date < latest_market_date),
            "offline_gap_policy": (
                "CATCH_UP_TO_CURRENT_STATE_WITHOUT_FABRICATING_MISSED_HISTORICAL_DECISIONS"
            ),
            "action": action,
            "run_id": run.run_id,
            "run_status": run.status,
            "regime": run.market.regime,
            "snapshot_id": run.market.snapshot_id,
            "daily_bar_complete": (run.market.metadata or {}).get("daily_bar_complete"),
            "state_break_level": state_break.get("level"),
            "state_break_score": state_break.get("score"),
            "state_break_reasons": state_break.get("reasons") or [],
            "fast_brake_applied": diagnostics.get("fast_brake_applied"),
            "account_risk_budget": diagnostics.get("account_risk_budget"),
            "effective_risk_budget": diagnostics.get("effective_risk_budget"),
            "underlying_exposure_guard": (
                dict((us_route or {}).get("underlying_exposure_guard") or {})
                if market_id == "US"
                else None
            ),
            "audit_passed": bool(run.audit and run.audit.passed),
        }

    report = engine.daily_report.all_markets(compact=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "runtime_version": VERSION,
        "generated_at": generated_at,
        "data_dir": str(data_dir),
        "report_dir": str(report_dir),
        "storage_status": engine.store.status(),
        "stale_run_recovery": recovery,
        "markets": market_results,
        "daily_report": report,
        "research_only": True,
        "broker_execution_enabled": False,
    }

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    text = _render_text(payload)
    _atomic_write(report_dir / f"TRIAID_FIN_{stamp}.json", json_text)
    _atomic_write(report_dir / f"TRIAID_FIN_{stamp}.txt", text)
    _atomic_write(report_dir / "latest.json", json_text)
    _atomic_write(report_dir / "latest.txt", text)

    status = {
        "runtime_version": VERSION,
        "generated_at": generated_at,
        "markets": {
            market: {
                "date": row["latest_market_date"],
                "status": row["run_status"],
                "state_break": row["state_break_level"],
                "audit_passed": row["audit_passed"],
            }
            for market, row in market_results.items()
        },
        "report_latest": str(report_dir / "latest.json"),
    }
    _atomic_write(report_dir / "runtime_status.json", json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))

    if not all(row["audit_passed"] for row in market_results.values()):
        return 20
    if not bool((report.get("integrity") or {}).get("passed")):
        return 21
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
