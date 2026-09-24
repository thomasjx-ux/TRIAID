from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

from .market_interfaces import market_interface


VERSION="verified-projection-repository@1.0.0"


def _canonical_hash(payload:dict)->str:
    raw=json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class VerifiedProjectionRepository:
    """Persist content-addressed formal UI evidence without future outcomes.

    This repository is deliberately narrower than the full UI projection.
    It stores only the decision-bearing route and strategy state that existed
    when the projection was generated. Posterior, realized curves, live data,
    activity and intraday recomputes are excluded so the artifact can never be
    mistaken for prospective T0 evidence plus future outcomes.
    """

    version=VERSION

    def __init__(self,journal)->None:
        self.journal=journal

    @staticmethod
    def _strategy_evidence(rows:list)->list[dict]:
        allowed=(
            "strategy_id",
            "market_id",
            "as_of",
            "run_id",
            "run_scope",
            "evidence_eligible",
            "lifecycle",
            "expected_net_return",
            "risk",
            "uncertainty",
            "metrics",
            "selected",
            "baseline_weight",
            "triaid_weight",
        )
        return [
            {
                key:deepcopy(row.get(key))
                for key in allowed
                if key in row
            }
            for row in rows
            if isinstance(row,dict) and row.get("strategy_id")
        ]

    @staticmethod
    def _decision_lineage(market:str,sections:dict)->dict:
        route_section=sections.get("route") or {}
        route_data=route_section.get("data") or {}
        spec=market_interface(market).route
        primary=spec.primary_payload(route_data)
        latest=primary.get("latest_decision") or {}
        strategies=(sections.get("strategies") or {}).get("data") or []
        strategy_run_id=next(
            (
                row.get("run_id")
                for row in strategies
                if isinstance(row,dict) and row.get("run_id")
            ),
            None,
        )
        strategy_as_of=next(
            (
                row.get("as_of")
                for row in strategies
                if isinstance(row,dict) and row.get("as_of")
            ),
            None,
        )
        decision_id=latest.get("decision_id") or strategy_run_id
        return {
            "decision_id":decision_id,
            "run_id":strategy_run_id,
            "frozen_at":latest.get("frozen_at"),
            "market_as_of":(
                latest.get("market_as_of")
                or primary.get("market_as_of")
                or primary.get("as_of")
                or strategy_as_of
            ),
            "route_source":route_section.get("source"),
        }

    def publish_market_page(self,payload:dict)->dict:
        market=str(payload.get("market_id") or "").upper()
        sections=payload.get("sections") or {}
        integrity=payload.get("integrity") or {}
        if not market:
            return {"state":"NOT_FROZEN","reason":"MARKET_ID_MISSING"}
        if not integrity.get("passed"):
            return {"state":"NOT_FROZEN","reason":"PROJECTION_INTEGRITY_NOT_PASSED"}
        if (sections.get("preview") or {}).get("state")!="NOT_APPLICABLE":
            return {"state":"NOT_FROZEN","reason":"PREVIEW_IS_NOT_FORMAL_EVIDENCE"}
        if (sections.get("route") or {}).get("state")!="READY":
            return {"state":"NOT_FROZEN","reason":"FORMAL_ROUTE_NOT_READY"}
        if (sections.get("strategies") or {}).get("state")!="READY":
            return {"state":"NOT_FROZEN","reason":"FORMAL_STRATEGIES_NOT_READY"}

        lineage=self._decision_lineage(market,sections)
        if not lineage.get("decision_id"):
            return {"state":"NOT_FROZEN","reason":"DECISION_IDENTITY_MISSING"}

        route_section=sections.get("route") or {}
        route_data=route_section.get("data") or {}
        route_spec=market_interface(market).route
        primary=route_spec.primary_payload(route_data)
        latest_decision=deepcopy(primary.get("latest_decision") or {})
        strategies=self._strategy_evidence(
            (sections.get("strategies") or {}).get("data") or []
        )
        formal_evidence={
            "evidence_schema":"formal-market-projection-evidence@1.1.0",
            "market_id":market,
            "contract_version":payload.get("contract_version"),
            "projection_scope":payload.get("projection_scope"),
            "decision_lineage":lineage,
            "market":deepcopy(payload.get("market") or {}),
            "formal_route":{
                "state":route_section.get("state"),
                "source":route_section.get("source"),
                "as_of":route_section.get("as_of"),
                "latest_decision":latest_decision,
                "primary_payload_keys":sorted(primary),
            },
            "formal_strategies":strategies,
            "future_information_excluded":[
                "posterior",
                "curves",
                "live",
                "activity",
                "intraday",
                "route_embedded_reviews",
                "localized_presentation_copy",
            ],
            "prospective_rule":"FORMAL_EVIDENCE_EXCLUDES_REALIZED_AND_INTRADAY_FUTURE_INFORMATION",
        }
        evidence_hash=_canonical_hash(formal_evidence)
        evidence_id=f"{market}-EVID-{evidence_hash[:20]}"
        created_at=datetime.now(timezone.utc).isoformat()
        artifact={
            **formal_evidence,
            "projection_generated_at_utc":payload.get("generated_at_utc"),
            "repository_version":self.version,
            "evidence_id":evidence_id,
            "evidence_hash_sha256":evidence_hash,
            "persisted_at_utc":created_at,
        }
        latest_name=f"verified_projections/{market}/latest.json"
        latest=self.journal.load_json(latest_name,default={})
        changed=latest.get("evidence_id")!=evidence_id
        if changed:
            self.journal.save_json(
                f"verified_projections/{market}/{evidence_id}.json",
                artifact,
            )
            self.journal.save_json(latest_name,artifact)
            self.journal.append_jsonl(
                "verified_projections/ledger.jsonl",
                {
                    "evidence_id":evidence_id,
                    "market_id":market,
                    "decision_id":lineage.get("decision_id"),
                    "run_id":lineage.get("run_id"),
                    "market_as_of":lineage.get("market_as_of"),
                    "frozen_at":lineage.get("frozen_at"),
                    "evidence_hash_sha256":evidence_hash,
                    "persisted_at_utc":created_at,
                },
            )
        return {
            "state":"FROZEN",
            "repository_version":self.version,
            "evidence_id":evidence_id,
            "evidence_hash_sha256":evidence_hash,
            "decision_lineage":lineage,
            "changed":changed,
            "future_information_excluded":formal_evidence["future_information_excluded"],
        }

    def get(self,market_id:str,evidence_id:str)->dict:
        market=str(market_id).upper()
        return self.journal.load_json(
            f"verified_projections/{market}/{evidence_id}.json",
            default={},
        )

    def recent(self,market_id:str,limit:int=50)->list[dict]:
        market=str(market_id).upper()
        rows=self.journal.read_jsonl(
            "verified_projections/ledger.jsonl",
            limit=max(50,int(limit)*4),
        )
        filtered=[
            row for row in rows
            if str(row.get("market_id") or "").upper()==market
        ]
        return filtered[-int(limit):]

    def latest(self,market_id:str)->dict:
        market=str(market_id).upper()
        return self.journal.load_json(
            f"verified_projections/{market}/latest.json",
            default={},
        )
