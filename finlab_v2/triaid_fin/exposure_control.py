from __future__ import annotations

from dataclasses import dataclass


VERSION = "underlying-exposure-guard@0.1.0"

DEFAULT_CAPS = {
    "US": {"asset": 0.60, "cluster": 0.85},
    "CN": {"asset": 0.50, "cluster": 0.80},
    "HK": {"asset": 0.50, "cluster": 0.80},
}

CLUSTERS = {
    "US": {
        "SPY": "US_EQUITY_BETA",
        "QQQ": "US_EQUITY_BETA",
        "IWM": "US_EQUITY_BETA",
        "TLT": "US_DURATION",
        "GLD": "GOLD",
    },
    "CN": {
        "510300.SS": "CN_EQUITY_BETA",
        "510500.SS": "CN_EQUITY_BETA",
        "159915.SZ": "CN_EQUITY_BETA",
        "512100.SS": "CN_EQUITY_BETA",
        "511010.SS": "CN_DURATION",
    },
    "HK": {
        "2800.HK": "HK_EQUITY_BETA",
        "2828.HK": "HK_EQUITY_BETA",
        "3033.HK": "HK_EQUITY_BETA",
        "2819.HK": "HK_DURATION",
    },
}


@dataclass(frozen=True)
class ExposureGuardResult:
    scale: float
    asset_cap: float
    cluster_cap: float
    max_asset_weight_before: float
    max_cluster_weight_before: float
    asset_weights_before: dict[str, float]
    cluster_weights_before: dict[str, float]
    binding_constraints: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "version": VERSION,
            "scale": float(self.scale),
            "asset_cap": float(self.asset_cap),
            "cluster_cap": float(self.cluster_cap),
            "max_asset_weight_before": float(self.max_asset_weight_before),
            "max_cluster_weight_before": float(self.max_cluster_weight_before),
            "asset_weights_before": dict(self.asset_weights_before),
            "cluster_weights_before": dict(self.cluster_weights_before),
            "binding_constraints": list(self.binding_constraints),
            "method": "UNIFORM_RISK_SCALING_PRESERVES_STRATEGY_DIRECTION_AND_RELATIVE_MIX",
        }


def cluster_weights(market_id: str, asset_weights: dict[str, float]) -> dict[str, float]:
    market = str(market_id).upper()
    mapping = CLUSTERS.get(market, {})
    out: dict[str, float] = {}
    for asset, weight in asset_weights.items():
        value = max(0.0, float(weight))
        cluster = mapping.get(str(asset), f"ASSET:{asset}")
        out[cluster] = out.get(cluster, 0.0) + value
    return out


def assess_exposure_guard(
    market_id: str,
    asset_weights: dict[str, float],
    *,
    asset_cap: float | None = None,
    cluster_cap: float | None = None,
) -> ExposureGuardResult:
    market = str(market_id).upper()
    defaults = DEFAULT_CAPS.get(market, {"asset": 1.0, "cluster": 1.0})
    asset_limit = max(1e-9, min(1.0, float(asset_cap if asset_cap is not None else defaults["asset"])))
    cluster_limit = max(1e-9, min(1.0, float(cluster_cap if cluster_cap is not None else defaults["cluster"])))
    clean = {str(k): max(0.0, float(v)) for k, v in asset_weights.items()}
    clusters = cluster_weights(market, clean)
    max_asset = max(clean.values(), default=0.0)
    max_cluster = max(clusters.values(), default=0.0)

    scale = 1.0
    bindings: list[str] = []
    if max_asset > asset_limit + 1e-12:
        scale = min(scale, asset_limit / max_asset)
        bindings.append("UNDERLYING_ASSET_CAP")
    if max_cluster > cluster_limit + 1e-12:
        scale = min(scale, cluster_limit / max_cluster)
        bindings.append("FACTOR_CLUSTER_CAP")

    return ExposureGuardResult(
        scale=max(0.0, min(1.0, scale)),
        asset_cap=asset_limit,
        cluster_cap=cluster_limit,
        max_asset_weight_before=max_asset,
        max_cluster_weight_before=max_cluster,
        asset_weights_before=clean,
        cluster_weights_before=clusters,
        binding_constraints=tuple(bindings or ["NONE"]),
    )
