from __future__ import annotations

from copy import deepcopy

from triaid_fin.validation_projection import ValidationSummaryProjection


class FakeResolver:
    def __init__(self)->None:
        self.rows={
            "US":[
                {
                    "market_id":"US",
                    "evidence_id":"US-E1",
                    "outcome_as_of":"2026-09-24",
                    "resolved_at_utc":"2026-09-24T22:00:00+00:00",
                    "triaid_excess_vs_baseline":0.010,
                },
                {
                    "market_id":"US",
                    "evidence_id":"US-E2",
                    "outcome_as_of":"2026-09-25",
                    "resolved_at_utc":"2026-09-25T22:00:00+00:00",
                    "triaid_excess_vs_baseline":-0.004,
                },
            ],
            "CN":[
                {
                    "market_id":"CN",
                    "evidence_id":"CN-E1",
                    "outcome_as_of":"2026-09-25",
                    "resolved_at_utc":"2026-09-25T07:00:00+00:00",
                    "triaid_excess_vs_baseline":0.003,
                },
            ],
            "HK":[],
        }
        self.latest={
            "US":{
                "state":"EVALUATED",
                "evidence_id":"US-E2",
                "outcome_hash_sha256":"b"*64,
                "t0_market_as_of":"2026-09-24",
                "outcome_as_of":"2026-09-25",
                "baseline_realized_return":0.012,
                "triaid_realized_return":0.008,
                "triaid_excess_vs_baseline":-0.004,
                "observation_days":1,
                "method":"SYMMETRIC_THEORETICAL_HOLDINGS_COMPARISON",
            },
            "CN":{
                "state":"EVALUATED",
                "evidence_id":"CN-E1",
                "outcome_hash_sha256":"c"*64,
                "t0_market_as_of":"2026-09-24",
                "outcome_as_of":"2026-09-25",
                "baseline_realized_return":0.005,
                "triaid_realized_return":0.008,
                "triaid_excess_vs_baseline":0.003,
                "observation_days":1,
                "method":"GENERIC_EVALUATION_MODULE",
            },
            "HK":None,
        }

    def resolve_market(self,market):
        rows=self.rows[market]
        return {
            "resolver_version":"triaid-outcome-resolver@1.0.0",
            "market_id":market,
            "formal_evidence_count":max(1,len(rows)),
            "evaluated_count":len(rows),
            "waiting_count":1 if market=="HK" else 0,
            "latest_evaluated":deepcopy(self.latest[market]),
        }

    def history(self,market,limit=200):
        return deepcopy(self.rows[market][-limit:])


projection=ValidationSummaryProjection(FakeResolver())
page=projection.full("US")
overall=page["overall"]
us=page["selected_market"]

checks={
    "contract_version":page["contract_version"]=="validation-summary-projection@1.0.0",
    "scope":page["projection_scope"]=="TRIAID_REALIZED_VALUE_VALIDATION",
    "selected_market":page["market_id"]=="US" and us["market_id"]=="US",
    "overall_samples":overall["evaluated_samples"]==3,
    "overall_positive":overall["positive_samples"]==2,
    "overall_positive_rate":abs(overall["positive_rate"]-(2/3))<1e-12,
    "overall_mean":abs(overall["mean_excess"]-0.003)<1e-12,
    "overall_sum":abs(overall["sample_excess_sum"]-0.009)<1e-12,
    "markets_with_samples":overall["markets_with_evaluated_samples"]==2,
    "us_samples":us["stats"]["evaluated_samples"]==2,
    "us_curve_final":abs(us["stats"]["curve"][-1]["cumulative_sample_excess"]-0.006)<1e-12,
    "latest_is_preserved":us["latest_evaluated"]["evidence_id"]=="US-E2",
    "waiting_market_is_explicit":page["markets"]["HK"]["state"]=="WAITING",
    "definition_not_account_return":"not account cumulative return" in page["definitions"]["sample_excess_sum"],
    "projection_does_not_recompute_returns":"does not recompute returns" in ValidationSummaryProjection.__doc__,
    "integrity_passed":page["integrity"]["passed"] is True,
}

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_VALIDATION_PROJECTION_SMOKE_FAILED:"+"|".join(failed))

print(
    "TRIAID_VALIDATION_PROJECTION_SMOKE_PASS",
    {
        "checks":len(checks),
        "samples":overall["evaluated_samples"],
        "mean_excess":overall["mean_excess"],
        "sample_excess_sum":overall["sample_excess_sum"],
    },
)
