from __future__ import annotations

from types import SimpleNamespace

from triaid_fin.ui_projection import MarketPageProjection, READY, WAITING


class FakePopulation:
    def __init__(self,missing_numeric:bool=False)->None:
        self.missing_numeric=missing_numeric

    def strategy_cards(self,lang,market):
        return [
            {
                "strategy_id":"P00_BUY_HOLD",
                "name":"买入并持有" if lang=="zh" else "Buy & Hold",
                "summary":"control",
                "best_conditions":"all",
            }
        ]


class FakeEngine:
    architecture_version="triaid-fin-v2@projection-test"

    def __init__(self,missing_numeric:bool=False)->None:
        self.core=SimpleNamespace(version="triaid-core-v2@test")
        self.strategy_population=FakePopulation(missing_numeric)
        expected=None if missing_numeric else 0.12
        state=SimpleNamespace(
            strategy_id="P00_BUY_HOLD",
            lifecycle="ACTIVE",
            expected_net_return=expected,
            risk=0.10,
            uncertainty=0.01,
            metrics={},
        )
        group=SimpleNamespace(
            members=["P00_BUY_HOLD"],
            weights={"P00_BUY_HOLD":1.0},
            reasons={},
        )
        decision=SimpleNamespace(
            core_version="triaid-core-v2@test",
            weights_after={"P00_BUY_HOLD":1.0},
            reasons={},
        )
        market=SimpleNamespace(
            market_id="US",
            as_of="2026-09-23",
            snapshot_id="US:test",
            metadata={"experiment_mode":"US_RETURN_MAX_CAPACITY"},
        )
        self.run=SimpleNamespace(
            run_id="US-test",
            market=market,
            status="DECIDED",
            triaid_decision=decision,
            strategy_group=group,
            strategy_states=[state],
            diagnostic_summary={},
            evaluation=None,
        )

    def latest_decision_run(self,market):
        return self.run

    def get_run(self,run_id):
        return self.run

    def all_runs(self):
        return [self.run]

    def _evidence_eligible_run(self,run):
        return True

    def curves(self,market):
        return []

    def evolution_status(self):
        return {
            "history":[],
            "diagnosis":{
                "evaluated_runs":0,
                "mean_excess_return":None,
                "negative_rate":None,
            },
        }

    def daily_summary(self,market,compact=False):
        return {
            "date":"2026-09-23",
            "us_return_max":{
                "latest_decision":{
                    "decision_id":"USRM-test",
                    "market_as_of":"2026-09-23",
                    "frozen_at":"2026-09-23T20:10:00+00:00",
                    "target_strategy_weights":{"P00_BUY_HOLD":1.0},
                    "generic_core_control_weights":{"P00_BUY_HOLD":1.0},
                    "target_asset_weights":{
                        "SPY":1.0,"QQQ":0.0,"IWM":0.0,"TLT":0.0,"GLD":0.0,
                    },
                    "projected_annualized_expected_net_return":0.12,
                    "generic_core_projected_annualized_expected_net_return":0.10,
                    "buy_hold_projected_annualized_expected_net_return":0.08,
                    "cash_residual_weight":0.0,
                    "capital_capacity":{
                        "max_participation_adv":0.03,
                        "base_cost_bps":1.5,
                        "impact_coefficient_bps":45.0,
                        "sleeves":[
                            {
                                "starting_capital_usd":100000,
                                "target_invested_notional_usd":100000,
                                "max_one_day_participation_adv":0.00001,
                                "minimum_execution_days":1,
                                "estimated_round_trip_cost_proxy_usd":30.0,
                            },
                            {
                                "starting_capital_usd":1000000,
                                "target_invested_notional_usd":1000000,
                                "max_one_day_participation_adv":0.0001,
                                "minimum_execution_days":1,
                                "estimated_round_trip_cost_proxy_usd":300.0,
                            },
                            {
                                "starting_capital_usd":10000000,
                                "target_invested_notional_usd":10000000,
                                "max_one_day_participation_adv":0.001,
                                "minimum_execution_days":1,
                                "estimated_round_trip_cost_proxy_usd":3000.0,
                            },
                            {
                                "starting_capital_usd":100000000,
                                "target_invested_notional_usd":100000000,
                                "max_one_day_participation_adv":0.01,
                                "minimum_execution_days":1,
                                "estimated_round_trip_cost_proxy_usd":30000.0,
                            },
                        ]
                    },
                },
                "latest_decision_review":{"observation_days":0,"daily_path":[]},
                "previous_decision_review":{"observation_days":0,"daily_path":[]},
            },
        }


class FakeAutomation:
    def __init__(self,stale:bool=False)->None:
        self.stale=stale

    def live_indicators(self,market):
        return {
            "market_id":market,
            "session_phase":"OPEN",
            "available":True,
            "provider":"fake",
            "source_latest_ts":1790265600,
            "source_time_utc":"2026-09-24T14:40:00+00:00",
            "freshness_seconds":240.0 if self.stale else 20.0,
            "instruments":[
                {"symbol":"SPY","close":600.0,"change_pct":0.001},
                {"symbol":"QQQ","close":520.0,"change_pct":0.002},
                {"symbol":"IWM","close":230.0,"change_pct":-0.001},
                {"symbol":"TLT","close":95.0,"change_pct":0.0},
                {"symbol":"GLD","close":240.0,"change_pct":0.001},
            ],
        }

    def activity(self,market,limit):
        return {
            "market_id":market,
            "session_phase":"OPEN",
            "refresh_plan":{"REALTIME":60},
            "schedule_text":"REALTIME every 60s",
            "events":[{"at":"2026-09-24T14:40:00+00:00","kind":"DATA_FETCH"}],
        }


class FakeScheduler:
    def status(self):
        return {
            "version":"decision-scheduler@test",
            "enabled":True,
            "markets":{
                "US":{
                    "session_date":"2026-09-24",
                    "baseline_done":True,
                    "baseline_fresh":True,
                    "decision_count":3,
                }
            },
        }

    def events(self,market,limit=120):
        return [
            {
                "market_id":"US",
                "session_date":"2026-09-24",
                "event_type":"TRANSITION_RESEARCH_DECISION",
                "created_at":"2026-09-24T14:39:00+00:00",
                "decision":{
                    "weight_change_l1_vs_reference":0.0,
                    "transition_regime":"intraday_risk_on",
                },
            }
        ]

class ClosedAutomation(FakeAutomation):
    def __init__(self)->None:
        super().__init__(stale=False)

    def live_indicators(self,market):
        row=super().live_indicators(market)
        row.update({
            "session_phase":"CLOSED",
            "available":False,
            "freshness_seconds":None,
            "instruments":[],
        })
        return row

    def activity(self,market,limit):
        return {
            "market_id":market,
            "session_phase":"CLOSED",
            "refresh_plan":{},
            "schedule_text":"",
            "events":[],
        }


class ClosedScheduler(FakeScheduler):
    def status(self):
        payload=super().status()
        payload["markets"]["US"].update({
            "baseline_done":False,
            "baseline_fresh":False,
            "decision_count":0,
        })
        return payload

    def events(self,market,limit=120):
        return []


projection=MarketPageProjection(FakeEngine(),FakeAutomation(),FakeScheduler())
page=projection.full("US","zh")
sections=page["sections"]

checks={
    "contract_version":page["contract_version"]=="market-page-projection@1.3.0",
    "full_scope":page["projection_scope"]=="FULL",
    "daily_ready":sections["daily"]["state"]==READY,
    "strategies_ready":sections["strategies"]["state"]==READY,
    "route_ready":sections["route"]["state"]==READY,
    "live_ready":sections["live"]["state"]==READY,
    "scheduler_ready":sections["scheduler"]["state"]==READY,
    "intraday_ready":sections["intraday"]["state"]==READY,
    "evolution_ready":sections["evolution"]["state"]==READY,
    "preview_not_requested_is_explicit":sections["preview"]["state"]=="NOT_APPLICABLE" and bool(sections["preview"]["reason"]),
    "single_source_contract_declared":page["contract"]["single_market_page_source_of_truth"] is True,
    "posterior_waiting_is_explicit":sections["posterior"]["state"]==WAITING and bool(sections["posterior"]["reason"]),
    "expected_waiting_posterior_does_not_fail_page":page["integrity"]["passed"] is True,
    "waiting_posterior_makes_page_degraded":page["integrity"]["status"]=="DEGRADED",
    "no_unexplained_empty_sections":page["integrity"]["unexplained_non_ready_sections"]==[],
    "formal_evidence_absence_is_explicit_without_repository":page["formal_evidence"]["state"]=="NOT_CONFIGURED",
    "four_us_capital_sleeves":len(sections["route"]["data"]["latest_decision"]["capital_capacity"]["sleeves"])==4,
}

bad=MarketPageProjection(FakeEngine(missing_numeric=True),FakeAutomation(),FakeScheduler()).full("US","zh")
checks["missing_required_numeric_blocks_projection"]=bad["integrity"]["passed"] is False
checks["numeric_failure_is_named"]=any("NUMERIC_FIELDS_INCOMPLETE" in x for x in bad["integrity"]["errors"])

live=projection.live("US")
checks["live_projection_uses_same_contract"]=live["contract_version"]==page["contract_version"]
checks["live_projection_integrity_passes"]=live["integrity"]["passed"] is True
checks["live_projection_contains_intraday_section"]="intraday" in live["sections"]

stale_projection=MarketPageProjection(FakeEngine(),FakeAutomation(stale=True),FakeScheduler())
stale_full=stale_projection.full("US","zh")
stale_live=stale_projection.live("US")
checks["stale_live_does_not_blank_frozen_full_page"]=stale_full["integrity"]["passed"] is True
checks["stale_live_marks_full_page_degraded"]=stale_full["integrity"]["status"]=="DEGRADED"
checks["stale_live_blocks_live_contract"]=stale_live["integrity"]["passed"] is False
checks["stale_live_failure_is_explicit"]=any("OPEN_SESSION_LIVE_DATA_STALE_GT_180S" in x for x in stale_live["integrity"]["errors"])

closed_projection=MarketPageProjection(FakeEngine(),ClosedAutomation(),ClosedScheduler())
closed_live=closed_projection.live("US")
checks["closed_market_live_contract_passes"]=closed_live["integrity"]["passed"] is True
checks["closed_market_live_is_not_applicable"]=closed_live["sections"]["live"]["state"]=="NOT_APPLICABLE"
checks["closed_market_activity_is_not_applicable"]=closed_live["sections"]["activity"]["state"]=="NOT_APPLICABLE"
checks["closed_market_intraday_is_not_applicable"]=closed_live["sections"]["intraday"]["state"]=="NOT_APPLICABLE"
checks["closed_market_has_no_unexplained_empty_sections"]=closed_live["integrity"]["unexplained_non_ready_sections"]==[]

failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit("TRIAID_UI_PROJECTION_FAILED:"+"|".join(failed))

print("TRIAID_UI_PROJECTION_PASS",{"checks":len(checks),"status":page["integrity"]["status"]})
