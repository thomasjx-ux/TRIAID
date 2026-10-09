from types import SimpleNamespace

from triaid_fin.risk_transition import apply_fast_brake, detect_state_break, enforce_asset_concentration


def make_panel(shock: bool):
    n = 80
    spy = [100.0]
    qqq = [100.0]
    iwm = [100.0]
    for _ in range(1, n):
        spy.append(spy[-1] * 1.0010)
        qqq.append(qqq[-1] * 1.0013)
        iwm.append(iwm[-1] * 1.0008)
    if shock:
        for arr, drops in (
            (spy, (0.985, 0.978, 0.965)),
            (qqq, (0.980, 0.965, 0.940)),
            (iwm, (0.975, 0.960, 0.930)),
        ):
            start = arr[-4]
            arr[-3] = start * drops[0]
            arr[-2] = arr[-3] * drops[1]
            arr[-1] = arr[-2] * drops[2]
    spec = SimpleNamespace(benchmark="SPY", risk_assets=("SPY", "QQQ", "IWM"))
    return SimpleNamespace(spec=spec, close={"SPY": spy, "QQQ": qqq, "IWM": iwm})


def main():
    calm = detect_state_break(make_panel(False))
    assert calm["level"] == "STABLE", calm
    assert calm["risk_cap"] == 1.0, calm

    broken = detect_state_break(make_panel(True))
    assert broken["level"] in {"BRAKE", "STRONG_BRAKE"}, broken
    assert broken["risk_cap"] <= 0.70, broken
    assert broken["direction_change_allowed"] is False, broken

    braked, diag = apply_fast_brake({"P20": 0.70, "P21": 0.30}, 0.70)
    assert abs(sum(braked.values()) - 1.0) < 1e-12, braked
    assert abs(sum(v for k, v in braked.items() if k != "P28_CASH") - 0.70) < 1e-12, braked
    assert diag["applied"] is True, diag

    assets, exposure = enforce_asset_concentration(
        {"SPY": 0.30, "QQQ": 0.70, "IWM": 0.0, "TLT": 0.0, "GLD": 0.0},
        risk_assets=("SPY", "QQQ", "IWM"),
        max_single_asset_weight=0.60,
        max_risk_cluster_weight=0.90,
    )
    assert max(assets.values()) <= 0.60 + 1e-12, assets
    assert sum(assets.get(x, 0.0) for x in ("SPY", "QQQ", "IWM")) <= 0.90 + 1e-12, assets
    assert exposure["cash_residual_weight"] >= 0.10 - 1e-12, exposure
    assert exposure["hhi_after"] < exposure["hhi_before"], exposure

    print("RISK_TRANSITION_SMOKE_OK", calm["level"], broken["level"], braked, assets)


if __name__ == "__main__":
    main()
