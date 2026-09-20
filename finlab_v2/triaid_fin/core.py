from __future__ import annotations

from .contracts import BilingualText, MarketSnapshot, StrategyGroup, TriaidDecision


class TriaidCoreModule:
    version = "triaid-core-scaffold@0.1.0"

    def decide(self, market: MarketSnapshot, group: StrategyGroup) -> TriaidDecision:
        # Architecture baseline only. It makes no intervention so later Core versions
        # can be compared against a clean, reproducible identity decision.
        reasons = {
            strategy_id: BilingualText(
                zh="架构基线 Core 不改变权重。该位置将由独立版本的 TRIAID Core 替换。",
                en="The architecture-baseline Core leaves the weight unchanged. This slot is replaceable by independently versioned TRIAID Core implementations.",
            )
            for strategy_id in group.members
        }
        return TriaidDecision(
            core_version=self.version,
            weights_before=dict(group.weights),
            weights_after=dict(group.weights),
            reasons=reasons,
        )
