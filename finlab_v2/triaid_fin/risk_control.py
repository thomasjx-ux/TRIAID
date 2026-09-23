from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .store import RunStore


class CrossMarketRiskControlExperiment:
    version="cross-market-risk-control@0.1.0"
    latest_file="cross_market_risk_control_latest.json"
    history_file="cross_market_risk_control_history.jsonl"

    def __init__(self,store:RunStore)->None:
        self.store=store

    @staticmethod
    def _canonical(payload:dict)->str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _clamp01(value)->float:
        try:
            return min(1.0,max(0.0,float(value)))
        except Exception:
            return 0.0

    @classmethod
    def _market_row(cls,market:str,current:dict,overall_risk:dict)->dict:
        features=current.get("features") or {}
        pcts=current.get("point_in_time_percentiles") or {}
        dd=float(features.get(f"{market}_DRAWDOWN_STRESS_252") or 0.0)
        mom_value=features.get(f"{market}_NEGATIVE_MOMENTUM_63")
        mom_pct=pcts.get(f"{market}_NEGATIVE_MOMENTUM_63")
        vol=features.get(f"{market}_VOLATILITY_63")
        vol_pct=pcts.get(f"{market}_VOLATILITY_63")
        risk_score=float((overall_risk.get("overall") or {}).get("risk_pressure_index") or 0.0)
        systemic=bool(
            ((current.get("composites") or {}).get("SYSTEMIC_TRANSMISSION") or {}).get("triggered")
        )
        if risk_score>=80 and systemic:
            stage="SHADOW_DEFENSIVE_BIAS"
            risky_multiplier=0.65
            defensive_floor=0.35
        elif risk_score>=65 or (systemic and (dd>=0.10 or cls._clamp01(mom_pct)>=0.80)):
            stage="SHADOW_TIGHTEN_RISK_CONSTRAINTS"
            risky_multiplier=0.85
            defensive_floor=0.15
        elif risk_score>=45 or dd>=0.10 or cls._clamp01(mom_pct)>=0.80:
            stage="WATCH_ONLY"
            risky_multiplier=1.00
            defensive_floor=0.00
        else:
            stage="NORMAL_OBSERVATION"
            risky_multiplier=1.00
            defensive_floor=0.00
        return {
            "market":market,
            "drawdown_stress_252":dd,
            "negative_momentum_63":mom_value,
            "negative_momentum_percentile":mom_pct,
            "volatility_63":vol,
            "volatility_percentile":vol_pct,
            "risk_control_stage":stage,
            "shadow_candidate_constraints":{
                "risky_exposure_multiplier":risky_multiplier,
                "defensive_exposure_floor":defensive_floor,
            },
            "applied_to_production":False,
        }

    @staticmethod
    def _chain_step(step_id:str,label_zh:str,state:str,score:float|None,evidence:list[str])->dict:
        return {
            "id":step_id,
            "label_zh":label_zh,
            "state":state,
            "score":score,
            "evidence":evidence,
        }

    @classmethod
    def _dynamics_chain(cls,risk_warning:dict,latent:dict,long_cycle:dict)->list[dict]:
        current=latent.get("current_state") or {}
        comps=current.get("composites") or {}
        subs=risk_warning.get("subscores") or {}
        hypotheses=long_cycle.get("hypotheses") or {}
        structural=(hypotheses.get("stretch_vulnerability") or {}).get("state") or "UNKNOWN"
        downturn=(hypotheses.get("downturn_confirmation") or {}).get("state") or "UNKNOWN"
        policy=comps.get("POLICY_REPRICING_STRESS") or {}
        hk=comps.get("HK_RATES_EARLY_WARNING") or {}
        systemic=comps.get("SYSTEMIC_TRANSMISSION") or {}
        return [
            cls._chain_step(
                "STRUCTURAL_VULNERABILITY","长期结构脆弱",
                structural,
                ((subs.get("structural") or {}).get("score_0_100")),
                [f"stretch={structural}",f"secular={downturn}"],
            ),
            cls._chain_step(
                "POLICY_REPRICING","政策/利率重新定价",
                "ACTIVE" if policy.get("triggered") else "INACTIVE",
                ((subs.get("rates_policy") or {}).get("score_0_100")),
                list(policy.get("triggered_factors") or []),
            ),
            cls._chain_step(
                "BOND_VOLATILITY","美债波动",
                "ELEVATED" if float((current.get("point_in_time_percentiles") or {}).get("MOVE_RISE_30D") or 0.0)>=0.80 else "NORMAL",
                (current.get("point_in_time_percentiles") or {}).get("MOVE_RISE_30D"),
                [
                    f"MOVE={(current.get('features') or {}).get('MOVE_LEVEL')}",
                    f"MOVE_30D={(current.get('features') or {}).get('MOVE_RISE_30D')}",
                ],
            ),
            cls._chain_step(
                "HK_BRIDGE","港股桥梁/早期传导",
                "ACTIVE" if hk.get("triggered") else "NOT_CONFIRMED",
                ((subs.get("transmission") or {}).get("score_0_100")),
                list(hk.get("triggered_factors") or []),
            ),
            cls._chain_step(
                "SYSTEMIC_TRANSMISSION","三市场系统性传导",
                "ACTIVE" if systemic.get("triggered") else "NOT_CONFIRMED",
                ((subs.get("transmission") or {}).get("score_0_100")),
                list(systemic.get("triggered_factors") or []),
            ),
            cls._chain_step(
                "CREDIT_LIQUIDITY","信用/流动性确认",
                "STRESSED" if float(((subs.get("credit_liquidity") or {}).get("score_0_100")) or 0.0)>=50 else "NOT_CONFIRMED",
                ((subs.get("credit_liquidity") or {}).get("score_0_100")),
                ["HY_OAS","NFCI"],
            ),
            cls._chain_step(
                "PRICE_CONFIRMATION","价格层确认",
                "DETERIORATING" if float(((subs.get("market_deterioration") or {}).get("score_0_100")) or 0.0)>=50 else "PARTIAL",
                ((subs.get("market_deterioration") or {}).get("score_0_100")),
                ["US/CN/HK drawdown","US/CN/HK 63d momentum"],
            ),
        ]

    @staticmethod
    def _compact_term_curve(policy_curve:dict)->dict:
        def rows(name:str)->list[dict]:
            key={
                "fed_funds":"fed_funds_curve",
                "sofr_1m":"sofr_1m_curve",
                "sofr_3m":"sofr_3m_curve",
            }[name]
            return [
                {
                    "contract_month":x.get("contract_month"),
                    "symbol":x.get("symbol"),
                    "price":x.get("price"),
                    "implied_rate":x.get("implied_rate"),
                }
                for x in (policy_curve.get(key) or [])
            ]
        return {
            "data_quality":policy_curve.get("data_quality") or {},
            "metrics":policy_curve.get("metrics") or {},
            "fed_funds":rows("fed_funds"),
            "sofr_1m":rows("sofr_1m"),
            "sofr_3m":rows("sofr_3m"),
        }

    @staticmethod
    def _macro_snapshot(latent:dict,long_cycle:dict)->dict:
        current=latent.get("current_state") or {}
        f=current.get("features") or {}
        p=current.get("point_in_time_percentiles") or {}
        macro=long_cycle.get("macro") or {}
        keys=[
            "US_TREASURY_2Y_LEVEL","US_TREASURY_10Y_LEVEL","US_TREASURY_30Y_LEVEL",
            "US_REAL_YIELD_10Y_LEVEL","US_TREASURY_2Y_RISE_90D","US_TREASURY_10Y_RISE_90D",
            "US_REAL_YIELD_10Y_RISE_90D","FED_POLICY_RATE_LEVEL",
            "FED_FUNDS_FUTURES_IMPLIED_RATE","FED_FUNDS_FUTURES_REPRICING_ABS_30D",
            "MOVE_LEVEL","MOVE_RISE_30D","MOVE_RISE_90D",
            "YIELD_CURVE_INVERSION","YIELD_CURVE_10Y3M_INVERSION",
            "FINANCIAL_CONDITIONS_NFCI","HY_CREDIT_SPREAD_LEVEL",
        ]
        out={}
        for key in keys:
            out[key]={
                "value":f.get(key),
                "point_in_time_percentile":p.get(key),
            }
        out["HY_OAS_LONG_CYCLE"]={
            "value":(macro.get("HY_OAS") or {}).get("latest_value"),
            "percentiles":(macro.get("HY_OAS") or {}).get("percentiles"),
        }
        out["NFCI_LONG_CYCLE"]={
            "value":(macro.get("NFCI") or {}).get("latest_value"),
            "percentiles":(macro.get("NFCI") or {}).get("percentiles"),
        }
        return out

    @classmethod
    def _experiment_stage(cls,risk_warning:dict,market_rows:list[dict],latent:dict)->dict:
        overall=float((risk_warning.get("overall") or {}).get("risk_pressure_index") or 0.0)
        current=latent.get("current_state") or {}
        systemic=bool(((current.get("composites") or {}).get("SYSTEMIC_TRANSMISSION") or {}).get("triggered"))
        stages=[x["risk_control_stage"] for x in market_rows]
        if "SHADOW_DEFENSIVE_BIAS" in stages:
            stage="SHADOW_DEFENSIVE_BIAS"
        elif "SHADOW_TIGHTEN_RISK_CONSTRAINTS" in stages:
            stage="SHADOW_TIGHTEN_RISK_CONSTRAINTS"
        elif "WATCH_ONLY" in stages:
            stage="WATCH_ONLY"
        else:
            stage="NORMAL_OBSERVATION"
        return {
            "stage":stage,
            "overall_risk_pressure_index":overall,
            "systemic_transmission":systemic,
            "production_action":"NONE",
            "applied_to_weights":False,
            "objective_guard":"Risk is a feasibility/evidence layer. Maximum realizable net return remains the sole optimization objective.",
            "promotion_gate":{
                "prospective_validation_required":True,
                "must_show_positive_net_benefit_after_cost":True,
                "must_not_use_hindsight":True,
                "market_stratified_US_CN_HK_required":True,
            },
        }

    def build(
        self,
        *,
        risk_warning:dict|None,
        latent:dict|None,
        long_cycle:dict|None,
        cross_market:dict|None,
        policy_curve:dict|None,
        prospective:dict|None,
        force:bool=False,
    )->dict:
        risk_warning=risk_warning or {}
        latent=latent or {}
        long_cycle=long_cycle or {}
        cross_market=cross_market or {}
        policy_curve=policy_curve or {}
        prospective=prospective or {}
        current=latent.get("current_state") or {}

        market_rows=[self._market_row(m,current,risk_warning) for m in ("US","CN","HK")]
        payload={
            "version":self.version,
            "as_of":max(
                [str(x) for x in (
                    risk_warning.get("as_of"),latent.get("as_of"),long_cycle.get("as_of"),
                    cross_market.get("as_of"),policy_curve.get("as_of"),prospective.get("as_of"),
                ) if x] or [datetime.now(timezone.utc).date().isoformat()]
            ),
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "experiment_type":"THREE_MARKET_RISK_CONTROL_SHADOW",
            "shadow_only":True,
            "risk_estimate":{
                "overall":risk_warning.get("overall") or {},
                "horizon_estimates":risk_warning.get("horizon_estimates") or {},
                "subscores":risk_warning.get("subscores") or {},
                "confidence":risk_warning.get("confidence") or {},
                "data_coverage":risk_warning.get("data_coverage") or {},
            },
            "three_market_state":market_rows,
            "dynamics_chain":self._dynamics_chain(risk_warning,latent,long_cycle),
            "rates_policy_credit_snapshot":self._macro_snapshot(latent,long_cycle),
            "term_curve":self._compact_term_curve(policy_curve),
            "historical_validation":{
                "historical_support":risk_warning.get("historical_support") or {},
                "statistically_supported_composites":latent.get("statistically_supported_composite_rows") or [],
                "candidate_composites":latent.get("top_any_lead_composites") or [],
            },
            "prospective_validation":{
                "risk_warning":risk_warning.get("prospective_validation") or {},
                "ledger_id":prospective.get("ledger_id"),
                "evidence_eligible":prospective.get("evidence_eligible"),
                "resolved_horizons":prospective.get("resolved_horizons") or [],
                "pending_horizons":prospective.get("pending_horizons") or [],
                "outcomes":prospective.get("outcomes") or {},
            },
            "risk_control_experiment":self._experiment_stage(risk_warning,market_rows,latent),
            "warnings":risk_warning.get("warnings") or [],
            "main_drivers":risk_warning.get("main_drivers") or [],
            "missing_confirmations":risk_warning.get("missing_confirmations") or [],
            "escalation_conditions":risk_warning.get("escalation_conditions") or [],
            "deescalation_conditions":risk_warning.get("deescalation_conditions") or [],
            "source_status":risk_warning.get("source_status") or {},
            "semantics":{
                "risk_center":"A cross-market research surface, not a fourth market.",
                "risk_control":"Hypothetical risk-constraint candidates are shadow-only until prospective net-benefit validation passes.",
                "probability_guard":"Risk pressure scores are not calibrated crash probabilities.",
            },
        }
        digest=self._hash(payload)
        payload["experiment_hash"]=digest
        payload["experiment_id"]=f"RISK-CONTROL-{payload['as_of']}-{digest[:10]}"
        previous=self.latest()
        if (
            previous
            and previous.get("as_of")==payload["as_of"]
            and previous.get("experiment_hash")==payload["experiment_hash"]
            and not force
        ):
            return previous
        self.store.save_json(self.latest_file,payload)
        history=self.history(5000)
        if not any(x.get("experiment_id")==payload["experiment_id"] for x in history):
            self.store.append_jsonl(self.history_file,payload)
        return payload

    def latest(self)->dict|None:
        row=self.store.load_json(self.latest_file,default={})
        return row or None

    def history(self,limit:int=100)->list[dict]:
        return self.store.read_jsonl(self.history_file,limit=limit)

    def status(self)->dict:
        latest=self.latest()
        return {
            "version":self.version,
            "latest_experiment_id":latest.get("experiment_id") if latest else None,
            "latest_as_of":latest.get("as_of") if latest else None,
            "stage":((latest.get("risk_control_experiment") or {}).get("stage")) if latest else None,
            "risk_estimate":(latest.get("risk_estimate") or {}) if latest else None,
        }
