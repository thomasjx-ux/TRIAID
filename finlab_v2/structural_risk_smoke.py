from __future__ import annotations

from triaid_fin.structural_risk import build_cross_sectional_risk_brake


def _trend(start: float, days: int, step: float) -> list[float]:
    return [start + step * i for i in range(days)]


def main() -> None:
    class Spec:
        benchmark = "SPY"
        risk_assets = ("SPY", "QQQ", "IWM")

    class StablePanel:
        spec = Spec()
        close = {
            "SPY": _trend(100.0, 30, 0.10),
            "QQQ": _trend(100.0, 30, 0.12),
            "IWM": _trend(100.0, 30, 0.08),
        }

    stable = build_cross_sectional_risk_brake(StablePanel())
    assert stable["status"] == "STABLE", stable
    assert stable["risk_cap"] == 1.0, stable

    spy = _trend(100.0, 29, 0.10)
    qqq = _trend(100.0, 29, 0.12)
    iwm = _trend(100.0, 29, 0.08)
    spy.append(spy[-1] * 0.995)
    qqq.append(qqq[-1] * 0.96)
    iwm.append(iwm[-1] * 1.001)

    class ConcentratedSelloffPanel:
        spec = Spec()
        close = {"SPY": spy, "QQQ": qqq, "IWM": iwm}

    watch = build_cross_sectional_risk_brake(ConcentratedSelloffPanel())
    assert watch["status"] == "STRUCTURAL_WATCH", watch
    assert abs(watch["risk_cap"] - 0.85) < 1e-12, watch
    assert watch["worst_risk_asset"] == "QQQ", watch
    assert watch["scores"]["downside_gap"] >= 0.65, watch
    assert watch["scores"]["worst_asset_shock"] >= 0.75, watch
    assert watch["direction_change_allowed"] is False
    assert watch["outcome_specific_retuning"] is False

    partial_spy = _trend(100.0, 30, 0.10) + [80.0]
    partial_qqq = _trend(100.0, 30, 0.12) + [60.0]
    partial_iwm = _trend(100.0, 30, 0.08) + [90.0]

    class OpenPartialPanel:
        spec = Spec()
        close = {"SPY": partial_spy, "QQQ": partial_qqq, "IWM": partial_iwm}

    open_state = build_cross_sectional_risk_brake(OpenPartialPanel(), input_phase="OPEN")
    assert open_state["status"] == "STABLE", open_state
    assert open_state["risk_cap"] == 1.0, open_state

    print("structural_risk_smoke: PASS")


if __name__ == "__main__":
    main()
