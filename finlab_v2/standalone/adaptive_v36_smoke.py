from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from adaptive_v36 import AdaptiveV36Store


POLICY = {
    "admission_percentile": 0.75,
    "retention_percentile": 0.50,
    "probation_sessions": 3,
    "exit_reviews": 2,
}


def candidate(symbol: str, impact: float, prop: float, inc: float, liq: float = 10.0):
    return {
        "symbol": symbol,
        "name": symbol,
        "sector": "TEST",
        "hard_gate_status": "PASS",
        "purchase_eligible": True,
        "liquidity_raw": liq,
        "scores": {
            "index_weight_score": 0.5,
            "market_impact_score": impact,
            "propagation_score": prop,
            "incremental_info_score": inc,
            "stability_score": 0.9,
        },
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = AdaptiveV36Store(Path(td))
        assert store.activation_probe() is True

        high = [
            candidate("AAA", 0.95, 0.90, 0.40, 12.0),
            candidate("BBB", 0.60, 0.55, 0.20, 11.0),
            candidate("CCC", 0.20, 0.15, 0.05, 10.0),
        ]
        r1 = store.review_driver_universe(market="US", trading_date="2026-10-20", candidates=high, policy=POLICY)
        r2 = store.review_driver_universe(market="US", trading_date="2026-10-21", candidates=high, policy=POLICY)
        r3 = store.review_driver_universe(market="US", trading_date="2026-10-22", candidates=high, policy=POLICY)
        status = {row["symbol"]: row["lifecycle_status"] for row in r3["members"]}
        assert status["AAA"] == "ACTIVE", status

        low = [
            candidate("AAA", 0.05, 0.05, 0.01, 10.0),
            candidate("BBB", 0.95, 0.90, 0.40, 12.0),
            candidate("CCC", 0.75, 0.70, 0.30, 11.0),
        ]
        r4 = store.review_driver_universe(market="US", trading_date="2026-10-23", candidates=low, policy=POLICY)
        s4 = {row["symbol"]: row["lifecycle_status"] for row in r4["members"]}
        assert s4["AAA"] == "WATCH", s4
        r5 = store.review_driver_universe(market="US", trading_date="2026-10-26", candidates=low, policy=POLICY)
        s5 = {row["symbol"]: row["lifecycle_status"] for row in r5["members"]}
        assert s5["AAA"] == "EXITED", s5

        binding = store.bind_session(
            market="US",
            trading_date="2026-10-26",
            universe_version=r5["universe_version"],
        )
        assert binding["binding_status"] == "PASS"
        same = store.bind_session(
            market="US",
            trading_date="2026-10-26",
            universe_version=r5["universe_version"],
        )
        assert same["session_policy_id"] == binding["session_policy_id"]

        source_time = "2026-10-26T12:00:00+00:00"
        observed_at = "2026-10-26T12:05:00+00:00"
        evidence = store.append_evidence(
            module_id="TEST",
            market_scope="US",
            evidence_type="PRICE_STATE",
            source_time=source_time,
            observed_at=observed_at,
            direction="DOWN",
            strength=0.7,
            confidence=0.8,
            valid_until="2026-10-26T13:05:00+00:00",
            source_status="VERIFIED",
            source_event_id="test-source-1",
            payload={"test": True},
        )
        epoch = store.append_epoch(
            session_policy_id=binding["session_policy_id"],
            market="US",
            trading_date="2026-10-26",
            epoch_type="PREOPEN",
            event_time="2026-10-26T12:06:00+00:00",
            evidence_event_ids=[evidence["evidence_event_id"]],
            prior_price_state="STABLE",
            new_price_state="WEAK",
            propagation_state="PROPAGATING",
            certification_status="CERTIFIED",
            certification_score=0.8,
            action_status="SHADOW_REDUCE",
            price_anchor=100.0,
        )
        assert epoch["observation_latency_minutes"] >= 1.0

        try:
            store.append_evidence(
                module_id="TEST",
                market_scope="US",
                evidence_type="BAD_TIME",
                source_time="2026-10-26T12:10:00+00:00",
                observed_at="2026-10-26T12:09:00+00:00",
                direction=None,
                strength=None,
                confidence=None,
                valid_until=None,
                source_status="VERIFIED",
                source_event_id="bad-time",
                payload={},
            )
        except ValueError:
            pass
        else:
            raise AssertionError("backdated evidence was accepted")

        brief = store.create_preopen_brief(
            session_policy_id=binding["session_policy_id"],
            market="US",
            trading_date="2026-10-26",
            current_view="Tech risk is concentrated, not systemic.",
            one_line_action="Reduce the weak driver; build only after confirmation.",
            broad_market_guard="Do not cut broad market solely on one driver.",
            objects=[
                {
                    "symbol": "BBB",
                    "name": "BBB",
                    "intent": "REDUCE",
                    "position_step": "REDUCE_1_3",
                    "latest_price": 100.0,
                    "sigma20": 0.02,
                    "reference_time": observed_at,
                    "reference_source": "SYNTHETIC_VERIFIED",
                },
                {
                    "symbol": "CCC",
                    "name": "CCC",
                    "intent": "BUILD",
                    "position_step": "BUILD_1_3",
                    "latest_price": 50.0,
                    "sigma20": 0.025,
                    "reference_time": observed_at,
                    "reference_source": "SYNTHETIC_VERIFIED",
                },
            ],
            level_rule={"valid_minutes": 90},
            generated_at="2026-10-26T12:07:00+00:00",
        )
        assert brief["publish_status"] == "PUBLISHED"
        assert len(brief["levels"]) == 2
        opp = [x for x in brief["levels"] if x["intent"] == "BUILD"][0]
        assert opp["target1_price"] is not None

        try:
            store.conn.execute(
                "UPDATE session_bindings SET binding_status='NO' WHERE session_policy_id=?",
                (binding["session_policy_id"],),
            )
            store.conn.commit()
        except sqlite3.DatabaseError:
            store.conn.rollback()
        else:
            raise AssertionError("append-only session binding was mutable")

        print("ADAPTIVE_V36_SMOKE_OK", r1["universe_version"], r5["universe_version"], brief["brief_id"])


if __name__ == "__main__":
    main()
