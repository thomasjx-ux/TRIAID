from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from triaid_fin.projection_repository import VerifiedProjectionRepository
from triaid_fin.runtime_ports import RuntimeServices
from triaid_fin.ui_ports import UiReadServices


class FakeStore:
    def __init__(self)->None:
        self.objects={}
        self.streams={}
    def load_json(self,name,default=None):
        value=self.objects.get(name)
        return deepcopy(value) if value is not None else ({} if default is None else deepcopy(default))
    def save_json(self,name,payload):
        self.objects[name]=deepcopy(payload)
    def append_jsonl(self,name,payload):
        self.streams.setdefault(name,[]).append(deepcopy(payload))
    def read_jsonl(self,name,limit=None):
        rows=list(self.streams.get(name,[]))
        return deepcopy(rows[-limit:] if limit else rows)


class FakePopulation:
    def strategy_cards(self,lang,market):
        return []


class FakeEngine:
    architecture_version="architecture-test"
    def __init__(self)->None:
        self.store=FakeStore()
        self.core=SimpleNamespace(version="core-test")
        self.strategy_population=FakePopulation()


engine=FakeEngine()
runtime=RuntimeServices(engine)
ui=UiReadServices(engine)

checks={
    "runtime_services_v2":runtime.version=="runtime-services@2.0.0",
    "market_data_port_present":runtime.market_data.version=="market-data-runtime-port@1.0.0",
    "decision_port_present":runtime.decision.version=="decision-runtime-port@1.0.0",
    "research_port_present":runtime.research.version=="research-runtime-port@1.0.0",
    "runtime_journal_present":runtime.journal.version=="runtime-journal@1.1.0",
    "ui_services_v2":ui.version=="ui-read-services@2.0.0",
    "market_page_read_port_present":ui.market_page.version=="market-page-read-port@1.0.0",
    "risk_read_port_present":ui.risk.version=="risk-read-port@1.0.0",
}

root=Path(__file__).resolve().parent
runtime_source=(root/"triaid_fin"/"market_runtime.py").read_text(encoding="utf-8")
scheduler_source=(root/"triaid_fin"/"decision_scheduler.py").read_text(encoding="utf-8")
jobs_source=(root/"triaid_fin"/"runtime_jobs.py").read_text(encoding="utf-8")
projection_source=(root/"triaid_fin"/"ui_projection.py").read_text(encoding="utf-8")
risk_source=(root/"triaid_fin"/"risk_projection.py").read_text(encoding="utf-8")

checks.update({
    "market_runtime_uses_market_data_port":"self.services.market_data." in runtime_source,
    "market_runtime_no_compatibility_market_calls":all(
        token not in runtime_source
        for token in (
            "self.services.market_data_capabilities",
            "self.services.refresh_market_data",
            "self.services.market_data_snapshot",
            "self.services.record_market_observation",
        )
    ),
    "scheduler_uses_decision_port":"self.services.decision." in scheduler_source,
    "scheduler_uses_market_data_port":"self.services.market_data." in scheduler_source,
    "runtime_jobs_use_research_port":"ctx.services.research." in jobs_source,
    "market_projection_uses_narrow_read_port":"MarketPageReadPort" in projection_source,
    "risk_projection_uses_narrow_read_port":"RiskReadPort" in risk_source,
})

repo=VerifiedProjectionRepository(runtime.journal)
payload={
    "contract_version":"market-page-projection@1.3.0",
    "projection_scope":"FULL",
    "market_id":"US",
    "generated_at_utc":"2026-09-24T15:00:00+00:00",
    "market":{
        "benchmark":"SPY",
        "currency":"USD",
        "timezone":"America/New_York",
        "assets":["SPY","QQQ","IWM","TLT","GLD"],
        "primary_experiment_mode":"US_RETURN_MAX_CAPACITY",
    },
    "integrity":{"passed":True},
    "sections":{
        "route":{
            "state":"READY",
            "source":"us_return_max_ledger",
            "data":{
                "latest_decision_review":{"observation_days":2,"daily_path":[{"future_return":99}]},
                "latest_decision":{
                    "decision_id":"USRM-1",
                    "frozen_at":"2026-09-24T14:00:00+00:00",
                    "market_as_of":"2026-09-23",
                    "target_strategy_weights":{"P20_XMOM126":1.0},
                }
            },
        },
        "strategies":{
            "state":"READY",
            "data":[{
                "strategy_id":"P20_XMOM126",
                "run_id":"US-run-1",
                "as_of":"2026-09-23",
                "selected":True,
                "baseline_weight":1.0,
                "triaid_weight":1.0,
                "name":"localized name",
                "summary":"localized summary",
                "selection_reason":"localized reason",
                "triaid_reason":"localized triaid reason",
            }],
        },
        "preview":{
            "state":"NOT_APPLICABLE",
            "reason":"NO_PREVIEW_REQUESTED",
            "data":{},
        },
        "posterior":{"state":"READY","data":{"future_return":99}},
        "curves":{"state":"READY","data":[{"future_return":99}]},
        "live":{"state":"READY","data":{"future_price":99}},
        "activity":{"state":"READY","data":{"events":[99]}},
        "intraday":{"state":"READY","data":{"decision_events":[99]}},
    },
}
first=repo.publish_market_page(payload)
payload2=deepcopy(payload)
payload2["generated_at_utc"]="2026-09-24T15:05:00+00:00"
second=repo.publish_market_page(payload2)
latest=repo.latest("US")

checks.update({
    "formal_evidence_frozen":first.get("state")=="FROZEN",
    "formal_evidence_content_addressed":bool(first.get("evidence_id")) and len(first.get("evidence_hash_sha256") or "")==64,
    "render_time_does_not_change_evidence_identity":first.get("evidence_id")==second.get("evidence_id"),
    "duplicate_formal_state_deduped":second.get("changed") is False,
    "formal_evidence_has_decision_lineage":((latest.get("decision_lineage") or {}).get("decision_id")=="USRM-1"),
    "formal_evidence_schema_v11":latest.get("evidence_schema")=="formal-market-projection-evidence@1.1.0",
    "formal_evidence_excludes_posterior":"posterior" not in latest,
    "formal_evidence_excludes_curves":"curves" not in latest,
    "formal_evidence_excludes_live":"live" not in latest,
    "formal_evidence_excludes_intraday":"intraday" not in latest,
    "formal_route_strips_embedded_reviews":"latest_decision_review" not in (latest.get("formal_route") or {}),
    "formal_strategy_strips_localized_copy":all("name" not in row and "summary" not in row and "selection_reason" not in row and "triaid_reason" not in row for row in (latest.get("formal_strategies") or [])),
    "formal_evidence_declares_future_exclusions":set(latest.get("future_information_excluded") or [])=={"posterior","curves","live","activity","intraday","route_embedded_reviews","localized_presentation_copy"},
    "formal_evidence_ledger_written":len(engine.store.streams.get("verified_projections/ledger.jsonl",[]))==1,
})

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_CAPABILITY_PORTS_EVIDENCE_FAILED:"+"|".join(failed))

print(
    "TRIAID_CAPABILITY_PORTS_EVIDENCE_PASS",
    {
        "checks":len(checks),
        "evidence_id":first.get("evidence_id"),
        "runtime_ports":runtime.status().get("ports"),
        "ui_ports":ui.status().get("ports"),
    },
)
