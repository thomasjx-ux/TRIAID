from __future__ import annotations

from pathlib import Path
from app import home

ROOT=Path(__file__).resolve().parent
app=(ROOT/"app.py").read_text(encoding="utf-8")
engine=(ROOT/"triaid_fin"/"engine.py").read_text(encoding="utf-8")
registry=(ROOT/"triaid_fin"/"market_registry.py").read_text(encoding="utf-8")
prospective=(ROOT/"triaid_fin"/"prospective_experiment.py").read_text(encoding="utf-8")
html=home()

checks={
    "cn_primary_route_is_return_max":"metadata={\"primary_experiment_mode\":\"CN_RETURN_MAX_CAPACITY\"}" in registry,
    "prospective_protocol_is_legacy_route":'experiment_mode = "CN_WORST_POOL_RESCUE"' in prospective,
    "daily_summary_exposes_protocol_status":'summary["prospective_experiment_status"]=self.prospective_experiment.status()' in engine,
    "daily_summary_only_exposes_report_when_present":'prospective=self.prospective_experiment.daily_report()' in engine and 'if prospective:' in engine,
    "ui_does_not_claim_inactive_protocol_is_primary":"当前主路线是 CN_RETURN_MAX_CAPACITY" in app,
    "ui_explains_protocol_mismatch":"这套前瞻协议只在 CN_WORST_POOL_RESCUE 下登记" in app,
    "ui_hides_empty_prospective_tables":".prospective-panel.unavailable .summary" in html and ".prospective-panel.unavailable h3" in html and ".prospective-panel.unavailable .tablewrap" in html,
    "ui_keeps_protocol_status_visible":"A股辅助前瞻协议 · 当前未激活" in html and "非当前主路线证据" in html,
    "ui_passes_status_to_renderer":"renderProspective(isCN?routeData.prospective_experiment:null,isCN?routeData.prospective_experiment_status:null)" in html,
    "ui_route_data_comes_from_projection":"const routeData=(sections.route||{}).data||{};" in html,
    "cn_scope_separates_legacy_evidence":"旧 CN_WORST_POOL_RESCUE 前瞻协议单独显示状态，不混入当前主路线" in html,
}
failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_CN_PROSPECTIVE_ROUTE_CONTRACT_FAILED:"+"|".join(failed))
print("TRIAID_CN_PROSPECTIVE_ROUTE_CONTRACT_PASS",{"checks":len(checks)})
