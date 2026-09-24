from __future__ import annotations

from copy import deepcopy

from triaid_fin.outcome_resolver import OutcomeResolver


class FakeJournal:
    def __init__(self)->None:
        self.objects={}
        self.streams={}
    def load_json(self,name,default=None):
        row=self.objects.get(name)
        return deepcopy(row) if row is not None else deepcopy(default if default is not None else {})
    def save_json(self,name,payload):
        self.objects[name]=deepcopy(payload)
    def append_jsonl(self,name,payload):
        self.streams.setdefault(name,[]).append(deepcopy(payload))
    def read_jsonl(self,name,limit=None):
        rows=list(self.streams.get(name,[]))
        return deepcopy(rows[-limit:] if limit else rows)


class FakeEvidenceRepository:
    def __init__(self,evidence):
        self.evidence=evidence
    def recent(self,market,limit=50):
        return [{"market_id":market,"evidence_id":self.evidence["evidence_id"]}]
    def get(self,market,evidence_id):
        return deepcopy(self.evidence)


class FakeOutcomePort:
    def __init__(self,payload):
        self.payload=payload
    def review(self,market,decision_id,run_id=None):
        return deepcopy(self.payload)


def evidence(market,decision_id,run_id=None):
    return {
        "market_id":market,
        "evidence_id":f"{market}-EVID-1",
        "evidence_hash_sha256":"a"*64,
        "decision_lineage":{
            "decision_id":decision_id,
            "run_id":run_id,
            "market_as_of":"2026-09-23",
        },
    }


journal=FakeJournal()
us_review={
    "kind":"ROUTE_REVIEW",
    "review":{
        "observation_days":2,
        "daily_path":[{"as_of":"2026-09-24"},{"as_of":"2026-09-25"}],
        "current_return_max_theoretical_return":0.03,
        "current_generic_core_theoretical_return":0.02,
        "current_spy_buy_hold_return":0.015,
        "capital_sleeves":{
            "sleeves":[{
                "sleeve_id":"USD_100000",
                "starting_capital_usd":100000.0,
                "fill_ratio":1.0,
                "total_execution_cost_usd":20.0,
                "current_net_return":0.027,
                "observation_days":2,
            }]
        },
    },
}
us=OutcomeResolver(
    FakeEvidenceRepository(evidence("US","US-D1")),
    FakeOutcomePort(us_review),
    journal,
)
us_status=us.resolve_market("US")
us_latest=us_status["latest_evaluated"]

cn_eval={
    "kind":"RUN_EVALUATION",
    "run_id":"CN-R1",
    "status":"VERIFIED",
    "outcome_as_of":"2026-09-24",
    "evaluation":{
        "status":"EVALUATED",
        "baseline_return":0.01,
        "triaid_return":0.014,
        "excess_return":0.004,
        "trading_cost":0.001,
        "baseline_contributions":{"P1":0.01},
        "triaid_contributions":{"P1":0.006,"P2":0.008},
    },
    "diagnostic_summary":{},
}
cn=OutcomeResolver(
    FakeEvidenceRepository(evidence("CN","CN-R1","CN-R1")),
    FakeOutcomePort(cn_eval),
    journal,
)
cn_status=cn.resolve_market("CN")
cn_latest=cn_status["latest_evaluated"]

checks={
    "us_evaluated":us_latest["state"]=="EVALUATED",
    "us_excess_correct":abs(us_latest["triaid_excess_vs_baseline"]-0.01)<1e-12,
    "us_benchmark_excess_correct":abs(us_latest["triaid_excess_vs_benchmark"]-0.015)<1e-12,
    "us_execution_sleeve_supplemental":len(us_latest["execution_sleeves"])==1,
    "us_method_symmetric":us_latest["method"]=="SYMMETRIC_THEORETICAL_HOLDINGS_COMPARISON",
    "cn_evaluated":cn_latest["state"]=="EVALUATED",
    "cn_excess_reused":abs(cn_latest["triaid_excess_vs_baseline"]-0.004)<1e-12,
    "cn_cost_reused":abs(cn_latest["trading_cost"]-0.001)<1e-12,
    "cn_contribution_delta":abs(cn_latest["contribution_deltas"]["P2"]-0.008)<1e-12,
    "outcome_hash_present":len(us_latest["outcome_hash_sha256"])==64,
    "ledger_written":len(journal.streams.get("verified_outcomes/ledger.jsonl",[]))==2,
}

# Re-resolving the same T1 state must not append duplicate outcome evidence.
us.resolve_market("US")
checks["outcome_idempotent"]=len(journal.streams.get("verified_outcomes/ledger.jsonl",[]))==2

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_OUTCOME_RESOLVER_SMOKE_FAILED:"+"|".join(failed))

print(
    "TRIAID_OUTCOME_RESOLVER_SMOKE_PASS",
    {
        "checks":len(checks),
        "us_excess":us_latest["triaid_excess_vs_baseline"],
        "cn_excess":cn_latest["triaid_excess_vs_baseline"],
    },
)
