from __future__ import annotations

from triaid_fin.risk_warning import RiskWarningSystem

class FakeStore:
    def __init__(self):
        self.json={}
        self.logs={}
    def load_json(self,name,default=None):
        return self.json.get(name,default)
    def save_json(self,name,payload):
        self.json[name]=payload
        return payload
    def read_jsonl(self,name,limit=100):
        return list(self.logs.get(name,[]))[-int(limit):]
    def append_jsonl(self,name,payload):
        self.logs.setdefault(name,[]).append(payload)
        return payload

long_cycle={
    "as_of":"2026-09-22",
    "experiment_hash":"long-hash",
    "hypotheses":{
        "stretch_vulnerability":{"support_ratio":0.714,"state":"HIGH_STRETCH_EVIDENCE"},
        "downturn_confirmation":{"support_ratio":0.0,"state":"NOT_CONFIRMED"},
    },
    "macro":{
        "HY_OAS":{"latest_value":2.7,"percentiles":{"20":{"percentile":0.25}}},
        "NFCI":{"latest_value":-0.5,"percentiles":{"20":{"percentile":0.20}}},
    },
}
latent={
    "as_of":"2026-09-21",
    "experiment_hash":"latent-hash",
    "event_count":4,
    "control_count":56,
    "statistically_supported_composite_rows":[
        {
            "composite":"POLICY_REPRICING_STRESS",
            "lead_trading_days":120,
            "event_samples":3,
            "event_hit_rate":1.0,
            "control_false_positive_rate":3/36,
            "hit_rate_lift":1.0-3/36,
            "bh_q_value":0.035,
            "leave_one_event_out_min_hit_rate":1.0,
        }
    ],
    "current_state":{
        "features":{
            "MOVE_LEVEL":81.2,
            "US_TREASURY_2Y_RISE_90D":0.6,
            "FED_FUNDS_FUTURES_REPRICING_ABS_30D":0.12,
            "CROSS_MARKET_STRESS_COUNT_10PCT":1.0,
            "US_DRAWDOWN_STRESS_252":0.005,
            "CN_DRAWDOWN_STRESS_252":0.07,
            "HK_DRAWDOWN_STRESS_252":0.105,
            "US_NEGATIVE_MOMENTUM_63":-0.02,
            "CN_NEGATIVE_MOMENTUM_63":-0.01,
            "HK_NEGATIVE_MOMENTUM_63":0.03,
        },
        "point_in_time_percentiles":{
            "MOVE_LEVEL":0.82,
            "US_TREASURY_2Y_RISE_90D":0.92,
            "FED_FUNDS_FUTURES_REPRICING_ABS_30D":0.88,
            "HY_CREDIT_SPREAD_LEVEL":0.22,
            "FINANCIAL_CONDITIONS_NFCI":0.18,
            "HK_BRIDGE_DIFFERENTIAL_60":0.45,
            "CORR_HK_US_60":0.40,
            "US_NEGATIVE_MOMENTUM_63":0.20,
            "CN_NEGATIVE_MOMENTUM_63":0.35,
            "HK_NEGATIVE_MOMENTUM_63":0.42,
        },
        "composites":{
            "POLICY_REPRICING_STRESS":{
                "triggered":True,
                "historically_statistically_supported":True,
                "score":0.75,
                "alert_count":3,
                "min_hits":2,
                "triggered_factors":["US_TREASURY_2Y_RISE_90D","FED_FUNDS_FUTURES_REPRICING_ABS_30D","MOVE_RISE_30D"],
            },
            "RATES_POLICY_PRESSURE":{"triggered":False,"score":0.60},
            "SYSTEMIC_TRANSMISSION":{"triggered":False,"score":0.25},
            "HK_RATES_EARLY_WARNING":{"triggered":False,"score":0.45},
        },
    },
}
cross_market={"as_of":"2026-09-22","experiment_hash":"cross-hash"}
policy_curve={
    "as_of":"2026-09-22",
    "snapshot_hash":"curve-hash",
    "data_quality":{"term_curve_usable":True,"fed_funds_contracts":15,"sofr_1m_contracts":14,"sofr_3m_contracts":8},
    "metrics":{
        "fed_funds":{"available":True,"contracts":15,"front_implied_rate":3.75,"back_implied_rate":4.67,"front_to_back_change":0.92},
        "sofr_1m":{"available":True,"contracts":14,"front_implied_rate":3.75,"back_implied_rate":4.74,"front_to_back_change":0.99},
        "sofr_3m":{"available":True,"contracts":8,"front_implied_rate":4.00,"back_implied_rate":4.66,"front_to_back_change":0.66},
    },
}
prospective={
    "as_of":"2026-09-22",
    "ledger_id":"shadow-1",
    "evidence_eligible":True,
    "hazard_signal_as_of":"2026-09-21",
    "policy_curve_as_of":"2026-09-22",
    "outcomes":{},
    "pending_horizons":[20,60,120,250],
}

rw=RiskWarningSystem(FakeStore())
report=rw.build(
    long_cycle=long_cycle,
    latent=latent,
    cross_market=cross_market,
    policy_curve=policy_curve,
    prospective=prospective,
    force=True,
)
assert report["warning_type"]=="TRIAID_RISK_WARNING"
assert 0<=report["overall"]["risk_pressure_index"]<=100
assert set(report["horizon_estimates"])=={"20","60","120","250"}
assert report["horizon_estimates"]["120"]["risk_pressure_index"]>=report["horizon_estimates"]["20"]["risk_pressure_index"]
assert report["confidence"]["level"]=="MODERATE"
assert report["prospective_validation"]["maturity"]=="EARLY_UNRESOLVED"
assert any(x["code"]=="POLICY_REPRICING_STRESS_ACTIVE" for x in report["warnings"])
assert "not a calibrated crash probability" in report["semantics"]["risk_pressure_index"]
assert report["applied_to_weights"] is False
assert report["production_action"]=="NONE"

print("TRIAID_RISK_WARNING_SMOKE_PASS",{
    "overall":report["overall"],
    "horizons":{k:v["risk_pressure_index"] for k,v in report["horizon_estimates"].items()},
    "confidence":report["confidence"],
})
