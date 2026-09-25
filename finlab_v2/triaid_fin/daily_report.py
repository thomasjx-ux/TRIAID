from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from .daily_experiment_intelligence import build_cross_market_learning, build_market_intelligence
from .market_registry import MARKET_REGISTRY
from .trading_calendar import official_session_phase, trading_day_info


class DailyReportModule:
    """Registry-driven and market-phase-aware daily-report composition.

    Coverage follows the Market Registry, while content maturity follows each
    market's own official calendar and session clock. Formal completed-session
    evidence is kept separate from pre-open, intraday, break and post-close
    working context so markets in different time zones never appear to have the
    same data maturity merely because they share one aggregate report surface.
    """

    version = "daily-report@1.1.0"

    def __init__(
        self,
        *,
        market_ids_provider: Callable[[], Iterable[str]],
        all_runs_provider: Callable[[], list],
        review,
        store,
        market_section_providers: Mapping[str, Callable[..., dict]] | None = None,
        now_provider: Callable[[str], datetime] | None = None,
    ) -> None:
        self._market_ids_provider = market_ids_provider
        self._all_runs_provider = all_runs_provider
        self._review = review
        self._store = store
        self._now_provider = now_provider
        self._market_section_providers = {
            str(key).upper(): value
            for key, value in dict(market_section_providers or {}).items()
        }

    def market_ids(self) -> tuple[str, ...]:
        return tuple(str(x).upper() for x in self._market_ids_provider())

    def _now(self, market_id: str) -> datetime:
        if self._now_provider is not None:
            value = self._now_provider(market_id)
            if value.tzinfo is None:
                return value.replace(tzinfo=ZoneInfo(MARKET_REGISTRY.get(market_id).timezone))
            return value.astimezone(ZoneInfo(MARKET_REGISTRY.get(market_id).timezone))
        return datetime.now(ZoneInfo(MARKET_REGISTRY.get(market_id).timezone))

    @staticmethod
    def _formal_candidate(run) -> bool:
        metadata = run.market.metadata or {}
        return (
            run.market.snapshot_id != "PENDING"
            and metadata.get("evidence_eligible") is not False
            and str(metadata.get("run_scope") or "OFFICIAL_EVIDENCE") != "MANUAL_PREVIEW"
            and run.status != "PREVIEW_READY"
            and metadata.get("daily_bar_complete") is not False
        )

    @staticmethod
    def _content_profile(
        phase: str,
        *,
        is_trading_day: bool,
        close_finalized: bool,
        current_session_formal: bool,
        calendar_known: bool,
    ) -> tuple[str, str, list[str]]:
        phase = str(phase or "").upper()
        if not calendar_known or phase == "CALENDAR_UNAVAILABLE":
            return (
                "CALENDAR_DEGRADED",
                "DEGRADED",
                ["latest_formal_daily", "data_quality_warning"],
            )
        if not is_trading_day:
            return (
                "NON_TRADING_DAY_LATEST_FINAL",
                "FINAL_HISTORICAL",
                ["latest_formal_daily", "cross_market_updates", "risk_update"],
            )
        if phase == "PREOPEN":
            return (
                "PREOPEN_BRIEF",
                "PREVIOUS_FINAL_PLUS_PREOPEN",
                ["previous_close_review", "preopen_baseline", "today_watchlist", "risk_update"],
            )
        if phase == "OPEN":
            return (
                "LIVE_INTRADAY_UPDATE",
                "LIVE_PARTIAL",
                ["previous_close_realized_scorecard", "current_market_state", "intraday_strategy_changes", "live_risk", "next_decision_conditions"],
            )
        if phase == "BREAK":
            return (
                "MIDSESSION_BREAK_UPDATE",
                "LIVE_PARTIAL",
                ["previous_close_realized_scorecard", "morning_session_review", "current_strategy_state", "live_risk", "afternoon_watchlist"],
            )
        if phase == "POSTCLOSE":
            if close_finalized or current_session_formal:
                return (
                    "FINAL_DAILY",
                    "CURRENT_SESSION_FINAL",
                    ["session_review", "realized_scorecard", "intervention_attribution", "strategy_analysis", "risk_review", "next_session_plan"],
                )
            return (
                "POSTCLOSE_SETTLING",
                "CLOSE_PENDING",
                ["provisional_close_state", "pending_close_freeze", "previous_formal_scorecard", "risk_update"],
            )
        if phase == "CLOSED":
            if close_finalized or current_session_formal:
                return (
                    "FINAL_DAILY",
                    "CURRENT_SESSION_FINAL",
                    ["session_review", "realized_scorecard", "intervention_attribution", "strategy_analysis", "risk_review", "next_session_plan"],
                )
            return (
                "OFF_SESSION_LATEST_FINAL",
                "PREVIOUS_FINAL",
                ["latest_formal_daily", "cross_market_updates", "risk_update"],
            )
        return (
            "UNKNOWN_SESSION_STATE",
            "DEGRADED",
            ["latest_formal_daily", "data_quality_warning"],
        )

    def _session_dates(
        self,
        all_rows: list,
        fallback_date: str | None,
    ) -> tuple[dict, dict, list[dict]]:
        decision_events = self._store.read_jsonl("decision_events.jsonl", limit=10000)
        working_dates: dict[str, str | None] = {}
        formal_dates: dict[str, str | None] = {}
        for market in self.market_ids():
            market_events = [
                row
                for row in decision_events
                if str(row.get("market_id") or "").upper() == market
                and row.get("session_date")
            ]
            market_rows = [
                row
                for row in all_rows
                if str(row.market.market_id).upper() == market and row.market.as_of
            ]
            working_dates[market] = (
                str(market_events[-1].get("session_date"))
                if market_events
                else (
                    str(market_rows[-1].market.as_of)
                    if market_rows
                    else fallback_date
                )
            )

            close_final_dates = sorted({
                str(row.get("session_date"))
                for row in market_events
                if str(row.get("event_type") or "").upper() == "CLOSE_FINAL"
                and row.get("session_date")
            })
            formal_run_dates = sorted({
                str(row.market.as_of)
                for row in market_rows
                if self._formal_candidate(row)
            })
            formal_dates[market] = (
                close_final_dates[-1]
                if close_final_dates
                else formal_run_dates[-1]
                if formal_run_dates
                else None
            )
        return working_dates, formal_dates, decision_events

    def _timing_context(
        self,
        market_id: str,
        *,
        all_rows: list,
        decision_events: list[dict],
        working_date: str | None,
        formal_date: str | None,
    ) -> dict:
        market = str(market_id).upper()
        spec = MARKET_REGISTRY.get(market)
        local_now = self._now(market)
        info = trading_day_info(market, local_now)
        phase = official_session_phase(market, local_now)
        session_date = local_now.date().isoformat()

        session_events = [
            row
            for row in decision_events
            if str(row.get("market_id") or "").upper() == market
            and str(row.get("session_date") or "") == session_date
        ]
        close_finalized = any(
            str(row.get("event_type") or "").upper() == "CLOSE_FINAL"
            for row in session_events
        )
        current_session_formal = bool(formal_date and formal_date == session_date)
        content_profile, maturity, sections = self._content_profile(
            phase,
            is_trading_day=bool(info.get("is_trading_day")),
            close_finalized=close_finalized,
            current_session_formal=current_session_formal,
            calendar_known=bool(info.get("calendar_known")),
        )

        market_rows = [
            row
            for row in all_rows
            if str(row.market.market_id).upper() == market and row.market.as_of
        ]
        latest_available_date = (
            max(str(row.market.as_of) for row in market_rows)
            if market_rows
            else None
        )
        current_session_has_data = latest_available_date == session_date
        working_is_formal = bool(working_date and formal_date and working_date == formal_date)

        return {
            "market_id": market,
            "timezone": spec.timezone,
            "local_iso": local_now.isoformat(),
            "session_date": session_date,
            "session_phase": phase,
            "calendar_known": bool(info.get("calendar_known")),
            "is_trading_day": bool(info.get("is_trading_day")),
            "early_close": bool(info.get("early_close")),
            "early_close_time": info.get("early_close_time"),
            "content_profile": content_profile,
            "data_maturity": maturity,
            "working_report_date": working_date,
            "formal_evidence_date": formal_date,
            "latest_available_data_date": latest_available_date,
            "current_session_has_data": current_session_has_data,
            "current_session_formal": current_session_formal,
            "close_finalized": close_finalized,
            "working_report_is_formal": working_is_formal,
            "content_sections": sections,
            "formal_live_separation": {
                "formal_layer": "Only completed daily evidence may support realized scorecards and cross-market evidence transfer.",
                "live_layer": "Pre-open, intraday, break and unsettled post-close content is working context and must not be presented as finalized daily evidence.",
            },
            "timing_rule": "MARKET_LOCAL_CALENDAR_AND_SESSION_PHASE_CONTROL_REPORT_CONTENT",
        }

    def _apply_market_extension(
        self,
        summary: dict,
        *,
        market_id: str,
        compact: bool,
        all_rows: list,
    ) -> list[dict]:
        provider = self._market_section_providers.get(market_id)
        if provider is None:
            return []
        try:
            extra = provider(
                market_id=market_id,
                compact=compact,
                summary=summary,
                all_rows=all_rows,
            ) or {}
            summary.update(extra)
            return []
        except Exception as exc:
            return [{
                "market_id": market_id,
                "error": f"{type(exc).__name__}:{exc}",
            }]

    def summary(self, market_id: str | None = None, compact: bool = False) -> dict:
        markets = self.market_ids()
        market_key = str(market_id).upper() if market_id is not None else None
        if market_key is not None and market_key not in markets:
            raise KeyError(f"unsupported_market:{market_id}")

        all_rows = list(self._all_runs_provider())
        rows = (
            [r for r in all_rows if str(r.market.market_id).upper() == market_key]
            if market_key is not None
            else all_rows
        )
        summary = self._review.daily_summary(rows, compact=compact)
        working_dates, formal_dates, decision_events = self._session_dates(
            all_rows,
            summary.get("date"),
        )

        if market_key is not None:
            timing = self._timing_context(
                market_key,
                all_rows=all_rows,
                decision_events=decision_events,
                working_date=working_dates.get(market_key),
                formal_date=formal_dates.get(market_key),
            )
            summary["report_timing"] = timing
            summary["experiment_intelligence"] = build_market_intelligence(
                all_rows,
                self._store,
                market_key,
                working_dates.get(market_key) or summary.get("date"),
                events=decision_events,
            )

        # Cross-market learning deliberately uses completed/formal cutoffs, not
        # whichever market happens to be open at request time.
        summary["cross_market_learning"] = build_cross_market_learning(
            all_rows,
            self._store,
            formal_dates,
            events=decision_events,
        )

        extension_errors: list[dict] = []
        if market_key is not None:
            extension_errors.extend(
                self._apply_market_extension(
                    summary,
                    market_id=market_key,
                    compact=compact,
                    all_rows=all_rows,
                )
            )
        else:
            for enabled_market in markets:
                extension_errors.extend(
                    self._apply_market_extension(
                        summary,
                        market_id=enabled_market,
                        compact=compact,
                        all_rows=all_rows,
                    )
                )

        contract = summary.setdefault("report_contract", {})
        contract.update({
            "market_phase_aware": True,
            "formal_and_live_layers_separated": True,
            "cross_market_learning_uses_formal_cutoffs": True,
            "same_clock_time_does_not_imply_same_market_maturity": True,
        })
        summary["report_module"] = {
            "version": self.version,
            "market_id": market_key,
            "enabled_markets": list(markets),
            "coverage_rule": "MARKET_REGISTRY_DRIVEN_NO_SILENT_OMISSION",
            "timing_rule": "MARKET_LOCAL_CALENDAR_AND_SESSION_PHASE_CONTROL_REPORT_CONTENT",
            "market_extension_state": (
                "REGISTERED_EXTENSION"
                if market_key in self._market_section_providers
                else "BASE_REPORT_ONLY"
                if market_key is not None
                else "AGGREGATE"
            ),
            "extension_errors": extension_errors,
        }
        return summary

    def all_markets(self, compact: bool = True) -> dict:
        markets = self.market_ids()
        reports = {
            market: self.summary(market, compact=compact)
            for market in markets
        }
        missing = [market for market in markets if market not in reports]
        extension_errors = [
            error
            for report in reports.values()
            for error in ((report.get("report_module") or {}).get("extension_errors") or [])
        ]
        timing = {
            market: dict((reports.get(market) or {}).get("report_timing") or {})
            for market in markets
        }
        missing_timing = [market for market, row in timing.items() if not row]
        profiles = {
            market: row.get("content_profile")
            for market, row in timing.items()
        }
        phases = {
            market: row.get("session_phase")
            for market, row in timing.items()
        }
        formal_dates = {
            market: row.get("formal_evidence_date")
            for market, row in timing.items()
        }
        working_dates = {
            market: row.get("working_report_date")
            for market, row in timing.items()
        }
        distinct_maturities = {
            row.get("data_maturity")
            for row in timing.values()
            if row.get("data_maturity")
        }
        return {
            "version": self.version,
            "report_type": "INVESTMENT_STRATEGY_DAILY",
            "markets": list(markets),
            "market_count": len(markets),
            "reports": reports,
            "market_timing": timing,
            "timing_alignment": {
                "market_phases": phases,
                "content_profiles": profiles,
                "working_report_dates": working_dates,
                "formal_evidence_dates": formal_dates,
                "mixed_market_maturity": len(distinct_maturities) > 1,
                "alignment_rule": "DO_NOT_FORCE_MARKETS_IN_DIFFERENT_SESSION_PHASES_OR_LOCAL_DATES_INTO_ONE_MATURITY_STATE",
                "cross_market_learning_basis": "FORMAL_COMPLETED_SESSION_CUTOFFS_ONLY",
            },
            "integrity": {
                "passed": not missing and not extension_errors and not missing_timing,
                "registry_market_count": len(markets),
                "report_market_count": len(reports),
                "missing_markets": missing,
                "missing_timing_context": missing_timing,
                "extension_errors": extension_errors,
                "rule": "EVERY_ENABLED_MARKET_MUST_HAVE_A_PHASE_AWARE_DAILY_REPORT",
            },
        }
