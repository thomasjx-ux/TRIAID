from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "preopen-action-evaluation@1.0.0"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return f"{prefix}|{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


class ActionEvaluationStore:
    """Append-only post-decision evaluation ledger.

    Pre-open action levels are immutable plans. Fill/outcome evidence is appended
    here instead of mutating the original recommendation after the market moves.
    """

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "adaptive_v36.sqlite3"
        self.conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS preopen_action_evaluations(
              evaluation_id TEXT PRIMARY KEY,
              action_level_id TEXT NOT NULL,
              evaluated_at TEXT NOT NULL,
              evaluation_status TEXT NOT NULL,
              fill_price REAL,
              fill_time TEXT,
              close_price REAL,
              close_time TEXT,
              max_adverse_excursion REAL,
              max_favorable_excursion REAL,
              action_value_vs_no_action REAL,
              slippage_pct REAL,
              evidence_anchor TEXT,
              notes TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_action_eval_level
              ON preopen_action_evaluations(action_level_id,evaluated_at);
            CREATE TRIGGER IF NOT EXISTS preopen_action_evaluations_no_update
            BEFORE UPDATE ON preopen_action_evaluations
            BEGIN SELECT RAISE(ABORT,'append_only:preopen_action_evaluations:update'); END;
            CREATE TRIGGER IF NOT EXISTS preopen_action_evaluations_no_delete
            BEFORE DELETE ON preopen_action_evaluations
            BEGIN SELECT RAISE(ABORT,'append_only:preopen_action_evaluations:delete'); END;
            """
        )
        self.conn.commit()

    def append(
        self,
        *,
        action_level_id: str,
        evaluation_status: str,
        evaluated_at: str | None = None,
        fill_price: float | None = None,
        fill_time: str | None = None,
        close_price: float | None = None,
        close_time: str | None = None,
        max_adverse_excursion: float | None = None,
        max_favorable_excursion: float | None = None,
        action_value_vs_no_action: float | None = None,
        slippage_pct: float | None = None,
        evidence_anchor: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        plan = self.conn.execute(
            "SELECT * FROM preopen_action_levels WHERE action_level_id=?",
            (action_level_id,),
        ).fetchone()
        if not plan:
            raise ValueError(f"unknown_action_level:{action_level_id}")
        evaluated_at = evaluated_at or _iso_now()
        if fill_time and str(fill_time) < str(plan["valid_from"]):
            raise ValueError("fill_time_before_action_valid_from")
        if fill_price is not None:
            low = float(plan["execute_low"])
            high = float(plan["execute_high"])
            max_slippage = float(plan["max_slippage_pct"])
            lower_allowed = low * (1.0 - max_slippage)
            upper_allowed = high * (1.0 + max_slippage)
            if not lower_allowed <= float(fill_price) <= upper_allowed:
                raise ValueError("fill_outside_sealed_execution_and_slippage_bounds")
        evaluation_id = _stable_id(
            "EVAL",
            action_level_id,
            evaluated_at,
            evaluation_status,
            fill_time or "",
            close_time or "",
        )
        self.conn.execute(
            """INSERT INTO preopen_action_evaluations(
              evaluation_id,action_level_id,evaluated_at,evaluation_status,
              fill_price,fill_time,close_price,close_time,max_adverse_excursion,
              max_favorable_excursion,action_value_vs_no_action,slippage_pct,
              evidence_anchor,notes,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evaluation_id,
                action_level_id,
                evaluated_at,
                evaluation_status,
                fill_price,
                fill_time,
                close_price,
                close_time,
                max_adverse_excursion,
                max_favorable_excursion,
                action_value_vs_no_action,
                slippage_pct,
                evidence_anchor,
                notes,
                _iso_now(),
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM preopen_action_evaluations WHERE evaluation_id=?",
            (evaluation_id,),
        ).fetchone()
        return dict(row)

    def latest(self, action_level_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT * FROM preopen_action_evaluations
               WHERE action_level_id=? ORDER BY evaluated_at DESC LIMIT 1""",
            (action_level_id,),
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.conn.close()
