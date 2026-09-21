from __future__ import annotations

from copy import deepcopy
from math import prod
from typing import Dict, Iterable, List

from .contracts import MarketSnapshot, StrategyGroup, StrategyState, TriaidDecision, utc_now
from .store import RunStore


class ProspectiveExperimentProtocol:
    """Pre-registered, no-retuning comparison protocol for CN stress-pool recovery."""

    version = "cn-prospective-controls@0.1.0"
    experiment_mode = "CN_WORST_POOL_RESCUE"
    state_file = "cn_prospective_experiments.json"

    def __init__(self, store: RunStore) -> None:
        self.store = store
        raw = store.load_json(self.state_file, default={})
        if not raw:
            raw = {"version": self.version, "experiments": []}
        raw["version"] = self.version
        raw.setdefault("experiments", [])
        self.state = raw
        self._save()

    def _save(self) -> None:
        self.store.save_json(self.state_file, self.state)

    @staticmethod
    def _compound(values: Iterable[float]) -> float:
        values = list(values)
        if not values:
            return 0.0
        return prod(1.0 + float(x) for x in values) - 1.0

    @staticmethod
    def _average_ranks(scores: Dict[str, float], descending: bool = True) -> Dict[str, float]:
        items = sorted(scores.items(), key=lambda x: ((-x[1]) if descending else x[1], x[0]))
        ranks: Dict[str, float] = {}
        i = 0
        while i < len(items):
            j = i + 1
            while j < len(items) and items[j][1] == items[i][1]:
                j += 1
            avg = ((i + 1) + j) / 2.0
            for k in range(i, j):
                ranks[items[k][0]] = avg
            i = j
        return ranks

    @staticmethod
    def _ordered(ranks: Dict[str, float]) -> List[dict]:
        return [
            {"strategy_id": sid, "rank": float(rank)}
            for sid, rank in sorted(ranks.items(), key=lambda x: (x[1], x[0]))
        ]

    @staticmethod
    def _spearman(predicted: Dict[str, float], actual: Dict[str, float]) -> float | None:
        keys = sorted(set(predicted) & set(actual))
        if len(keys) < 2:
            return None
        px = [float(predicted[k]) for k in keys]
        ax = [float(actual[k]) for k in keys]
        pm = sum(px) / len(px)
        am = sum(ax) / len(ax)
        num = sum((p - pm) * (a - am) for p, a in zip(px, ax))
        den_p = sum((p - pm) ** 2 for p in px)
        den_a = sum((a - am) ** 2 for a in ax)
        if den_p <= 1e-18 or den_a <= 1e-18:
            return None
        return num / ((den_p * den_a) ** 0.5)

    @staticmethod
    def _pairwise_accuracy(predicted: Dict[str, float], actual: Dict[str, float]) -> dict:
        keys = sorted(set(predicted) & set(actual))
        correct = 0
        comparable = 0
        for i, left in enumerate(keys):
            for right in keys[i + 1:]:
                pd = float(predicted[left]) - float(predicted[right])
                ad = float(actual[left]) - float(actual[right])
                if pd == 0 or ad == 0:
                    continue
                comparable += 1
                if (pd < 0 and ad < 0) or (pd > 0 and ad > 0):
                    correct += 1
        return {
            "correct_pairs": correct,
            "comparable_pairs": comparable,
            "accuracy": (correct / comparable) if comparable else None,
        }

    @staticmethod
    def _state_map(states: Iterable[StrategyState]) -> Dict[str, StrategyState]:
        return {s.strategy_id: s for s in states}

    def _control_rankings(
        self,
        pool: List[str],
        states: Dict[str, StrategyState],
        previous_states: Dict[str, StrategyState] | None,
    ) -> dict:
        current_expected = {sid: float(states[sid].expected_net_return) for sid in pool}
        momentum20 = {
            sid: self._compound(list(states[sid].recent_returns)[-20:])
            for sid in pool
        }
        low_risk = {sid: -float(states[sid].risk) for sid in pool}
        low_uncertainty = {sid: -float(states[sid].uncertainty) for sid in pool}

        component_scores = {
            "current_expected_return": current_expected,
            "momentum_20": momentum20,
            "low_risk": low_risk,
            "low_uncertainty": low_uncertainty,
        }
        transition_available = bool(previous_states) and all(sid in previous_states for sid in pool)
        if transition_available:
            component_scores["state_direction"] = {
                sid: float(states[sid].expected_net_return)
                - float(previous_states[sid].expected_net_return)
                for sid in pool
            }

        component_ranks = {
            name: self._average_ranks(scores, descending=True)
            for name, scores in component_scores.items()
        }
        triaid_borda_score = {
            sid: -sum(component_ranks[name][sid] for name in component_ranks)
            for sid in pool
        }

        ranking_scores = {
            "CURRENT_EXPECTED_RETURN": current_expected,
            "MOMENTUM_20": momentum20,
            "LOW_RISK": low_risk,
            "TRIAID_STATE_TRANSITION": triaid_borda_score,
        }
        return {
            "ranking_scores": ranking_scores,
            "rankings": {
                name: self._average_ranks(scores, descending=True)
                for name, scores in ranking_scores.items()
            },
            "triaid_components": {
                "component_scores": component_scores,
                "component_ranks": component_ranks,
                "aggregation": "equal-weight Borda rank aggregation; no fitted coefficients",
                "transition_component_available": transition_available,
            },
        }

    def register(
        self,
        *,
        run_id: str,
        market: MarketSnapshot,
        group: StrategyGroup,
        states: Iterable[StrategyState],
        decision: TriaidDecision,
        horizons: Iterable[int],
        previous_states: Iterable[StrategyState] | None = None,
    ) -> dict:
        if market.market_id.upper() != "CN":
            raise ValueError("Prospective CN protocol only accepts CN market runs.")
        if str(market.metadata.get("experiment_mode") or "").upper() != self.experiment_mode:
            raise ValueError("Run is not a CN worst-pool rescue experiment.")

        existing = next(
            (
                x for x in self.state["experiments"]
                if x.get("source_run_id") == run_id
            ),
            None,
        )
        if existing:
            return deepcopy(existing)

        pool = [
            sid for sid in group.members
            if sid != "P28_CASH" and float(group.weights.get(sid, 0.0)) > 0
        ]
        if len(pool) < 2:
            raise ValueError("Prospective recovery experiment requires at least two risky strategies.")

        state_map = self._state_map(states)
        missing = [sid for sid in pool if sid not in state_map]
        if missing:
            raise ValueError(f"Missing frozen strategy states: {missing}")

        previous_map = self._state_map(previous_states or [])
        controls = self._control_rankings(pool, state_map, previous_map or None)
        frozen_horizons = sorted({int(x) for x in horizons if int(x) > 0})
        if not frozen_horizons:
            raise ValueError("At least one positive observation horizon is required.")

        experiment_id = f"CNPROS-{run_id}"
        experiment = {
            "experiment_id": experiment_id,
            "protocol_version": self.version,
            "status": "OPEN",
            "registered_at": utc_now(),
            "source_run_id": run_id,
            "market_as_of": market.as_of,
            "snapshot_id": market.snapshot_id,
            "experiment_mode": self.experiment_mode,
            "design": {
                "pool_rule": "freeze the contemporaneously selected adverse risky pool; no member replacement after registration",
                "no_future_information": True,
                "no_post_result_retuning": True,
                "outcome_unit": "subsequent realized strategy returns from the production evaluation pipeline",
                "horizons_trading_days": frozen_horizons,
                "ranking_metrics": [
                    "spearman_rank_correlation",
                    "pairwise_ordering_accuracy",
                    "actual_best_strategy_predicted_rank",
                ],
                "portfolio_controls": {
                    "HOLD_EQUAL": "keep the frozen adverse pool at its original equal weights",
                    "CASH_DEFENSE": "zero risky exposure; return fixed at 0 before financing effects",
                    "TRIAID_STATIC_ALLOCATION": "freeze the contemporaneous TRIAID post-decision weights; isolates allocation/cash effect from later timing",
                },
                "ranking_controls": {
                    "CURRENT_EXPECTED_RETURN": "rank only by current expected net return",
                    "MOMENTUM_20": "rank by trailing 20-observation compounded strategy return; horizon inherited from the existing P13 20-day momentum rule",
                    "LOW_RISK": "rank lower-risk strategies first; aligned with the existing inverse-volatility/risk-control family",
                    "TRIAID_STATE_TRANSITION": "coefficient-free Borda aggregation of current expected return, 20-observation momentum, state direction when available, risk and uncertainty",
                },
            },
            "pool": pool,
            "frozen_baseline_weights": {
                sid: float(group.weights.get(sid, 0.0))
                for sid in group.members
            },
            "frozen_triaid_weights": {
                sid: float(decision.weights_after.get(sid, 0.0))
                for sid in set(group.members) | set(decision.weights_after)
            },
            "frozen_state": {
                sid: {
                    "expected_net_return": float(state_map[sid].expected_net_return),
                    "risk": float(state_map[sid].risk),
                    "uncertainty": float(state_map[sid].uncertainty),
                    "recent_returns": [float(x) for x in state_map[sid].recent_returns],
                }
                for sid in pool
            },
            "control_scores": controls["ranking_scores"],
            "control_rankings": {
                name: self._ordered(ranks)
                for name, ranks in controls["rankings"].items()
            },
            "triaid_components": controls["triaid_components"],
            "outcomes": [],
            "incomplete_observations": [],
            "evaluations": {},
        }
        self.state["experiments"].append(experiment)
        self._save()
        return deepcopy(experiment)

    def _evaluate_horizon(self, experiment: dict, horizon: int) -> dict:
        periods = list(experiment["outcomes"])[:horizon]
        pool = list(experiment["pool"])
        realized = {
            sid: self._compound([float(p["realized_returns"][sid]) for p in periods])
            for sid in pool
        }
        actual_ranks = self._average_ranks(realized, descending=True)
        controls = {}
        for name, rows in experiment["control_rankings"].items():
            predicted = {row["strategy_id"]: float(row["rank"]) for row in rows}
            best_sid = min(realized, key=lambda sid: actual_ranks[sid])
            controls[name] = {
                "spearman_rank_correlation": self._spearman(predicted, actual_ranks),
                "pairwise_ordering": self._pairwise_accuracy(predicted, actual_ranks),
                "actual_best_strategy": best_sid,
                "actual_best_realized_return": realized[best_sid],
                "actual_best_strategy_predicted_rank": predicted.get(best_sid),
            }

        baseline = experiment["frozen_baseline_weights"]
        triaid = experiment["frozen_triaid_weights"]
        hold_equal = sum(float(baseline.get(sid, 0.0)) * realized[sid] for sid in pool)
        triaid_static = sum(float(triaid.get(sid, 0.0)) * realized[sid] for sid in pool)
        return {
            "horizon_trading_days": horizon,
            "periods_used": [p["as_of"] for p in periods],
            "strategy_realized_returns": realized,
            "actual_ranking": self._ordered(actual_ranks),
            "ranking_controls": controls,
            "portfolio_controls": {
                "HOLD_EQUAL": hold_equal,
                "CASH_DEFENSE": 0.0,
                "TRIAID_STATIC_ALLOCATION": triaid_static,
                "TRIAID_STATIC_MINUS_HOLD_EQUAL": triaid_static - hold_equal,
            },
            "interpretation_guard": "Ranking metrics test recovery-selection ability. Static allocation return includes cash/exposure effects and must not be presented as recovery-selection evidence.",
        }

    def observe_period(self, as_of: str, realized_returns: Dict[str, float]) -> dict:
        updated = []
        for experiment in self.state["experiments"]:
            if experiment.get("status") != "OPEN":
                continue
            if as_of < str(experiment.get("market_as_of") or ""):
                continue
            if any(p.get("as_of") == as_of for p in experiment.get("outcomes", [])):
                continue

            pool = list(experiment.get("pool", []))
            missing = [sid for sid in pool if sid not in realized_returns]
            if missing:
                incomplete=experiment.setdefault("incomplete_observations", [])
                already=any(
                    row.get("as_of")==as_of
                    and row.get("missing_strategy_ids")==missing
                    for row in incomplete
                )
                if not already:
                    incomplete.append({
                        "as_of": as_of,
                        "missing_strategy_ids": missing,
                        "recorded_at": utc_now(),
                    })
                    updated.append(experiment["experiment_id"])
                continue

            experiment.setdefault("outcomes", []).append({
                "as_of": as_of,
                "recorded_at": utc_now(),
                "realized_returns": {sid: float(realized_returns[sid]) for sid in pool},
            })
            for horizon in experiment["design"]["horizons_trading_days"]:
                key = str(horizon)
                if len(experiment["outcomes"]) >= horizon and key not in experiment["evaluations"]:
                    experiment["evaluations"][key] = self._evaluate_horizon(experiment, horizon)
            max_h = max(experiment["design"]["horizons_trading_days"])
            if len(experiment["outcomes"]) >= max_h:
                experiment["status"] = "COMPLETE"
                experiment["completed_at"] = utc_now()
            updated.append(experiment["experiment_id"])
        if updated:
            self._save()
        return {"updated_experiment_ids": updated, "as_of": as_of}

    def get(self, experiment_id: str) -> dict:
        for experiment in self.state["experiments"]:
            if experiment.get("experiment_id") == experiment_id:
                return deepcopy(experiment)
        raise KeyError(experiment_id)

    def list(self, limit: int = 100) -> List[dict]:
        return deepcopy(self.state["experiments"][-max(1, int(limit)):])

    def latest(self) -> dict | None:
        if not self.state["experiments"]:
            return None
        return deepcopy(self.state["experiments"][-1])

    def status(self) -> dict:
        rows = self.state["experiments"]
        return {
            "version": self.version,
            "experiment_mode": self.experiment_mode,
            "experiment_count": len(rows),
            "open_count": sum(1 for x in rows if x.get("status") == "OPEN"),
            "complete_count": sum(1 for x in rows if x.get("status") == "COMPLETE"),
            "latest_experiment_id": rows[-1]["experiment_id"] if rows else None,
        }
