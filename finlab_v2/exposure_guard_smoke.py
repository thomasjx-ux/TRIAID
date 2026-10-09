from __future__ import annotations

from triaid_fin.exposure_control import assess_exposure_guard, cluster_weights


def main() -> None:
    # Regression for the 2026-10 US case: several strategies looked diversified,
    # but their final exposure collapsed into 70% QQQ + 30% SPY.
    before = {"QQQ": 0.70, "SPY": 0.30, "IWM": 0.0, "TLT": 0.0, "GLD": 0.0}
    guard = assess_exposure_guard("US", before)
    assert guard.scale < 1.0, guard
    assert "FACTOR_CLUSTER_CAP" in guard.binding_constraints, guard

    after = {asset: weight * guard.scale for asset, weight in before.items()}
    clusters = cluster_weights("US", after)
    assert max(after.values()) <= guard.asset_cap + 1e-12, (after, guard)
    assert max(clusters.values()) <= guard.cluster_cap + 1e-12, (clusters, guard)
    assert abs(clusters["US_EQUITY_BETA"] - 0.85) <= 1e-12, clusters

    balanced = {"QQQ": 0.40, "TLT": 0.30, "GLD": 0.30}
    stable = assess_exposure_guard("US", balanced)
    assert stable.scale == 1.0, stable
    assert stable.binding_constraints == ("NONE",), stable

    print("EXPOSURE_GUARD_SMOKE_OK", guard.to_dict(), stable.to_dict())


if __name__ == "__main__":
    main()
