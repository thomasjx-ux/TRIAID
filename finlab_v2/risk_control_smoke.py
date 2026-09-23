from __future__ import annotations

from triaid_fin.risk_control import CrossMarketRiskControlExperiment

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

risk_warning={
    "as_of":"2026-09-23",
    "overall":{"risk_pressure_index":45.6,"risk_band":"HIGH","risk_band_zh":"高"},
    "horizon_estimates":{
        "20":{"risk_pressure_index":37.0},
        "60":{"risk_pressure_index":43.1},
        "120":{"risk_pressure_index":51.6},
        "250":{"risk_pressure_index":49.3},
    },
    "subscores":{
        "structural":{"score_0_100":46.4},
        "rates_policy":{"score_0_100":78.0},
        "transmission":{"score_0_100":12.0},
        "credit_liquidity":{"score_0_100":25.0},
        "market_deterioration":{"score_0_100":31.0},
    },
    "confidence":{"level":"MODERATE"},
    "data_coverage":{"ratio":1.0},
    "historical_support":{"historical_event_count":4},
    "prospective_validation":{"maturity":"EARLY_UNRESOLVED"},
    "warnings":[{"code":"POLICY_REPRICING_STRESS_ACTIVE"}],
    "main_drivers":[{"id":"RATES_POLICY_PRESSURE","label_zh":"政策/利率重定价压力较高","severity":0.78}],
    "missing_confirmations":[{"id":"NO_SYSTEMIC_TRANSMISSION","label_zh":"尚未形成系统性跨市场传导"}],
    "source_status":{"term_curve_usable":True},
}
latent={
    "as_of":"2026-09-21",
    "current_state":{
        "features":{
            "US_DRAWDOWN_STRESS_252":0.0044,
            "CN_DRAWDOWN_STRESS_252":0.0690,
            "HK_DRAWDOWN_STRESS_252":0.1046,
            "US_NEGATIVE_MOMENTUM_63":-0.035,
            "CN_NEGATIVE_MOMENTUM_63":0.034,
            "HK_NEGATIVE_MOMENTUM_63":-0.047,
            "US_VOLATILITY_63":0.114,
            "CN_VOLATILITY_63":0.168,
            "HK_VOLATILITY_63":0.174,
            "US_TREASURY_2Y_LEVEL":4.76,
            "US_TREASURY_10Y_LEVEL":5.01,
            "US_TREASURY_30Y_LEVEL":5.34,
            "US_REAL_YIELD_10Y_LEVEL":2.68,
            "US_TREASURY_2Y_RISE_90D":0.60,
            "US_TREASURY_10Y_RISE_90D":0.51,
            "US_REAL_YIELD_10Y_RISE_90D":0.39,
            "FED_POLICY_RATE_LEVEL":3.88,
            "FED_FUNDS_FUTURES_IMPLIED_RATE":3.75,
            "FED_FUNDS_FUTURES_REPRICING_ABS_30D":0.12,
            "MOVE_LEVEL":81.2,
            "MOVE_RISE_30D":7.8,
            "MOVE_RISE_90D":12.42,
            "FINANCIAL_CONDITIONS_NFCI":-0.56,
            "HY_CREDIT_SPREAD_LEVEL":2.68,
            "CROSS_MARKET_STRESS_COUNT_10PCT":1,
        },
        "point_in_time_percentiles":{
            "US_NEGATIVE_MOMENTUM_63":0.57,
            "CN_NEGATIVE_MOMENTUM_63":0.70,
            "HK_NEGATIVE_MOMENTUM_63":0.36,
            "US_VOLATILITY_63":0.36,
            "CN_VOLATILITY_63":0.32,
            "HK_VOLATILITY_63":0.43,
            "MOVE_RISE_30D":0.84,
        },
        "composites":{
            "POLICY_REPRICING_STRESS":{"triggered":True,"triggered_factors":["US_TREASURY_2Y_RISE_90D","FED_FUNDS_FUTURES_REPRICING_ABS_30D","MOVE_RISE_30D"]},
            "HK_RATES_EARLY_WARNING":{"triggered":False,"triggered_factors":[]},
            "SYSTEMIC_TRANSMISSION":{"triggered":False,"triggered_factors":[]},
        },
    },
    "statistically_supported_composite_rows":[{
        "composite":"POLICY_REPRICING_STRESS","lead_trading_days":120,
        "event_hit_rate":1.0,"control_false_positive_rate":0.0833333333,
        "hit_rate_lift":0.9166666667,"fisher_p_value":0.0021884,
        "bh_q_value":0.0350148,"leave_one_event_out_min_hit_rate":1.0,
    }],
    "top_any_lead_composites":[],
}
long_cycle={
    "as_of":"2026-09-22",
    "hypotheses":{
        "stretch_vulnerability":{"state":"HIGH_STRETCH_EVIDENCE"},
        "downturn_confirmation":{"state":"NOT_CONFIRMED"},
    },
    "macro":{"HY_OAS":{"latest_value":2.68},"NFCI":{"latest_value":-0.56}},
}
cross_market={"as_of":"2026-09-22"}
policy_curve={
    "as_of":"2026-09-23",
    "data_quality":{"term_curve_usable":True,"fed_funds_contracts":15,"sofr_1m_contracts":14,"sofr_3m_contracts":8},
    "metrics":{
        "fed_funds":{"contracts":15,"front_implied_rate":3.7475,"back_implied_rate":4.6750,"front_to_back_change":0.9275},
        "sofr_1m":{"contracts":14,"front_implied_rate":3.75,"back_implied_rate":4.71,"front_to_back_change":0.96},
        "sofr_3m":{"contracts":8,"front_implied_rate":3.9975,"back_implied_rate":4.6150,"front_to_back_change":0.6175},
    },
    "fed_funds_curve":[{"contract_month":"2026-09","symbol":"ZQU26.CBT","price":96.2525,"implied_rate":3.7475}],
    "sofr_1m_curve":[{"contract_month":"2026-09","symbol":"SR1U26.CME","price":96.25,"implied_rate":3.75}],
    "sofr_3m_curve":[{"contract_month":"2026-09","symbol":"SR3U26.CME","price":96.0025,"implied_rate":3.9975}],
}
prospective={
    "as_of":"2026-09-23","ledger_id":"ledger-1","evidence_eligible":True,
    "resolved_horizons":[],"pending_horizons":[20,60,120,250],"outcomes":{},
}

exp=CrossMarketRiskControlExperiment(FakeStore())
r=exp.build(
    risk_warning=risk_warning,latent=latent,long_cycle=long_cycle,
    cross_market=cross_market,policy_curve=policy_curve,prospective=prospective,force=True,
)
assert r["experiment_type"]=="THREE_MARKET_RISK_CONTROL_SHADOW"
assert r["shadow_only"] is True
assert r["risk_control_experiment"]["production_action"]=="NONE"
assert r["risk_control_experiment"]["applied_to_weights"] is False
assert r["risk_control_experiment"]["stage"]=="WATCH_ONLY"
assert [x["market"] for x in r["three_market_state"]]==["US","CN","HK"]
assert all(x["applied_to_production"] is False for x in r["three_market_state"])
assert next(x for x in r["three_market_state"] if x["market"]=="HK")["drawdown_stress_252"]>0.10
assert len(r["dynamics_chain"])==7
assert r["dynamics_chain"][1]["state"]=="ACTIVE"
assert r["dynamics_chain"][4]["state"]=="NOT_CONFIRMED"
assert r["term_curve"]["data_quality"]["term_curve_usable"] is True
assert r["prospective_validation"]["pending_horizons"]==[20,60,120,250]
assert "not calibrated crash probabilities" in r["semantics"]["probability_guard"]

print("TRIAID_RISK_CONTROL_SMOKE_PASS",{
    "stage":r["risk_control_experiment"]["stage"],
    "markets":[(x["market"],x["risk_control_stage"]) for x in r["three_market_state"]],
    "dynamics":[(x["id"],x["state"]) for x in r["dynamics_chain"]],
})
