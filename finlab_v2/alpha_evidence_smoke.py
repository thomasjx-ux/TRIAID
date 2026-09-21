from triaid_fin.alpha_evidence import AlphaEvidenceLedger
from triaid_fin.store import RunStore
import tempfile

with tempfile.TemporaryDirectory() as td:
    store=RunStore(td)
    ledger=AlphaEvidenceLedger(store)
    us_dec=[{
        "decision_id":"D1","market_as_of":"2026-09-18","decision_status":"DAILY_FROZEN",
        "selected_strategy_id":"P00_BUY_HOLD",
        "fast_challenger":{"challenger_strategy_id":"P18_XMOM20"},
    }]
    us_out={
        "as_of":"2026-09-21","period_start_as_of":"2026-09-18",
        "strategy_returns":{"P00_BUY_HOLD":0.01,"P18_XMOM20":0.02},
        "product_returns":{"SPY":0.01},
    }
    x=ledger.record_us(us_dec,us_out)
    assert x["recorded"] is True
    assert abs(x["evidence"]["paired_challenger_excess"]-0.01)<1e-12
    assert ledger.record_us(us_dec,us_out)["recorded"] is False

    cn_dec=[{
        "decision_id":"C1","market_as_of":"2026-09-18","decision_status":"DAILY_FROZEN",
        "trade_opinions":[
            {"symbol":"510300.SS","target_weight":0.0},
            {"symbol":"510500.SS","target_weight":0.0},
        ],
        "soft_recovery_shadow":{
            "eligible_symbols":["510300.SS"],
            "max_shadow_probe_budget":0.05,
        },
    }]
    cn_out={
        "as_of":"2026-09-21","period_start_as_of":"2026-09-18",
        "product_returns":{"510300.SS":0.02,"510500.SS":0.01},
    }
    y=ledger.record_cn(cn_dec,cn_out)
    assert y["recorded"] is True
    assert abs(y["evidence"]["market_beta_return"]-0.015)<1e-12
    assert abs(y["evidence"]["soft_probe_return"]-0.001)<1e-12

    us_status=ledger.promotion_status("US")
    assert us_status["shadow_to_pilot_gate"]["passed"] is False
    assert "capacity_pass" in us_status["blockers"]
    assert ledger.status()["rows"]==2

print("TRIAID_ALPHA_EVIDENCE_SMOKE_PASS")
