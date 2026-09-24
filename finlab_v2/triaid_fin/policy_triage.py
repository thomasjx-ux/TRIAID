from __future__ import annotations

import math
from statistics import mean
from typing import Iterable

from .contracts import StrategyGroup, StrategyState
from .strategy_registry import FAMILIES

VERSION = "policy-triage@0.1.0"


class PolicyTriageModule:
    """Shadow-only policy-network and policy-chain triage.

    This module never changes live weights. It compresses the admissible policy
    network into selected policies, one challenger per omitted family, redundant
    alternatives and blocked policies. Future outcomes are then used to evaluate
    whether the triage missed a better family representative.

    The policy chain is intentionally asymmetric:
    STATE -> TRIAGE -> SELECTION -> TRANSITION -> INTERVENTION -> OUTCOME.
    Outcome may score prior frozen triage but may not rewrite it.
    """

    version = VERSION

    @staticmethod
    def _score(state: StrategyState) -> float:
        return float(state.expected_net_return) - max(0.0, float(state.estimated_cost))

    @staticmethod
    def _corr(a: list[float], b: list[float], lookback: int = 126) -> float | None:
        n = min(len(a), len(b), lookback)
        if n < 20:
            return None
        x = a[-n:]
        y = b[-n:]
        mx = mean(x)
        my = mean(y)
        dx = [v - mx for v in x]
        dy = [v - my for v in y]
        vx = sum(v * v for v in dx)
        vy = sum(v * v for v in dy)
        if vx <= 1e-18 or vy <= 1e-18:
            return None
        return max(-1.0, min(1.0, sum(i * j for i, j in zip(dx, dy)) / math.sqrt(vx * vy)))

    @staticmethod
    def _blocked_reasons(state: StrategyState) -> list[str]:
        reasons = []
        if not state.eligible:
            reasons.append("INELIGIBLE")
        if state.hard_failure:
            reasons.append("HARD_FAILURE")
        if state.lifecycle not in {"active", "reduced"}:
            reasons.append(f"LIFECYCLE_{state.lifecycle.upper()}")
        if not state.liquidity_ok:
            reasons.append("LIQUIDITY")
        if not state.capacity_ok:
            reasons.append("CAPACITY")
        if not state.risk_ok:
            reasons.append("RISK")
        if not state.concentration_ok:
            reasons.append("CONCENTRATION")
        return reasons

    @staticmethod
    def _mechanism(family: str) -> str:
        if family in {"cash", "defensive_rotation", "risk_control", "drawdown_control"}:
            return "DIRECT_DEFENSE"
        if family in {"volatility_control", "risk_balanced_allocation"}:
            return "RISK_SCALING"
        if family in {"time_series_momentum", "cross_asset_momentum", "cross_asset_trend", "breadth_rotation"}:
            return "PERSISTENCE_OR_ROTATION"
        if family == "short_horizon_reversal":
            return "REVERSAL"
        if family in {"market_beta", "strategic_allocation"}:
            return "BETA_OR_BALANCED"
        return "OTHER"

    def snapshot(
        self,
        market_id: str,
        market_regime: str | None,
        states: Iterable[StrategyState],
        group: StrategyGroup,
    ) -> dict:
        states = list(states)
        selected = {
            sid
            for sid, weight in (group.weights or {}).items()
            if sid != "P28_CASH" and float(weight) > 1e-12
        }
        state_map = {s.strategy_id: s for s in states}

        blocked = []
        admissible = []
        for state in states:
            if state.strategy_id == "P28_CASH":
                continue
            reasons = self._blocked_reasons(state)
            if reasons:
                blocked.append({
                    "strategy_id": state.strategy_id,
                    "family": FAMILIES.get(state.strategy_id, "unknown"),
                    "reasons": reasons,
                })
            else:
                admissible.append(state)

        by_family: dict[str, list[StrategyState]] = {}
        for state in admissible:
            by_family.setdefault(FAMILIES.get(state.strategy_id, "unknown"), []).append(state)
        for rows in by_family.values():
            rows.sort(key=lambda s: (-self._score(s), s.strategy_id))

        challengers = []
        redundant = []
        family_rows = []
        selected_families = {FAMILIES.get(sid, "unknown") for sid in selected}
        for family, rows in sorted(by_family.items()):
            selected_rows = [s for s in rows if s.strategy_id in selected]
            omitted = [s for s in rows if s.strategy_id not in selected]
            challenger = omitted[0] if omitted else None
            if challenger is not None:
                challengers.append({
                    "strategy_id": challenger.strategy_id,
                    "family": family,
                    "mechanism": self._mechanism(family),
                    "frozen_net_return_proxy": self._score(challenger),
                    "expected_net_return": float(challenger.expected_net_return),
                    "estimated_cost": float(challenger.estimated_cost),
                    "risk": float(challenger.risk),
                    "uncertainty": float(challenger.uncertainty),
                    "family_was_unrepresented": family not in selected_families,
                })
                for state in omitted[1:]:
                    redundant.append({
                        "strategy_id": state.strategy_id,
                        "family": family,
                        "mechanism": self._mechanism(family),
                        "frozen_net_return_proxy": self._score(state),
                    })
            family_rows.append({
                "family": family,
                "mechanism": self._mechanism(family),
                "admissible_count": len(rows),
                "selected_strategy_ids": [s.strategy_id for s in selected_rows],
                "challenger_strategy_id": challenger.strategy_id if challenger else None,
                "best_frozen_net_return_proxy": self._score(rows[0]) if rows else None,
            })

        duplicate_pairs = []
        for family, rows in by_family.items():
            for i, left in enumerate(rows):
                for right in rows[i + 1:]:
                    corr = self._corr(left.recent_returns, right.recent_returns)
                    if corr is not None and abs(corr) >= 0.99:
                        duplicate_pairs.append({
                            "family": family,
                            "left": left.strategy_id,
                            "right": right.strategy_id,
                            "abs_corr": abs(corr),
                            "preferred_by_frozen_return": (
                                left.strategy_id
                                if self._score(left) >= self._score(right)
                                else right.strategy_id
                            ),
                        })

        challengers.sort(key=lambda row: (-float(row["frozen_net_return_proxy"]), row["strategy_id"]))
        selected_rows = []
        for sid in sorted(selected):
            state = state_map.get(sid)
            if state is None:
                continue
            family = FAMILIES.get(sid, "unknown")
            selected_rows.append({
                "strategy_id": sid,
                "family": family,
                "mechanism": self._mechanism(family),
                "frozen_net_return_proxy": self._score(state),
                "weight": float(group.weights.get(sid, 0.0)),
            })

        return {
            "version": self.version,
            "market_id": str(market_id).upper(),
            "market_regime": market_regime,
            "mode": "SHADOW_ONLY_NO_WEIGHT_EFFECT",
            "no_hindsight_contamination": True,
            "policy_network": {
                "admissible_count": len(admissible),
                "selected_count": len(selected_rows),
                "family_count": len(by_family),
                "selected_family_count": len(selected_families),
                "blocked_count": len(blocked),
                "challenger_count": len(challengers),
                "high_correlation_pair_count": len(duplicate_pairs),
                "selected": selected_rows,
                "family_map": family_rows,
                "challengers": challengers,
                "redundant": redundant,
                "blocked": blocked,
                "high_correlation_pairs": duplicate_pairs,
            },
            "policy_chain": {
                "stages": [
                    "STATE",
                    "TRIAGE",
                    "SELECTION",
                    "TRANSITION",
                    "INTERVENTION",
                    "OUTCOME",
                ],
                "contracts": {
                    "STATE_TO_TRIAGE": "Only contemporaneously available state and strategy evidence may enter triage.",
                    "TRIAGE_TO_SELECTION": "Triage may rank and route policies but cannot use future realized outcomes.",
                    "SELECTION_TO_TRANSITION": "Intraday transition research may reweight only the frozen selected group; it may not introduce a new policy intraday.",
                    "TRANSITION_TO_INTERVENTION": "Risk-increasing action candidates require persistent confirmed state change; salience alone remains state evidence.",
                    "INTERVENTION_TO_OUTCOME": "Outcome scores the frozen decision and cost; it cannot rewrite the prior decision.",
                    "OUTCOME_TO_EVOLUTION": "Outcome may update future hypotheses and lifecycle evidence only prospectively.",
                },
            },
            "promotion_discipline": {
                "same_day_challenger_promotion_allowed": False,
                "single_day_outperformance_is_proof": False,
                "rule": (
                    "A challenger can become a future selection hypothesis only after repeated prospective "
                    "evidence across independent decisions; today outcomes are diagnostic, not retroactive evidence."
                ),
            },
        }

    def evaluate_outcome(
        self,
        snapshot: dict | None,
        strategy_realized_returns: dict[str, float],
        baseline_return: float | None,
    ) -> dict | None:
        if not snapshot:
            return None
        returns = {str(k): float(v) for k, v in strategy_realized_returns.items()}
        network = snapshot.get("policy_network") or {}
        selected_ids = [row.get("strategy_id") for row in network.get("selected") or []]
        selected_returns = [returns[sid] for sid in selected_ids if sid in returns]
        selected_mean = sum(selected_returns) / len(selected_returns) if selected_returns else None

        challenger_rows = []
        for row in network.get("challengers") or []:
            sid = str(row.get("strategy_id") or "")
            if sid not in returns:
                continue
            realized = returns[sid]
            challenger_rows.append({
                **row,
                "realized_return": realized,
                "realized_gap_vs_baseline": (
                    realized - float(baseline_return)
                    if baseline_return is not None
                    else None
                ),
                "realized_gap_vs_selected_mean": (
                    realized - selected_mean if selected_mean is not None else None
                ),
                "beats_baseline": (
                    realized > float(baseline_return)
                    if baseline_return is not None
                    else None
                ),
                "beats_selected_mean": (
                    realized > selected_mean if selected_mean is not None else None
                ),
            })

        challenger_rows.sort(
            key=lambda row: (-float(row["realized_return"]), row["strategy_id"])
        )
        return {
            "version": self.version,
            "mode": "POSTERIOR_DIAGNOSTIC_ONLY",
            "selected_mean_realized_return": selected_mean,
            "challengers_evaluated": len(challenger_rows),
            "challengers_beating_baseline": sum(
                1 for row in challenger_rows if row.get("beats_baseline") is True
            ),
            "challengers_beating_selected_mean": sum(
                1 for row in challenger_rows if row.get("beats_selected_mean") is True
            ),
            "top_realized_challengers": challenger_rows[:8],
            "interpretation_guard": (
                "A realized challenger win identifies a triage miss candidate only. "
                "It is not permission to rewrite the frozen decision or promote the strategy from one outcome."
            ),
        }
