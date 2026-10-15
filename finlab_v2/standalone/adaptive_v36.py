from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Iterable
from zoneinfo import ZoneInfo

VERSION = "triaid-fin-desktop-adaptive@3.6.0"
CORE_POLICY_VERSION = "FIN_CORE_V3.6"
EVIDENCE_CONTRACT_VERSION = "STANDARD_EVIDENCE_V1"


@dataclass(frozen=True)
class MarketAdapter:
    market: str
    timezone: str
    regular_open: str
    regular_close: str
    preopen_start: str
    adapter_version: str


MARKET_ADAPTERS: dict[str, MarketAdapter] = {
    "US": MarketAdapter("US", "America/New_York", "09:30", "16:00", "04:00", "US_ADAPTER_V1"),
    "CN": MarketAdapter("CN", "Asia/Shanghai", "09:30", "15:00", "09:15", "CN_ADAPTER_V1"),
    "HK": MarketAdapter("HK", "Asia/Hong_Kong", "09:30", "16:00", "09:00", "HK_ADAPTER_V1"),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | str | None = None) -> str:
    if value is None:
        value = _utc_now()
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return f"{prefix}|{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _returns(values: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(values)):
        prev = float(values[i - 1])
        cur = float(values[i])
        if prev:
            out.append(cur / prev - 1.0)
    return out


def _corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 8:
        return 0.0
    x = [float(v) for v in a[-n:]]
    y = [float(v) for v in b[-n:]]
    mx = mean(x)
    my = mean(y)
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if sx <= 1e-15 or sy <= 1e-15:
        return 0.0
    return sum((x[i] - mx) * (y[i] - my) for i in range(n)) / (sx * sy)


def _sigma(values: list[float], lookback: int = 20) -> float:
    rs = _returns(values)
    if len(rs) < 5:
        return 0.0
    sample = rs[-lookback:]
    return float(pstdev(sample)) if len(sample) > 1 else 0.0


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if abs(hi - lo) <= 1e-15:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def score_driver_series(
    *,
    symbol: str,
    closes: list[float],
    volumes: list[float],
    benchmark_closes: list[float],
    index_weight_score: float | None = None,
) -> dict[str, Any]:
    """Create market-structure features without hindsight rewriting.

    The function uses only the series supplied by the caller at review time.
    index_weight_score is optional because many local providers do not expose
    constituent weights; the composite score renormalizes over known fields.
    """
    n = min(len(closes), len(benchmark_closes))
    if n < 25:
        return {
            "symbol": symbol,
            "hard_gate_status": "FAIL_INSUFFICIENT_HISTORY",
            "scores": {},
            "latest_price": float(closes[-1]) if closes else None,
            "sigma20": _sigma(closes),
            "one_day_return": None,
            "liquidity_raw": 0.0,
        }
    c = [float(x) for x in closes[-min(n, 90):]]
    b = [float(x) for x in benchmark_closes[-min(n, 90):]]
    r = _returns(c)
    br = _returns(b)
    corr0 = _corr(r[-60:], br[-60:])
    lag_corr = _corr(r[-60:-1], br[-59:]) if len(r) >= 20 and len(br) >= 20 else 0.0
    benchmark_auto = _corr(br[-60:-1], br[-59:]) if len(br) >= 20 else 0.0
    drv_vol = pstdev(r[-60:]) if len(r[-60:]) > 1 else 0.0
    bmk_vol = pstdev(br[-60:]) if len(br[-60:]) > 1 else 0.0
    ratio = drv_vol / bmk_vol if bmk_vol > 1e-12 else 0.0
    market_impact = _clamp(abs(corr0) * (ratio / (1.0 + ratio)))
    propagation = _clamp(abs(lag_corr))
    incremental = _clamp(abs(lag_corr) - abs(benchmark_auto))
    half = max(8, min(len(r), len(br), 60) // 2)
    corr_old = _corr(r[-2 * half:-half], br[-2 * half:-half])
    corr_new = _corr(r[-half:], br[-half:])
    stability = _clamp(1.0 - abs(corr_new - corr_old) / 2.0)
    turnover: list[float] = []
    if volumes:
        k = min(len(volumes), len(closes), 20)
        for price, volume in zip(closes[-k:], volumes[-k:]):
            if float(price) > 0 and float(volume) >= 0:
                turnover.append(float(price) * float(volume))
    liquidity_raw = math.log1p(mean(turnover)) if turnover else 0.0
    scores: dict[str, float | None] = {
        "index_weight_score": None if index_weight_score is None else _clamp(index_weight_score),
        "market_impact_score": market_impact,
        "propagation_score": propagation,
        "incremental_info_score": incremental,
        "stability_score": stability,
    }
    return {
        "symbol": symbol,
        "hard_gate_status": "PASS" if float(c[-1]) > 0 else "FAIL_PRICE",
        "scores": scores,
        "latest_price": float(c[-1]),
        "sigma20": _sigma(c),
        "one_day_return": float(c[-1] / c[-2] - 1.0) if c[-2] else 0.0,
        "liquidity_raw": liquidity_raw,
    }


class AdaptiveV36Store:
    """Local append-only sidecar for the V3.6 desktop adaptive architecture.

    It intentionally does not replace the existing FIN engine/store. If this
    sidecar fails, the legacy standalone four-stage-capable research path can
    continue and adaptive features degrade to disabled/shadow.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "adaptive_v36.sqlite3"
        self.conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS activation_probes(
              probe_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_bindings(
              session_policy_id TEXT PRIMARY KEY,
              market TEXT NOT NULL,
              trading_date TEXT NOT NULL,
              core_policy_version TEXT NOT NULL,
              market_adapter_version TEXT NOT NULL,
              evidence_contract_version TEXT NOT NULL,
              universe_version TEXT NOT NULL,
              binding_status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(market,trading_date)
            );
            CREATE TABLE IF NOT EXISTS evidence_events(
              evidence_event_id TEXT PRIMARY KEY,
              module_id TEXT NOT NULL,
              market_scope TEXT NOT NULL,
              evidence_type TEXT NOT NULL,
              source_time TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              direction TEXT,
              strength REAL,
              confidence REAL,
              valid_until TEXT,
              source_status TEXT NOT NULL,
              source_event_id TEXT,
              payload_json TEXT NOT NULL,
              core_eligible INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_source_event
              ON evidence_events(source_event_id) WHERE source_event_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS adaptive_epochs(
              epoch_id TEXT PRIMARY KEY,
              session_policy_id TEXT NOT NULL,
              prediction_id TEXT,
              market TEXT NOT NULL,
              trading_date TEXT NOT NULL,
              epoch_type TEXT NOT NULL,
              event_time TEXT NOT NULL,
              prior_price_state TEXT,
              new_price_state TEXT,
              propagation_state TEXT,
              certification_status TEXT NOT NULL,
              certification_score REAL,
              evidence_event_ids_json TEXT NOT NULL,
              action_status TEXT NOT NULL,
              price_anchor REAL,
              source_status TEXT NOT NULL,
              observation_latency_minutes REAL,
              core_impact TEXT NOT NULL,
              notes TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS driver_universe_registry(
              registry_id TEXT PRIMARY KEY,
              market TEXT NOT NULL,
              trading_date TEXT NOT NULL,
              universe_version TEXT NOT NULL,
              symbol TEXT NOT NULL,
              name TEXT,
              sector TEXT,
              lifecycle_status TEXT NOT NULL,
              hard_gate_status TEXT NOT NULL,
              index_weight_score REAL,
              market_impact_score REAL,
              propagation_score REAL,
              liquidity_score REAL,
              incremental_info_score REAL,
              stability_score REAL,
              composite_influence_score REAL NOT NULL,
              rank_percentile REAL NOT NULL,
              normalized_driver_weight REAL NOT NULL,
              purchase_eligible INTEGER NOT NULL,
              admission_reason TEXT,
              watch_reason TEXT,
              exit_reason TEXT,
              probation_count INTEGER NOT NULL,
              consecutive_below_retention INTEGER NOT NULL,
              reviewed_at TEXT NOT NULL,
              evidence_anchor TEXT,
              UNIQUE(market,trading_date,symbol)
            );
            CREATE TABLE IF NOT EXISTS preopen_briefs(
              brief_id TEXT PRIMARY KEY,
              session_policy_id TEXT NOT NULL,
              market TEXT NOT NULL,
              trading_date TEXT NOT NULL,
              brief_version INTEGER NOT NULL,
              generated_at TEXT NOT NULL,
              current_view TEXT NOT NULL,
              one_line_action TEXT NOT NULL,
              broad_market_guard TEXT,
              publish_status TEXT NOT NULL,
              evidence_anchor TEXT,
              UNIQUE(market,trading_date,brief_version)
            );
            CREATE TABLE IF NOT EXISTS preopen_action_levels(
              action_level_id TEXT PRIMARY KEY,
              brief_id TEXT NOT NULL,
              market TEXT NOT NULL,
              trading_date TEXT NOT NULL,
              object_type TEXT NOT NULL,
              symbol TEXT NOT NULL,
              object_name TEXT,
              intent TEXT NOT NULL,
              position_step TEXT NOT NULL,
              reference_price REAL NOT NULL,
              reference_time TEXT NOT NULL,
              reference_source TEXT NOT NULL,
              trigger_type TEXT NOT NULL,
              trigger_price REAL NOT NULL,
              execute_low REAL NOT NULL,
              execute_high REAL NOT NULL,
              invalidation_price REAL NOT NULL,
              target1_price REAL,
              target2_price REAL,
              max_slippage_pct REAL NOT NULL,
              valid_from TEXT NOT NULL,
              valid_until TEXT NOT NULL,
              price_status TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              evidence_anchor TEXT,
              fill_price REAL,
              fill_time TEXT,
              close_price REAL,
              max_adverse_excursion REAL,
              max_favorable_excursion REAL,
              action_value_vs_no_action REAL,
              evaluation_status TEXT NOT NULL
            );
            """
        )
        for table in (
            "session_bindings",
            "evidence_events",
            "adaptive_epochs",
            "driver_universe_registry",
            "preopen_briefs",
            "preopen_action_levels",
        ):
            self.conn.executescript(
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'append_only:{table}:update'); END;
                CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'append_only:{table}:delete'); END;
                """
            )
        self.conn.commit()

    def activation_probe(self) -> bool:
        probe_id = _stable_id("PROBE", _iso())
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute(
                "INSERT INTO activation_probes(probe_id,created_at) VALUES(?,?)",
                (probe_id, _iso()),
            )
            row = self.conn.execute(
                "SELECT probe_id FROM activation_probes WHERE probe_id=?", (probe_id,)
            ).fetchone()
            self.conn.rollback()
            return bool(row and row["probe_id"] == probe_id)
        except Exception:
            self.conn.rollback()
            return False

    def latest_binding(self, market: str, trading_date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM session_bindings WHERE market=? AND trading_date=?",
            (market, trading_date),
        ).fetchone()
        return dict(row) if row else None

    def bind_session(
        self,
        *,
        market: str,
        trading_date: str,
        universe_version: str,
        binding_status: str = "PASS",
    ) -> dict:
        adapter = MARKET_ADAPTERS[market]
        session_policy_id = f"{market}|{trading_date}|ADAPTIVE_CORE_V2|{universe_version[:12]}"
        existing = self.latest_binding(market, trading_date)
        expected = {
            "session_policy_id": session_policy_id,
            "market": market,
            "trading_date": trading_date,
            "core_policy_version": CORE_POLICY_VERSION,
            "market_adapter_version": adapter.adapter_version,
            "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
            "universe_version": universe_version,
            "binding_status": binding_status,
        }
        if existing:
            for key, value in expected.items():
                if str(existing.get(key)) != str(value):
                    raise RuntimeError(f"session_binding_conflict:{key}:{existing.get(key)}!={value}")
            return existing
        self.conn.execute(
            """INSERT INTO session_bindings(
              session_policy_id,market,trading_date,core_policy_version,
              market_adapter_version,evidence_contract_version,universe_version,
              binding_status,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                session_policy_id,
                market,
                trading_date,
                CORE_POLICY_VERSION,
                adapter.adapter_version,
                EVIDENCE_CONTRACT_VERSION,
                universe_version,
                binding_status,
                _iso(),
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute(
            "SELECT * FROM session_bindings WHERE session_policy_id=?", (session_policy_id,)
        ).fetchone())

    def append_evidence(
        self,
        *,
        module_id: str,
        market_scope: str,
        evidence_type: str,
        source_time: str,
        observed_at: str,
        direction: str | None,
        strength: float | None,
        confidence: float | None,
        valid_until: str | None,
        source_status: str,
        source_event_id: str | None,
        payload: dict[str, Any],
        core_eligible: bool = True,
    ) -> dict:
        if _dt(observed_at) < _dt(source_time):
            raise ValueError("observed_at_before_source_time")
        if source_event_id:
            row = self.conn.execute(
                "SELECT * FROM evidence_events WHERE source_event_id=?", (source_event_id,)
            ).fetchone()
            if row:
                return dict(row)
        evidence_event_id = _stable_id(
            "EVID", module_id, market_scope, evidence_type, source_time, source_event_id or json.dumps(payload, sort_keys=True)
        )
        self.conn.execute(
            """INSERT INTO evidence_events(
              evidence_event_id,module_id,market_scope,evidence_type,source_time,
              observed_at,direction,strength,confidence,valid_until,source_status,
              source_event_id,payload_json,core_eligible,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evidence_event_id,
                module_id,
                market_scope,
                evidence_type,
                source_time,
                observed_at,
                direction,
                strength,
                confidence,
                valid_until,
                source_status,
                source_event_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                1 if core_eligible else 0,
                _iso(),
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute(
            "SELECT * FROM evidence_events WHERE evidence_event_id=?", (evidence_event_id,)
        ).fetchone())

    def append_epoch(
        self,
        *,
        session_policy_id: str,
        market: str,
        trading_date: str,
        epoch_type: str,
        event_time: str,
        evidence_event_ids: list[str],
        prior_price_state: str | None,
        new_price_state: str | None,
        propagation_state: str | None,
        certification_status: str,
        certification_score: float | None,
        action_status: str,
        price_anchor: float | None,
        source_status: str = "VERIFIED",
        core_impact: str = "NONE",
        prediction_id: str | None = None,
        notes: str | None = None,
    ) -> dict:
        observed_times: list[datetime] = []
        for evidence_id in evidence_event_ids:
            row = self.conn.execute(
                "SELECT observed_at FROM evidence_events WHERE evidence_event_id=?", (evidence_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"unknown_evidence:{evidence_id}")
            observed_times.append(_dt(row["observed_at"]))
        event_dt = _dt(event_time)
        if observed_times and event_dt < max(observed_times):
            raise ValueError("epoch_event_time_before_evidence_observed_at")
        latency = None
        if observed_times:
            latency = max(0.0, (event_dt - min(observed_times)).total_seconds() / 60.0)
        epoch_id = _stable_id(
            "EPOCH", session_policy_id, epoch_type, event_time, json.dumps(sorted(evidence_event_ids))
        )
        self.conn.execute(
            """INSERT OR IGNORE INTO adaptive_epochs(
              epoch_id,session_policy_id,prediction_id,market,trading_date,epoch_type,
              event_time,prior_price_state,new_price_state,propagation_state,
              certification_status,certification_score,evidence_event_ids_json,
              action_status,price_anchor,source_status,observation_latency_minutes,
              core_impact,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                epoch_id,
                session_policy_id,
                prediction_id,
                market,
                trading_date,
                epoch_type,
                event_time,
                prior_price_state,
                new_price_state,
                propagation_state,
                certification_status,
                certification_score,
                json.dumps(evidence_event_ids, sort_keys=True),
                action_status,
                price_anchor,
                source_status,
                latency,
                core_impact,
                notes,
                _iso(),
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute(
            "SELECT * FROM adaptive_epochs WHERE epoch_id=?", (epoch_id,)
        ).fetchone())

    def _latest_driver_row(self, market: str, symbol: str) -> dict | None:
        row = self.conn.execute(
            """SELECT * FROM driver_universe_registry
               WHERE market=? AND symbol=? ORDER BY reviewed_at DESC LIMIT 1""",
            (market, symbol),
        ).fetchone()
        return dict(row) if row else None

    def review_driver_universe(
        self,
        *,
        market: str,
        trading_date: str,
        candidates: list[dict[str, Any]],
        policy: dict[str, Any],
        reviewed_at: str | None = None,
    ) -> dict[str, Any]:
        reviewed_at = reviewed_at or _iso()
        weights_cfg = dict(policy.get("score_weights") or {})
        default_weights = {
            "index_weight_score": 0.20,
            "market_impact_score": 0.25,
            "propagation_score": 0.25,
            "liquidity_score": 0.15,
            "incremental_info_score": 0.10,
            "stability_score": 0.05,
        }
        score_weights = {k: float(weights_cfg.get(k, v)) for k, v in default_weights.items()}
        admission = float(policy.get("admission_percentile", 0.75))
        retention = float(policy.get("retention_percentile", 0.50))
        if admission <= retention:
            raise ValueError("admission_percentile_must_exceed_retention_percentile")
        probation_sessions = max(1, int(policy.get("probation_sessions", 3)))
        exit_reviews = max(1, int(policy.get("exit_reviews", 2)))

        liquidity_values = [float(row.get("liquidity_raw") or 0.0) for row in candidates]
        liquidity_scores = _minmax(liquidity_values)
        scored: list[dict[str, Any]] = []
        for idx, row in enumerate(candidates):
            scores = dict(row.get("scores") or {})
            scores["liquidity_score"] = liquidity_scores[idx] if idx < len(liquidity_scores) else 0.0
            numerator = 0.0
            denominator = 0.0
            for key, weight in score_weights.items():
                value = scores.get(key)
                if value is None:
                    continue
                numerator += float(weight) * _clamp(float(value))
                denominator += float(weight)
            composite = numerator / denominator if denominator > 0 else 0.0
            scored.append({**row, "scores": scores, "composite": _clamp(composite)})
        order = sorted(range(len(scored)), key=lambda i: (-scored[i]["composite"], str(scored[i].get("symbol"))))
        percentiles = [0.0] * len(scored)
        if len(order) == 1:
            percentiles[order[0]] = 1.0
        elif order:
            for rank, idx in enumerate(order):
                percentiles[idx] = 1.0 - rank / (len(order) - 1)

        lifecycle_rows: list[dict[str, Any]] = []
        for idx, row in enumerate(scored):
            symbol = str(row["symbol"])
            hard = str(row.get("hard_gate_status") or "FAIL_UNKNOWN")
            prev = self._latest_driver_row(market, symbol)
            prev_status = str((prev or {}).get("lifecycle_status") or "CANDIDATE")
            prev_probation = int((prev or {}).get("probation_count") or 0)
            prev_below = int((prev or {}).get("consecutive_below_retention") or 0)
            pct = float(percentiles[idx])
            incremental = float((row.get("scores") or {}).get("incremental_info_score") or 0.0)
            qualifies = hard == "PASS" and pct >= admission and incremental > 0.0
            retention_ok = hard == "PASS" and pct >= retention
            status = prev_status
            probation_count = prev_probation
            below = prev_below
            admission_reason = None
            watch_reason = None
            exit_reason = None

            if hard != "PASS":
                status = "EXITED" if prev else "CANDIDATE"
                probation_count = 0
                below = 0
                exit_reason = hard
            elif prev_status in {"CANDIDATE", "EXITED"}:
                if qualifies:
                    status = "PROBATION"
                    probation_count = 1
                    below = 0
                    admission_reason = "ADMISSION_BAND_AND_INCREMENTAL_INFO_PASS"
                else:
                    status = "CANDIDATE"
                    probation_count = 0
                    below = 0
            elif prev_status == "PROBATION":
                if qualifies:
                    probation_count = prev_probation + 1
                    status = "ACTIVE" if probation_count >= probation_sessions else "PROBATION"
                    admission_reason = "PROBATION_COMPLETED" if status == "ACTIVE" else "PROBATION_CONTINUES"
                    below = 0
                elif retention_ok:
                    status = "PROBATION"
                    probation_count = 0
                    below = 0
                    watch_reason = "ADMISSION_NOT_SUSTAINED_PROBATION_RESET"
                else:
                    status = "CANDIDATE"
                    probation_count = 0
                    below = 0
                    watch_reason = "FELL_BELOW_RETENTION_DURING_PROBATION"
            elif prev_status == "ACTIVE":
                if retention_ok:
                    status = "ACTIVE"
                    below = 0
                else:
                    status = "WATCH"
                    below = 1
                    watch_reason = "BELOW_RETENTION_BAND"
            elif prev_status == "WATCH":
                if retention_ok:
                    status = "ACTIVE"
                    below = 0
                    watch_reason = "RETENTION_RECOVERED"
                else:
                    below = prev_below + 1
                    if below >= exit_reviews:
                        status = "EXITED"
                        exit_reason = "CONSECUTIVE_BELOW_RETENTION"
                    else:
                        status = "WATCH"
                        watch_reason = "BELOW_RETENTION_BAND"
            lifecycle_rows.append({
                **row,
                "rank_percentile": pct,
                "lifecycle_status": status,
                "probation_count": probation_count,
                "consecutive_below_retention": below,
                "admission_reason": admission_reason,
                "watch_reason": watch_reason,
                "exit_reason": exit_reason,
            })

        included = [r for r in lifecycle_rows if r["lifecycle_status"] in {"ACTIVE", "WATCH"}]
        total = sum(max(0.0, float(r["composite"])) for r in included)
        for row in lifecycle_rows:
            if row in included and total > 1e-15:
                row["normalized_driver_weight"] = max(0.0, float(row["composite"])) / total
            else:
                row["normalized_driver_weight"] = 0.0
        universe_payload = {
            "market": market,
            "trading_date": trading_date,
            "policy": {
                "admission_percentile": admission,
                "retention_percentile": retention,
                "probation_sessions": probation_sessions,
                "exit_reviews": exit_reviews,
                "score_weights": score_weights,
            },
            "members": [
                {
                    "symbol": r["symbol"],
                    "status": r["lifecycle_status"],
                    "weight": round(float(r["normalized_driver_weight"]), 12),
                }
                for r in sorted(lifecycle_rows, key=lambda x: str(x["symbol"]))
            ],
        }
        universe_version = "DRV-" + hashlib.sha256(
            json.dumps(universe_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        for row in lifecycle_rows:
            scores = dict(row.get("scores") or {})
            registry_id = _stable_id("DRVREG", market, trading_date, universe_version, row["symbol"])
            self.conn.execute(
                """INSERT OR IGNORE INTO driver_universe_registry(
                  registry_id,market,trading_date,universe_version,symbol,name,sector,
                  lifecycle_status,hard_gate_status,index_weight_score,market_impact_score,
                  propagation_score,liquidity_score,incremental_info_score,stability_score,
                  composite_influence_score,rank_percentile,normalized_driver_weight,
                  purchase_eligible,admission_reason,watch_reason,exit_reason,
                  probation_count,consecutive_below_retention,reviewed_at,evidence_anchor)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    registry_id,
                    market,
                    trading_date,
                    universe_version,
                    row["symbol"],
                    row.get("name"),
                    row.get("sector"),
                    row["lifecycle_status"],
                    row.get("hard_gate_status") or "FAIL_UNKNOWN",
                    scores.get("index_weight_score"),
                    scores.get("market_impact_score"),
                    scores.get("propagation_score"),
                    scores.get("liquidity_score"),
                    scores.get("incremental_info_score"),
                    scores.get("stability_score"),
                    float(row["composite"]),
                    float(row["rank_percentile"]),
                    float(row["normalized_driver_weight"]),
                    1 if bool(row.get("purchase_eligible", True)) else 0,
                    row.get("admission_reason"),
                    row.get("watch_reason"),
                    row.get("exit_reason"),
                    int(row["probation_count"]),
                    int(row["consecutive_below_retention"]),
                    reviewed_at,
                    row.get("evidence_anchor"),
                ),
            )
        self.conn.commit()
        return {
            "market": market,
            "trading_date": trading_date,
            "universe_version": universe_version,
            "policy": universe_payload["policy"],
            "members": lifecycle_rows,
        }

    def create_preopen_brief(
        self,
        *,
        session_policy_id: str,
        market: str,
        trading_date: str,
        current_view: str,
        one_line_action: str,
        broad_market_guard: str,
        objects: list[dict[str, Any]],
        level_rule: dict[str, Any],
        evidence_anchor: str | None = None,
        generated_at: str | None = None,
    ) -> dict[str, Any]:
        generated_at = generated_at or _iso()
        row = self.conn.execute(
            "SELECT COALESCE(MAX(brief_version),0) AS n FROM preopen_briefs WHERE market=? AND trading_date=?",
            (market, trading_date),
        ).fetchone()
        version = int(row["n"] or 0) + 1
        executable = [obj for obj in objects if obj.get("latest_price") and float(obj.get("sigma20") or 0.0) > 0]
        publish_status = "PUBLISHED" if executable else "PUBLISH_HOLD"
        brief_id = _stable_id("BRIEF", market, trading_date, version, generated_at)
        self.conn.execute(
            """INSERT INTO preopen_briefs(
              brief_id,session_policy_id,market,trading_date,brief_version,generated_at,
              current_view,one_line_action,broad_market_guard,publish_status,evidence_anchor)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                brief_id,
                session_policy_id,
                market,
                trading_date,
                version,
                generated_at,
                current_view,
                one_line_action,
                broad_market_guard,
                publish_status,
                evidence_anchor,
            ),
        )
        created_levels: list[dict[str, Any]] = []
        for obj in objects[:6]:
            latest_price = obj.get("latest_price")
            sigma20 = float(obj.get("sigma20") or 0.0)
            if latest_price is None or float(latest_price) <= 0 or sigma20 <= 0:
                continue
            intent = str(obj.get("intent") or "OBSERVE").upper()
            ref = float(latest_price)
            trigger_mult = float(level_rule.get("trigger_sigma_mult", 0.25))
            exec_mult = float(level_rule.get("execute_sigma_mult", 0.25))
            invalid_mult = float(level_rule.get("invalidation_sigma_mult", 0.75))
            target1_mult = float(level_rule.get("target1_sigma_mult", 1.00))
            target2_mult = float(level_rule.get("target2_sigma_mult", 1.75))
            max_slippage = float(level_rule.get("max_slippage_pct", 0.003))
            if intent in {"REDUCE", "AVOID", "SELL"}:
                trigger = ref * (1.0 - trigger_mult * sigma20)
                execute_low = trigger * (1.0 - exec_mult * sigma20)
                execute_high = trigger
                invalidation = ref * (1.0 + invalid_mult * sigma20)
                target1 = None
                target2 = None
                position_step = str(obj.get("position_step") or "REDUCE_1_3")
            elif intent in {"BUILD", "BUY", "ADD"}:
                trigger = ref * (1.0 + trigger_mult * sigma20)
                execute_low = trigger
                execute_high = trigger * (1.0 + exec_mult * sigma20)
                invalidation = ref * (1.0 - invalid_mult * sigma20)
                target1 = ref * (1.0 + target1_mult * sigma20)
                target2 = ref * (1.0 + target2_mult * sigma20)
                position_step = str(obj.get("position_step") or "BUILD_1_3")
            else:
                continue
            valid_from = generated_at
            valid_minutes = max(1, int(level_rule.get("valid_minutes", 90)))
            valid_until = (_dt(generated_at) + timedelta(minutes=valid_minutes)).isoformat()
            action_level_id = _stable_id("LEVEL", brief_id, obj["symbol"], intent)
            self.conn.execute(
                """INSERT INTO preopen_action_levels(
                  action_level_id,brief_id,market,trading_date,object_type,symbol,
                  object_name,intent,position_step,reference_price,reference_time,
                  reference_source,trigger_type,trigger_price,execute_low,execute_high,
                  invalidation_price,target1_price,target2_price,max_slippage_pct,
                  valid_from,valid_until,price_status,observed_at,evidence_anchor,
                  evaluation_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    action_level_id,
                    brief_id,
                    market,
                    trading_date,
                    str(obj.get("object_type") or "STOCK"),
                    obj["symbol"],
                    obj.get("name") or obj["symbol"],
                    intent,
                    position_step,
                    ref,
                    str(obj.get("reference_time") or generated_at),
                    str(obj.get("reference_source") or "LOCAL_VERIFIED_MARKET_DATA"),
                    "VOL_BAND_V1",
                    trigger,
                    min(execute_low, execute_high),
                    max(execute_low, execute_high),
                    invalidation,
                    target1,
                    target2,
                    max_slippage,
                    valid_from,
                    valid_until,
                    "EXECUTABLE",
                    generated_at,
                    obj.get("evidence_anchor") or evidence_anchor,
                    "PENDING",
                ),
            )
            created_levels.append({
                "action_level_id": action_level_id,
                "symbol": obj["symbol"],
                "intent": intent,
                "position_step": position_step,
                "reference_price": ref,
                "trigger_price": trigger,
                "execute_low": min(execute_low, execute_high),
                "execute_high": max(execute_low, execute_high),
                "invalidation_price": invalidation,
                "target1_price": target1,
                "target2_price": target2,
                "price_status": "EXECUTABLE",
            })
        self.conn.commit()
        return {
            "brief_id": brief_id,
            "brief_version": version,
            "publish_status": publish_status,
            "levels": created_levels,
        }

    def market_driver_snapshot(self, market: str, trading_date: str) -> dict[str, Any]:
        rows = self.conn.execute(
            """SELECT * FROM driver_universe_registry
               WHERE market=? AND trading_date=? ORDER BY normalized_driver_weight DESC,symbol""",
            (market, trading_date),
        ).fetchall()
        return {
            "market": market,
            "trading_date": trading_date,
            "rows": [dict(row) for row in rows],
        }

    def status(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "db_path": str(self.path),
            "activation_probe_ok": self.activation_probe(),
            "table_counts": {
                table: int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
                for table in (
                    "session_bindings",
                    "evidence_events",
                    "adaptive_epochs",
                    "driver_universe_registry",
                    "preopen_briefs",
                    "preopen_action_levels",
                )
            },
        }


def adapter_local_now(market: str, now: datetime | None = None) -> datetime:
    adapter = MARKET_ADAPTERS[market]
    now = now or _utc_now()
    return now.astimezone(ZoneInfo(adapter.timezone))


def phase_from_clock(market: str, now: datetime | None = None) -> str:
    local = adapter_local_now(market, now)
    hhmm = local.strftime("%H:%M")
    adapter = MARKET_ADAPTERS[market]
    if adapter.preopen_start <= hhmm < adapter.regular_open:
        return "PREOPEN"
    if adapter.regular_open <= hhmm < adapter.regular_close:
        return "OPEN"
    if hhmm >= adapter.regular_close:
        return "POSTCLOSE"
    return "CLOSED"
