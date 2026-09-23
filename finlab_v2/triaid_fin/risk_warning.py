from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .store import RunStore
from .market_registry import market_ids


HORIZON_WEIGHTS={
    "20":{
        "structural":0.05,
        "rates_policy":0.15,
        "transmission":0.30,
        "credit_liquidity":0.20,
        "market_deterioration":0.30,
    },
    "60":{
        "structural":0.10,
        "rates_policy":0.25,
        "transmission":0.25,
        "credit_liquidity":0.20,
        "market_deterioration":0.20,
    },
    "120":{
        "structural":0.25,
        "rates_policy":0.35,
        "transmission":0.15,
        "credit_liquidity":0.15,
        "market_deterioration":0.10,
    },
    "250":{
        "structural":0.40,
        "rates_policy":0.25,
        "transmission":0.10,
        "credit_liquidity":0.15,
        "market_deterioration":0.10,
    },
}


class RiskWarningSystem:
    version="risk-warning@0.2.0"
    latest_file="risk_warning_latest.json"
    history_file="risk_warning_history.jsonl"

    def __init__(self,store:RunStore)->None:
        self.store=store

    @staticmethod
    def _canonical(payload:dict)->str:
        return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)

    @classmethod
    def _hash(cls,payload:dict)->str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _clamp01(value:float|int|None)->float:
        if value is None:
            return 0.0
        try:
            x=float(value)
        except Exception:
            return 0.0
        return min(1.0,max(0.0,x))

    @staticmethod
    def _risk_band(score:float)->str:
        s=float(score)
        if s>=80:
            return "CRITICAL"
        if s>=65:
            return "SEVERE"
        if s>=45:
            return "HIGH"
        if s>=25:
            return "ELEVATED"
        return "LOW"

    @staticmethod
    def _risk_band_zh(band:str)->str:
        return {
            "LOW":"低",
            "ELEVATED":"升高",
            "HIGH":"高",
            "SEVERE":"严重",
            "CRITICAL":"临界",
        }.get(str(band),str(band))

    @staticmethod
    def _percentile(macro:dict,name:str,years:int=20)->float|None:
        value=(((macro.get(name) or {}).get("percentiles") or {}).get(str(years)) or {}).get("percentile")
        return float(value) if value is not None else None

    @staticmethod
    def _composite(current:dict,name:str)->dict:
        return ((current.get("composites") or {}).get(name) or {})

    @staticmethod
    def _factor_pct(current:dict,name:str)->float|None:
        value=(current.get("point_in_time_percentiles") or {}).get(name)
        return float(value) if value is not None else None

    @classmethod
    def _structural_score(cls,long_cycle:dict)->tuple[float,list[dict]]:
        hypotheses=long_cycle.get("hypotheses") or {}
        stretch=hypotheses.get("stretch_vulnerability") or {}
        downturn=hypotheses.get("downturn_confirmation") or {}
        stretch_ratio=cls._clamp01(stretch.get("support_ratio"))
        downturn_ratio=cls._clamp01(downturn.get("support_ratio"))
        score=0.65*stretch_ratio+0.35*downturn_ratio
        drivers=[
            {
                "id":"LONG_RUN_STRETCH",
                "label_zh":"长期回报拉伸",
                "value":stretch_ratio,
                "state":stretch.get("state"),
                "role":"vulnerability",
            },
            {
                "id":"SECULAR_DOWNTURN_CONFIRMATION",
                "label_zh":"多年熊市确认",
                "value":downturn_ratio,
                "state":downturn.get("state"),
                "role":"confirmation",
            },
        ]
        return cls._clamp01(score),drivers

    @classmethod
    def _rates_policy_score(cls,latent:dict,long_cycle:dict,policy_curve:dict)->tuple[float,list[dict]]:
        current=latent.get("current_state") or {}
        policy=cls._composite(current,"POLICY_REPRICING_STRESS")
        pressure=cls._composite(current,"RATES_POLICY_PRESSURE")
        pscore=float(policy.get("score") or 0.0)
        rscore=float(pressure.get("score") or 0.0)
        historically_supported=bool(policy.get("historically_statistically_supported"))
        policy_triggered=bool(policy.get("triggered"))
        move_pct=cls._factor_pct(current,"MOVE_LEVEL")
        two_y_rise=cls._factor_pct(current,"US_TREASURY_2Y_RISE_90D")
        futures_repricing=cls._factor_pct(current,"FED_FUNDS_FUTURES_REPRICING_ABS_30D")
        raw=[
            x for x in (move_pct,two_y_rise,futures_repricing)
            if x is not None
        ]
        factor_pressure=(sum(cls._clamp01(x) for x in raw)/len(raw)) if raw else 0.0
        curve_usable=bool((policy_curve.get("data_quality") or {}).get("term_curve_usable"))
        score=(
            0.35*cls._clamp01(pscore)
            +0.20*cls._clamp01(rscore)
            +0.30*cls._clamp01(factor_pressure)
            +0.15*(1.0 if policy_triggered and historically_supported else 0.0)
        )
        if not curve_usable:
            score*=0.90
        drivers=[
            {
                "id":"POLICY_REPRICING_STRESS",
                "label_zh":"政策重定价联合状态",
                "triggered":policy_triggered,
                "historically_statistically_supported":historically_supported,
                "score":pscore,
                "alert_count":policy.get("alert_count"),
                "min_hits":policy.get("min_hits"),
                "triggered_factors":policy.get("triggered_factors") or [],
            },
            {
                "id":"MOVE_LEVEL",
                "label_zh":"美债波动率 MOVE",
                "percentile":move_pct,
                "value":(current.get("features") or {}).get("MOVE_LEVEL"),
            },
            {
                "id":"US_TREASURY_2Y_RISE_90D",
                "label_zh":"2Y美债90日上行速度",
                "percentile":two_y_rise,
                "value":(current.get("features") or {}).get("US_TREASURY_2Y_RISE_90D"),
            },
            {
                "id":"FED_FUNDS_FUTURES_REPRICING_ABS_30D",
                "label_zh":"Fed Funds期货30日重定价",
                "percentile":futures_repricing,
                "value":(current.get("features") or {}).get("FED_FUNDS_FUTURES_REPRICING_ABS_30D"),
            },
            {
                "id":"POLICY_TERM_CURVE",
                "label_zh":"政策利率期限曲线",
                "usable":curve_usable,
                "data_quality":policy_curve.get("data_quality") or {},
                "metrics":policy_curve.get("metrics") or {},
            },
        ]
        return cls._clamp01(score),drivers

    @classmethod
    def _transmission_score(cls,latent:dict)->tuple[float,list[dict]]:
        current=latent.get("current_state") or {}
        systemic=cls._composite(current,"SYSTEMIC_TRANSMISSION")
        hk_rates=cls._composite(current,"HK_RATES_EARLY_WARNING")
        bridge=cls._factor_pct(current,"HK_BRIDGE_DIFFERENTIAL_60")
        hk_us=cls._factor_pct(current,"CORR_HK_US_60")
        corr_mean=cls._factor_pct(current,"CROSS_MARKET_CORR_MEAN_60")
        features=current.get("features") or {}
        stress=features.get("CROSS_MARKET_STRESS_COUNT_10PCT")
        stress_share=features.get("CROSS_MARKET_STRESS_SHARE_10PCT")
        if stress_share is None:
            available_market_count=max(
                1,
                sum(1 for key in features if str(key).endswith("_DRAWDOWN_STRESS_252")),
            )
            stress_ratio=cls._clamp01(float(stress or 0.0)/available_market_count)
        else:
            stress_ratio=cls._clamp01(stress_share)
        generic_link=max(cls._clamp01(corr_mean),cls._clamp01(hk_us),cls._clamp01(bridge))
        base=(
            0.50*cls._clamp01(systemic.get("score"))
            +0.20*stress_ratio
            +0.15*generic_link
            +0.15*cls._clamp01(hk_rates.get("score"))
        )
        if bool(systemic.get("triggered")):
            base=max(base,0.80)
        drivers=[
            {
                "id":"SYSTEMIC_TRANSMISSION",
                "label_zh":"系统性跨市场传导",
                "triggered":bool(systemic.get("triggered")),
                "score":systemic.get("score"),
                "triggered_factors":systemic.get("triggered_factors") or [],
            },
            {
                "id":"HK_RATES_EARLY_WARNING",
                "label_zh":"港股—利率早期预警",
                "triggered":bool(hk_rates.get("triggered")),
                "score":hk_rates.get("score"),
                "triggered_factors":hk_rates.get("triggered_factors") or [],
            },
            {
                "id":"CROSS_MARKET_STRESS_SHARE_10PCT",
                "label_zh":"跨市场10%压力占比",
                "value":stress_share,
                "count":stress,
                "normalized":stress_ratio,
            },
            {
                "id":"CROSS_MARKET_CORR_MEAN_60",
                "label_zh":"跨市场60日平均相关性",
                "percentile":corr_mean,
                "value":features.get("CROSS_MARKET_CORR_MEAN_60"),
            },
            {
                "id":"HK_BRIDGE_DIFFERENTIAL_60",
                "label_zh":"港股桥梁效应",
                "percentile":bridge,
                "value":(current.get("features") or {}).get("HK_BRIDGE_DIFFERENTIAL_60"),
            },
        ]
        return cls._clamp01(base),drivers

    @classmethod
    def _credit_liquidity_score(cls,long_cycle:dict,latent:dict)->tuple[float,list[dict]]:
        macro=long_cycle.get("macro") or {}
        hy=cls._percentile(macro,"HY_OAS",20)
        nfci=cls._percentile(macro,"NFCI",20)
        current=latent.get("current_state") or {}
        hy_current=cls._factor_pct(current,"HY_CREDIT_SPREAD_LEVEL")
        nfci_current=cls._factor_pct(current,"FINANCIAL_CONDITIONS_NFCI")
        values=[x for x in (hy,nfci,hy_current,nfci_current) if x is not None]
        score=sum(cls._clamp01(x) for x in values)/len(values) if values else 0.0
        drivers=[
            {
                "id":"HY_OAS",
                "label_zh":"高收益信用利差",
                "percentile_20y":hy,
                "latest":(macro.get("HY_OAS") or {}).get("latest_value"),
                "point_in_time_percentile":hy_current,
            },
            {
                "id":"NFCI",
                "label_zh":"金融条件指数 NFCI",
                "percentile_20y":nfci,
                "latest":(macro.get("NFCI") or {}).get("latest_value"),
                "point_in_time_percentile":nfci_current,
            },
        ]
        return cls._clamp01(score),drivers

    @classmethod
    def _market_deterioration_score(cls,latent:dict)->tuple[float,list[dict]]:
        current=latent.get("current_state") or {}
        features=current.get("features") or {}
        pcts=current.get("point_in_time_percentiles") or {}
        dds=[]
        momentum=[]
        drivers=[]
        for market in market_ids():
            dd_name=f"{market}_DRAWDOWN_STRESS_252"
            mom_name=f"{market}_NEGATIVE_MOMENTUM_63"
            if dd_name not in features and mom_name not in features and mom_name not in pcts:
                continue
            dd=float(features.get(dd_name) or 0.0)
            mom=float(pcts.get(mom_name) or 0.0)
            dds.append(cls._clamp01(dd/0.20))
            momentum.append(cls._clamp01(mom))
            drivers.append({
                "id":dd_name,
                "label_zh":f"{market} 252日回撤压力",
                "value":dd,
                "normalized_to_20pct":cls._clamp01(dd/0.20),
            })
            drivers.append({
                "id":mom_name,
                "label_zh":f"{market} 63日负动量异常",
                "percentile":pcts.get(mom_name),
                "value":features.get(mom_name),
            })
        if not dds:
            return 0.0,drivers
        score=0.60*(sum(dds)/len(dds))+0.40*(sum(momentum)/len(momentum))
        return cls._clamp01(score),drivers

    @staticmethod
    def _historical_support(latent:dict)->dict:
        supported=latent.get("statistically_supported_composite_rows") or []
        policy=[
            x for x in supported
            if x.get("composite")=="POLICY_REPRICING_STRESS"
        ]
        best=min(policy,key=lambda x:float(x.get("bh_q_value") or 1.0),default=None)
        return {
            "supported_composite_count":len(supported),
            "policy_repricing_best":best,
            "historical_event_count":latent.get("event_count"),
            "policy_repricing_event_samples":best.get("event_samples") if best else None,
            "policy_repricing_control_samples":best.get("control_samples") if best else None,
            "normal_control_count":latent.get("control_count"),
        }

    @staticmethod
    def _prospective_validation(prospective:dict)->dict:
        outcomes=prospective.get("outcomes") or {}
        resolved=sorted(int(k) for k in outcomes if str(k).isdigit())
        return {
            "ledger_id":prospective.get("ledger_id"),
            "evidence_eligible":prospective.get("evidence_eligible"),
            "hazard_signal_as_of":prospective.get("hazard_signal_as_of"),
            "policy_curve_as_of":prospective.get("policy_curve_as_of"),
            "resolved_horizons":resolved,
            "pending_horizons":prospective.get("pending_horizons") or [],
            "maturity":(
                "MATURE"
                if len(resolved)>=4
                else "PARTIAL"
                if resolved
                else "EARLY_UNRESOLVED"
            ),
        }

    @classmethod
    def _confidence(cls,historical:dict,prospective:dict)->dict:
        best=historical.get("policy_repricing_best") or {}
        total_event_count=int(historical.get("historical_event_count") or 0)
        tested_event_count=int(historical.get("policy_repricing_event_samples") or 0)
        q=best.get("bh_q_value")
        prospective_maturity=prospective.get("maturity")
        if tested_event_count>=8 and q is not None and float(q)<=0.05 and prospective_maturity=="MATURE":
            level="HIGH"
        elif tested_event_count>=3 and q is not None and float(q)<=0.10:
            level="MODERATE"
        else:
            level="LOW"
        return {
            "level":level,
            "historical_crisis_samples_total":total_event_count,
            "historical_crisis_samples_tested":tested_event_count,
            "best_supported_q_value":q,
            "prospective_maturity":prospective_maturity,
            "guard":"This is confidence in the warning-state evidence, not a probability that a crash will occur.",
        }

    @classmethod
    def _drivers_and_blockers(cls,subscores:dict,detail:dict)->tuple[list[dict],list[dict]]:
        drivers=[]
        blockers=[]
        if subscores["rates_policy"]>=0.60:
            drivers.append({
                "id":"RATES_POLICY_PRESSURE",
                "label_zh":"政策/利率重定价压力较高",
                "severity":subscores["rates_policy"],
            })
        if subscores["structural"]>=0.45:
            drivers.append({
                "id":"STRUCTURAL_VULNERABILITY",
                "label_zh":"长期结构性脆弱度较高",
                "severity":subscores["structural"],
            })
        if subscores["transmission"]>=0.50:
            drivers.append({
                "id":"CROSS_MARKET_TRANSMISSION",
                "label_zh":"跨市场传导正在增强",
                "severity":subscores["transmission"],
            })
        else:
            blockers.append({
                "id":"NO_SYSTEMIC_TRANSMISSION",
                "label_zh":"尚未形成系统性跨市场传导",
                "strength":1.0-subscores["transmission"],
            })
        if subscores["credit_liquidity"]>=0.50:
            drivers.append({
                "id":"CREDIT_LIQUIDITY_STRESS",
                "label_zh":"信用/流动性压力升高",
                "severity":subscores["credit_liquidity"],
            })
        else:
            blockers.append({
                "id":"NO_CREDIT_LIQUIDITY_CONFIRMATION",
                "label_zh":"信用/流动性尚未确认系统性压力",
                "strength":1.0-subscores["credit_liquidity"],
            })
        if subscores["market_deterioration"]>=0.50:
            drivers.append({
                "id":"MARKET_DETERIORATION",
                "label_zh":"三市场价格结构正在恶化",
                "severity":subscores["market_deterioration"],
            })
        else:
            blockers.append({
                "id":"LIMITED_MARKET_DETERIORATION",
                "label_zh":"价格层恶化尚未全面展开",
                "strength":1.0-subscores["market_deterioration"],
            })
        return drivers,blockers

    def build(
        self,
        *,
        long_cycle:dict|None,
        latent:dict|None,
        cross_market:dict|None,
        policy_curve:dict|None,
        prospective:dict|None,
        force:bool=False,
    )->dict:
        long_cycle=long_cycle or {}
        latent=latent or {}
        cross_market=cross_market or {}
        policy_curve=policy_curve or {}
        prospective=prospective or {}

        source_presence={
            "long_cycle":bool(long_cycle),
            "latent_hazard":bool(latent),
            "cross_market":bool(cross_market),
            "policy_curve":bool(policy_curve),
            "prospective":bool(prospective),
        }
        source_presence_ratio=sum(1 for x in source_presence.values() if x)/len(source_presence)
        latent_completeness=latent.get("data_completeness") or {}
        latent_errors=dict(latent_completeness.get("errors") or {})
        term_curve_usable=bool((policy_curve.get("data_quality") or {}).get("term_curve_usable"))
        if source_presence_ratio<1.0:
            coverage_grade="DEGRADED"
        elif latent_errors or not term_curve_usable:
            coverage_grade="PARTIAL"
        else:
            coverage_grade="FULL"

        component_dates=[
            str(x)
            for x in (
                long_cycle.get("as_of"),
                latent.get("as_of"),
                cross_market.get("as_of"),
                policy_curve.get("as_of"),
                prospective.get("as_of"),
            )
            if x
        ]
        as_of=max(component_dates) if component_dates else datetime.now(timezone.utc).date().isoformat()
        previous=self.latest()
        if previous and previous.get("as_of")==as_of and previous.get("source_hashes")=={
            "long_cycle":long_cycle.get("experiment_hash"),
            "latent":latent.get("experiment_hash"),
            "cross_market":cross_market.get("experiment_hash"),
            "policy_curve":policy_curve.get("snapshot_hash"),
            "prospective":prospective.get("ledger_id"),
        } and not force:
            return previous

        structural,structural_detail=self._structural_score(long_cycle)
        rates,rates_detail=self._rates_policy_score(latent,long_cycle,policy_curve)
        transmission,transmission_detail=self._transmission_score(latent)
        credit,credit_detail=self._credit_liquidity_score(long_cycle,latent)
        market,market_detail=self._market_deterioration_score(latent)
        subscores={
            "structural":structural,
            "rates_policy":rates,
            "transmission":transmission,
            "credit_liquidity":credit,
            "market_deterioration":market,
        }
        detail={
            "structural":structural_detail,
            "rates_policy":rates_detail,
            "transmission":transmission_detail,
            "credit_liquidity":credit_detail,
            "market_deterioration":market_detail,
        }

        horizon_estimates={}
        for horizon,weights in HORIZON_WEIGHTS.items():
            raw=sum(float(weights[k])*subscores[k] for k in weights)
            score=round(100.0*self._clamp01(raw),1)
            band=self._risk_band(score)
            horizon_estimates[horizon]={
                "horizon_trading_days":int(horizon),
                "risk_pressure_index":score,
                "risk_band":band,
                "risk_band_zh":self._risk_band_zh(band),
                "weights":weights,
                "semantics":"State/evidence pressure index, not a calibrated crash probability.",
            }

        overall=round(
            100.0*(
                0.20*structural
                +0.25*rates
                +0.20*transmission
                +0.15*credit
                +0.20*market
            ),
            1,
        )
        band=self._risk_band(overall)
        historical=self._historical_support(latent)
        prospective_validation=self._prospective_validation(prospective)
        confidence=self._confidence(historical,prospective_validation)
        drivers,blockers=self._drivers_and_blockers(subscores,detail)

        current_state=latent.get("current_state") or {}
        policy_comp=self._composite(current_state,"POLICY_REPRICING_STRESS")
        systemic_comp=self._composite(current_state,"SYSTEMIC_TRANSMISSION")
        warnings=[]
        if policy_comp.get("triggered"):
            warnings.append({
                "code":"POLICY_REPRICING_STRESS_ACTIVE",
                "level":"WARNING",
                "message_zh":"政策/利率重新定价联合状态已触发。",
            })
        if systemic_comp.get("triggered"):
            warnings.append({
                "code":"SYSTEMIC_TRANSMISSION_ACTIVE",
                "level":"SEVERE",
                "message_zh":"跨市场系统性传导状态已触发。",
            })
        if (long_cycle.get("hypotheses") or {}).get("downturn_confirmation",{}).get("state")=="MULTI_DIMENSION_CONFIRMED":
            warnings.append({
                "code":"SECULAR_DOWNTURN_CONFIRMED",
                "level":"SEVERE",
                "message_zh":"多年级下行假设获得多维确认。",
            })

        escalation=[
            {
                "condition":"SYSTEMIC_TRANSMISSION triggers",
                "meaning_zh":"港股桥梁、跨市场压力和相关性进一步同步恶化",
            },
            {
                "condition":"HY OAS / NFCI enter high historical percentiles",
                "meaning_zh":"信用利差和金融条件从未确认转为确认",
            },
            {
                "condition":"registered-market drawdown stress broadens",
                "meaning_zh":"价格层由局部压力扩展为两到三个市场同时恶化",
            },
            {
                "condition":"SECULAR_DOWNTURN_CONFIRMATION reaches MULTI_DIMENSION_CONFIRMED",
                "meaning_zh":"长周期脆弱性转为多年下行确认",
            },
        ]
        deescalation=[
            {
                "condition":"POLICY_REPRICING_STRESS clears persistently",
                "meaning_zh":"政策/利率重定价压力持续解除",
            },
            {
                "condition":"MOVE and 2Y repricing normalize",
                "meaning_zh":"债券波动和政策预期变化回落",
            },
            {
                "condition":"cross-market momentum improves without credit stress",
                "meaning_zh":"港股和跨市场传导没有继续扩散",
            },
        ]

        payload={
            "version":self.version,
            "as_of":as_of,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "warning_type":"TRIAID_RISK_WARNING",
            "shadow_only":True,
            "applied_to_weights":False,
            "production_action":"NONE",
            "overall":{
                "risk_pressure_index":overall,
                "risk_band":band,
                "risk_band_zh":self._risk_band_zh(band),
                "summary_zh":(
                    f"当前风险压力为{self._risk_band_zh(band)}（{overall:.1f}/100）。"
                    "该数值是多维状态证据压力指数，不是股灾发生概率。"
                ),
            },
            "horizon_estimates":horizon_estimates,
            "subscores":{
                k:{
                    "score_0_1":round(v,4),
                    "score_0_100":round(100.0*v,1),
                }
                for k,v in subscores.items()
            },
            "detail":detail,
            "warnings":warnings,
            "main_drivers":drivers,
            "missing_confirmations":blockers,
            "historical_support":historical,
            "prospective_validation":prospective_validation,
            "confidence":confidence,
            "data_coverage":{
                "source_presence_ratio":round(source_presence_ratio,3),
                "coverage_grade":coverage_grade,
                "sources":source_presence,
                "term_curve_usable":term_curve_usable,
                "latent_data_completeness":latent_completeness,
                "known_gaps":[
                    {"source":key,"error":value}
                    for key,value in sorted(latent_errors.items())
                ],
                "guard":"Module presence is not the same as complete internal coverage. Known missing series are surfaced explicitly; missing dimensions must not be interpreted as reassuring or as zero risk.",
            },
            "escalation_conditions":escalation,
            "deescalation_conditions":deescalation,
            "source_status":{
                "long_cycle_as_of":long_cycle.get("as_of"),
                "latent_hazard_as_of":latent.get("as_of"),
                "cross_market_as_of":cross_market.get("as_of"),
                "policy_curve_as_of":policy_curve.get("as_of"),
                "prospective_as_of":prospective.get("as_of"),
                "term_curve_usable":bool((policy_curve.get("data_quality") or {}).get("term_curve_usable")),
            },
            "source_hashes":{
                "long_cycle":long_cycle.get("experiment_hash"),
                "latent":latent.get("experiment_hash"),
                "cross_market":cross_market.get("experiment_hash"),
                "policy_curve":policy_curve.get("snapshot_hash"),
                "prospective":prospective.get("ledger_id"),
            },
            "semantics":{
                "risk_pressure_index":"0-100 descriptive evidence-pressure score. It is intentionally not a calibrated probability of a crash or loss.",
                "risk_band":"LOW <25; ELEVATED 25-44.9; HIGH 45-64.9; SEVERE 65-79.9; CRITICAL >=80.",
                "confidence":"Confidence in the warning-state evidence only. Historical sample size and prospective maturity limit interpretation.",
                "action":"No automatic portfolio action is authorized by this warning layer.",
            },
        }
        digest=self._hash(payload)
        payload["warning_hash"]=digest
        payload["warning_id"]=f"RISK-{as_of}-{digest[:10]}"
        self.store.save_json(self.latest_file,payload)
        history=self.history(5000)
        if not any(x.get("warning_id")==payload["warning_id"] for x in history):
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
            "latest_warning_id":latest.get("warning_id") if latest else None,
            "latest_as_of":latest.get("as_of") if latest else None,
            "overall":latest.get("overall") if latest else None,
            "confidence":latest.get("confidence") if latest else None,
        }
