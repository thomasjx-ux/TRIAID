from __future__ import annotations

import json
from datetime import date
from app import engine, home, status

issues=[]
checks={}

def check(name,condition,detail=None):
    ok=bool(condition)
    checks[name]={"passed":ok,"detail":detail}
    if not ok:
        issues.append({"check":name,"detail":detail})
    return ok

s=status()
rw=engine.risk_warning_latest()
rc=engine.risk_control_latest()
latent=engine.latent_hazard_latest()
curve=engine.policy_curve_latest()
prospective=engine.hazard_prospective_latest()
long_cycle=engine.long_cycle_hypothesis_latest()
cross=engine.cross_market_crash_latest()
html=home()

check("architecture",s.get("architecture_version")=="fin-evolution-lab@0.14.0",s.get("architecture_version"))
check("risk_warning_present",isinstance(rw,dict) and bool(rw),None if rw else "missing")
check("risk_control_present",isinstance(rc,dict) and bool(rc),None if rc else "missing")
check("latent_present",isinstance(latent,dict) and bool(latent),None if latent else "missing")
check("policy_curve_present",isinstance(curve,dict) and bool(curve),None if curve else "missing")
check("prospective_present",isinstance(prospective,dict) and bool(prospective),None if prospective else "missing")
check("long_cycle_present",isinstance(long_cycle,dict) and bool(long_cycle),None if long_cycle else "missing")
check("cross_market_present",isinstance(cross,dict) and bool(cross),None if cross else "missing")

if rw:
    overall=float((rw.get("overall") or {}).get("risk_pressure_index") or -1)
    check("risk_score_range",0.0<=overall<=100.0,overall)
    horizons=rw.get("horizon_estimates") or {}
    check("risk_horizons_exact",set(horizons)=={"20","60","120","250"},sorted(horizons))
    check(
        "risk_horizon_ranges",
        all(0.0<=float((horizons.get(h) or {}).get("risk_pressure_index") or -1)<=100.0 for h in ("20","60","120","250")),
        {h:(horizons.get(h) or {}).get("risk_pressure_index") for h in ("20","60","120","250")},
    )
    check("risk_not_probability","not a calibrated" in str((rw.get("semantics") or {}).get("risk_pressure_index","")).lower(),(rw.get("semantics") or {}).get("risk_pressure_index"))
    check("risk_no_production_action",rw.get("production_action")=="NONE" and rw.get("applied_to_weights") is False,{"action":rw.get("production_action"),"applied":rw.get("applied_to_weights")})
    coverage=rw.get("data_coverage") or {}
    check("coverage_grade_present",coverage.get("coverage_grade") in {"FULL","PARTIAL","DEGRADED"},coverage)
    check("coverage_known_gaps_explicit","known_gaps" in coverage,coverage)
    source_dates=[
        x for x in (rw.get("source_status") or {}).values()
        if isinstance(x,str) and len(x)>=10 and x[:4].isdigit()
    ]
    if source_dates:
        check("warning_asof_not_before_sources",str(rw.get("as_of"))>=max(source_dates),{"warning_as_of":rw.get("as_of"),"sources":source_dates})

if rc:
    market_rows=rc.get("three_market_state") or []
    check("three_markets_exact",[x.get("market") for x in market_rows]==["US","CN","HK"],[x.get("market") for x in market_rows])
    check("risk_control_shadow_only",rc.get("shadow_only") is True,rc.get("shadow_only"))
    rce=rc.get("risk_control_experiment") or {}
    check("risk_control_no_production_action",rce.get("production_action")=="NONE" and rce.get("applied_to_weights") is False,rce)
    check("market_rows_never_applied",all(x.get("applied_to_production") is False for x in market_rows),market_rows)
    check("promotion_gate_requires_prospective",bool((rce.get("promotion_gate") or {}).get("prospective_validation_required")),rce.get("promotion_gate"))
    check("promotion_gate_requires_net_benefit",bool((rce.get("promotion_gate") or {}).get("must_show_positive_net_benefit_after_cost")),rce.get("promotion_gate"))
    check("dynamics_chain_complete",len(rc.get("dynamics_chain") or [])==7,[x.get("id") for x in (rc.get("dynamics_chain") or [])])
    dq=rc.get("data_quality") or {}
    check("daily_evidence_not_marked_affected",dq.get("evidence_critical_daily_data_affected") is False,dq)
    check("risk_control_data_quality_present","risk_evidence_coverage" in dq,dq)

if latent:
    supported=latent.get("statistically_supported_composite_rows") or []
    for i,row in enumerate(supported):
        check(f"supported_q_{i}",float(row.get("bh_q_value") or 1.0)<=0.10,row)
        check(f"supported_samples_{i}",int(row.get("event_samples") or 0)>=3,row)
        check(f"supported_loo_{i}",float(row.get("leave_one_event_out_min_hit_rate") or 0.0)>=0.50,row)
    current=latent.get("current_state") or {}
    check("latent_shadow_only",current.get("shadow_only") is True,current.get("shadow_only"))
    check("latent_no_action",current.get("production_action")=="NONE",current.get("production_action"))

if curve:
    quality=curve.get("data_quality") or {}
    if quality.get("term_curve_usable"):
        check("fed_funds_curve_min4",int(quality.get("fed_funds_contracts") or 0)>=4,quality)
        check("sofr_curve_min4",max(int(quality.get("sofr_1m_contracts") or 0),int(quality.get("sofr_3m_contracts") or 0))>=4,quality)

if prospective:
    h=str(prospective.get("hazard_signal_as_of") or "")
    p=str(prospective.get("policy_curve_as_of") or "")
    a=str(prospective.get("as_of") or "")
    check("prospective_time_alignment",bool(a) and a>=h and a>=p,{"as_of":a,"hazard":h,"policy_curve":p})
    check("prospective_evidence_eligible",prospective.get("evidence_eligible") is not False,prospective.get("evidence_eligible"))
    check("prospective_no_weight_action",prospective.get("applied_to_weights") is False,prospective.get("applied_to_weights"))

check("strategy_count_us",len(engine.strategy_population.definitions("US"))==29,len(engine.strategy_population.definitions("US")))
check("strategy_count_cn",len(engine.strategy_population.definitions("CN"))==33,len(engine.strategy_population.definitions("CN")))
check("strategy_count_hk",len(engine.strategy_population.definitions("HK"))==29,len(engine.strategy_population.definitions("HK")))
check("market_list_exact",s.get("markets")==["US","CN","HK"],s.get("markets"))

required_ui=[
    'id="riskWarningPanel"','id="riskThreeMarketRows"','id="riskDynamicsRows"',
    'id="riskMacroRows"','id="riskTermRows"','id="riskCurveContractRows"',
    'id="riskHistoryRows"','id="riskControlRows"','id="riskDataQuality"',
    'id="riskDataGaps"','/api/risk-warning/latest','/api/risk-control/latest',
    'TRIAID 三市场联动风险中心','它不是第四个市场',
]
check("risk_center_ui_complete",all(x in html for x in required_ui),[x for x in required_ui if x not in html])

receipt={
    "audit":"TRIAID_THREE_MARKET_RISK_CENTER_FULL_AUDIT",
    "passed":not issues,
    "check_count":len(checks),
    "failed_count":len(issues),
    "issues":issues,
    "checks":checks,
    "summary":{
        "risk_warning_id":(rw or {}).get("warning_id"),
        "risk_control_experiment_id":(rc or {}).get("experiment_id"),
        "risk_pressure_index":((rw or {}).get("overall") or {}).get("risk_pressure_index"),
        "risk_band":((rw or {}).get("overall") or {}).get("risk_band"),
        "risk_control_stage":((rc or {}).get("risk_control_experiment") or {}).get("stage"),
        "coverage_grade":((rw or {}).get("data_coverage") or {}).get("coverage_grade"),
        "known_gap_count":len(((rw or {}).get("data_coverage") or {}).get("known_gaps") or []),
    },
}
engine.store.save_json("risk_center_full_audit_latest.json",receipt)
engine.store.append_jsonl("risk_center_full_audit_history.jsonl",receipt)
print("TRIAID_RISK_CENTER_FULL_AUDIT_"+("PASS" if receipt["passed"] else "FAIL"),json.dumps(receipt,ensure_ascii=False,sort_keys=True))
if issues:
    raise SystemExit(1)
