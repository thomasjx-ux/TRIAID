from __future__ import annotations

from datetime import datetime, timezone

from .market_registry import MARKET_REGISTRY, market_ids
from .ui_ports import MarketPageReadPort, UiReadServices


class HomeBriefProjection:
    """Fast, read-only first-paint projection from in-memory formal decisions.

    Does not rebuild daily reports, publish evidence, read historical ledgers,
    fetch market data or resolve outcomes. Live indicators use the separate
    /api/ui/market-page/{market_id}/live route.
    """

    version="home-brief@1.0.0"

    def __init__(self,services)->None:
        if isinstance(services,MarketPageReadPort):
            self.services=services
        elif isinstance(services,UiReadServices):
            self.services=services.market_page
        else:
            self.services=UiReadServices(services).market_page

    def full(self)->dict:
        enabled_markets=market_ids()
        # One in-memory history scan for all markets, not one expensive report
        # or one separate historical storage query per market.
        runs=self.services.all_runs()
        evaluated={}
        for run in runs:
            market=str(run.market.market_id).upper()
            if market not in enabled_markets or run.evaluation is None:
                continue
            metadata=run.market.metadata or {}
            primary=str(MARKET_REGISTRY.get(market).metadata.get("primary_experiment_mode") or "").upper()
            if primary and str(metadata.get("experiment_mode") or "").upper()!=primary:
                continue
            if metadata.get("evidence_eligible") is False:
                continue
            if str(metadata.get("run_scope") or "OFFICIAL_EVIDENCE")=="MANUAL_PREVIEW":
                continue
            if run.evaluation.status!="EVALUATED":
                continue
            evaluated[market]=run

        markets={}
        errors=[]
        for market in enabled_markets:
            try:
                spec=MARKET_REGISTRY.get(market)
                run=self.services.latest_decision_run(market)
                selected=list(run.strategy_group.members) if run and run.strategy_group else []
                before=dict(run.strategy_group.weights) if run and run.strategy_group else {}
                after=dict(run.triaid_decision.weights_after) if run and run.triaid_decision else {}
                names_zh={
                    card["strategy_id"]:card.get("name") or card["strategy_id"]
                    for card in self.services.strategy_cards("zh",market)
                }
                names_en={
                    card["strategy_id"]:card.get("name") or card["strategy_id"]
                    for card in self.services.strategy_cards("en",market)
                }
                last=evaluated.get(market)
                markets[market]={
                    "market_id":market,
                    "timezone":spec.timezone,
                    "currency":spec.currency,
                    "benchmark":spec.benchmark,
                    "primary_experiment_mode":spec.metadata.get("primary_experiment_mode"),
                    "run_id":run.run_id if run else None,
                    "market_as_of":run.market.as_of if run else None,
                    "run_status":run.status if run else "WAITING_FOR_FORMAL_DECISION",
                    "market_regime":run.market.regime if run else None,
                    "selection":{
                        "selected_count":len(selected) if run else None,
                        "changed_count":sum(
                            1 for sid in selected
                            if abs(float(after.get(sid,0.0))-float(before.get(sid,0.0)))>1e-8
                        ) if run else None,
                        "selected_names_zh":[names_zh.get(sid,sid) for sid in sorted(
                            selected,key=lambda sid:float(after.get(sid,0.0)),reverse=True
                        )[:6]],
                        "selected_names_en":[names_en.get(sid,sid) for sid in sorted(
                            selected,key=lambda sid:float(after.get(sid,0.0)),reverse=True
                        )[:6]],
                    },
                    "latest_evaluated":{
                        "run_id":last.run_id,
                        "market_as_of":last.market.as_of,
                        "evaluation":{
                            "status":last.evaluation.status,
                            "baseline_return":last.evaluation.baseline_return,
                            "triaid_return":last.evaluation.triaid_return,
                            "excess_return":last.evaluation.excess_return,
                        },
                    } if last is not None else None,
                    "data_maturity":"FORMAL_COMPLETED_SESSION_ONLY",
                    "status":"READY" if run else "WAITING",
                }
            except Exception as exc:
                errors.append(f"{market}:{type(exc).__name__}:{exc}")
                markets[market]={
                    "market_id":market,
                    "status":"ERROR",
                    "reason":f"{type(exc).__name__}:{exc}",
                    "data_maturity":"UNAVAILABLE",
                }
        return {
            "version":self.version,
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "market_count":len(enabled_markets),
            "markets":markets,
            "integrity":{
                "passed":not errors,
                "errors":errors,
                "rule":"ONE_MEMORY_ONLY_BRIEF_PER_REGISTERED_MARKET_NO_PREVIEW_AS_FORMAL_EVIDENCE",
            },
            "performance_contract":{
                "no_market_data_fetch":True,
                "no_daily_report_rebuild":True,
                "no_evidence_publishing":True,
                "no_outcome_resolution":True,
                "live_data_separate":True,
            },
        }
