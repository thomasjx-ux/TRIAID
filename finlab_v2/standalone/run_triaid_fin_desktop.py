from __future__ import annotations

"""Final TRIAID FIN desktop entrypoint for Adaptive V3.6.

The V3.6 adaptive runtime remains detachable from the legacy core. This thin
orchestrator adds post-decision shadow evaluation without modifying historical
pre-open action plans.
"""

from pathlib import Path

import run_triaid_fin_v36 as runtime
from action_evaluation_runner_v36 import evaluate_market_actions

VERSION = "triaid-fin-desktop@0.3.0-v36"

_ORIGINAL_RUN_ADAPTIVE_MARKET = runtime.run_adaptive_market


def _run_adaptive_market_with_evaluation(store, config: dict, market: str):
    result = _ORIGINAL_RUN_ADAPTIVE_MARKET(store, config, market)
    phase = str(result.get("phase") or "")
    trading_date = result.get("trading_date")
    if trading_date and phase in {"OPEN", "POSTCLOSE"}:
        try:
            data_dir, _ = runtime.base_runtime.ensure_local_storage(config)
            result["action_evaluation"] = evaluate_market_actions(
                data_dir=Path(data_dir),
                market=market,
                trading_date=str(trading_date),
                phase=phase,
                series_fetcher=runtime._fetch_series,
            )
        except Exception as exc:
            result["action_evaluation"] = {
                "status": "EVALUATION_DEGRADED",
                "error": f"{type(exc).__name__}:{exc}",
                "core_impact": "NONE",
            }
    return result


runtime.run_adaptive_market = _run_adaptive_market_with_evaluation


def main() -> int:
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
