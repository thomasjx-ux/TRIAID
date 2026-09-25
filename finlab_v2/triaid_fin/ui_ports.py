from __future__ import annotations


class MarketPageReadPort:
    """Read-only market-page capability port."""

    version="market-page-read-port@1.0.0"

    def __init__(self,engine)->None:
        self._engine=engine

    @property
    def core_version(self)->str:
        return str(self._engine.core.version)

    @property
    def architecture_version(self)->str:
        return str(self._engine.architecture_version)

    def daily_summary(self,market_id:str,compact:bool=True)->dict:
        return self._engine.daily_summary(market_id,compact=compact)

    def strategy_cards(self,lang:str,market_id:str)->list[dict]:
        return self._engine.strategy_population.strategy_cards(lang,market_id)

    def get_run(self,run_id:str):
        return self._engine.get_run(run_id)

    def latest_decision_run(self,market_id:str):
        return self._engine.latest_decision_run(market_id)

    def all_runs(self):
        return self._engine.all_runs()

    def evidence_eligible_run(self,run)->bool:
        return bool(self._engine._evidence_eligible_run(run))

    def curves(self,market_id:str)->list[dict]:
        return self._engine.curves(market_id)

    def evolution_status(self)->dict:
        return self._engine.evolution_status()


class DailyReportReadPort:
    """Read-only daily-report module capability port."""

    version="daily-report-read-port@1.0.0"

    def __init__(self,engine)->None:
        self._engine=engine

    def summary(self,market_id:str|None=None,compact:bool=False)->dict:
        return self._engine.daily_summary(market_id,compact=compact)

    def all_markets(self,compact:bool=True)->dict:
        return self._engine.daily_reports(compact=compact)


class RiskReadPort:
    """Read-only risk center capability port."""

    version="risk-read-port@1.0.0"

    def __init__(self,engine)->None:
        self._engine=engine

    def warning_latest(self)->dict|None:
        return self._engine.risk_warning_latest()

    def control_latest(self)->dict|None:
        return self._engine.risk_control_latest()


class OutcomeReadPort:
    """Read-only realized outcome access for T0/T1 pairing."""

    version="outcome-read-port@1.0.0"

    def __init__(self,engine)->None:
        self._engine=engine

    @staticmethod
    def _model_dump(value):
        if value is None:
            return None
        if hasattr(value,"model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value,dict):
            return dict(value)
        return None

    def review(self,market_id:str,decision_id:str|None,run_id:str|None=None)->dict|None:
        market=str(market_id).upper()
        if market=="US":
            rows=self._engine.us_return_max_ledger.decisions(5000)
            decision=next((row for row in rows if row.get("decision_id")==decision_id),None)
            if decision is None:
                return None
            return {
                "kind":"ROUTE_REVIEW",
                "review":self._engine.us_return_max_ledger.review_decision(decision),
            }
        if market=="HK":
            rows=self._engine.hk_return_max_ledger.decisions(5000)
            decision=next((row for row in rows if row.get("decision_id")==decision_id),None)
            if decision is None:
                return None
            return {
                "kind":"ROUTE_REVIEW",
                "review":self._engine.hk_return_max_ledger.review_decision(decision),
            }
        resolved_run_id=run_id or decision_id
        if not resolved_run_id:
            return None
        try:
            run=self._engine.get_run(resolved_run_id)
        except Exception:
            return None
        evaluation=self._model_dump(run.evaluation)
        return {
            "kind":"RUN_EVALUATION",
            "run_id":resolved_run_id,
            "status":run.status,
            "outcome_as_of":(run.diagnostic_summary or {}).get("outcome_as_of"),
            "evaluation":evaluation,
            "diagnostic_summary":dict(run.diagnostic_summary or {}),
        }


class UiReadServices:
    """Composition root for UI capability ports.

    New projections should accept the narrow capability port they need.
    Compatibility methods remain while non-projection callers migrate.
    """

    version="ui-read-services@2.0.0"

    def __init__(self,engine)->None:
        self.market_page=MarketPageReadPort(engine)
        self.daily_report=DailyReportReadPort(engine)
        self.risk=RiskReadPort(engine)
        self.outcome=OutcomeReadPort(engine)

    @property
    def core_version(self)->str:
        return self.market_page.core_version

    @property
    def architecture_version(self)->str:
        return self.market_page.architecture_version

    def daily_summary(self,market_id:str,compact:bool=True)->dict:
        return self.daily_report.summary(market_id,compact)

    def daily_reports(self,compact:bool=True)->dict:
        return self.daily_report.all_markets(compact)

    def strategy_cards(self,lang:str,market_id:str)->list[dict]:
        return self.market_page.strategy_cards(lang,market_id)

    def get_run(self,run_id:str):
        return self.market_page.get_run(run_id)

    def latest_decision_run(self,market_id:str):
        return self.market_page.latest_decision_run(market_id)

    def all_runs(self):
        return self.market_page.all_runs()

    def evidence_eligible_run(self,run)->bool:
        return self.market_page.evidence_eligible_run(run)

    def curves(self,market_id:str)->list[dict]:
        return self.market_page.curves(market_id)

    def evolution_status(self)->dict:
        return self.market_page.evolution_status()

    def risk_warning_latest(self)->dict|None:
        return self.risk.warning_latest()

    def risk_control_latest(self)->dict|None:
        return self.risk.control_latest()

    def status(self)->dict:
        return {
            "version":self.version,
            "ports":{
                "market_page":self.market_page.version,
                "daily_report":self.daily_report.version,
                "risk":self.risk.version,
                "outcome":self.outcome.version,
            },
        }
