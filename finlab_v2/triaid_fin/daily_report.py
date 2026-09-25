from __future__ import annotations

from typing import Callable, Iterable, Mapping

from .daily_experiment_intelligence import build_cross_market_learning, build_market_intelligence


class DailyReportModule:
    """Registry-driven daily-report composition module.

    The module owns daily-report assembly and market coverage. It deliberately
    reads the enabled market list from the Market Registry provider instead of
    hard-coding market IDs in the report or UI layer.

    Market-specific route sections are optional extensions. A newly registered
    market therefore still receives a base daily report even before a dedicated
    route extension exists, which prevents silent omission from the report.
    """

    version = "daily-report@1.0.0"

    def __init__(
        self,
        *,
        market_ids_provider: Callable[[], Iterable[str]],
        all_runs_provider: Callable[[], list],
        review,
        store,
        market_section_providers: Mapping[str, Callable[..., dict]] | None = None,
    ) -> None:
        self._market_ids_provider = market_ids_provider
        self._all_runs_provider = all_runs_provider
        self._review = review
        self._store = store
        self._market_section_providers = {
            str(key).upper(): value
            for key, value in dict(market_section_providers or {}).items()
        }

    def market_ids(self) -> tuple[str, ...]:
        return tuple(str(x).upper() for x in self._market_ids_provider())

    def _session_dates(self, all_rows: list, fallback_date: str | None) -> tuple[dict, list[dict]]:
        decision_events = self._store.read_jsonl("decision_events.jsonl", limit=10000)
        session_dates = {}
        for market in self.market_ids():
            market_events = [
                row
                for row in decision_events
                if str(row.get("market_id") or "").upper() == market
                and row.get("session_date")
            ]
            session_dates[market] = (
                str(market_events[-1].get("session_date"))
                if market_events
                else next(
                    (
                        r.market.as_of
                        for r in reversed(all_rows)
                        if str(r.market.market_id).upper() == market and r.market.as_of
                    ),
                    fallback_date,
                )
            )
        return session_dates, decision_events

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
        session_dates, decision_events = self._session_dates(
            all_rows,
            summary.get("date"),
        )

        if market_key is not None:
            summary["experiment_intelligence"] = build_market_intelligence(
                all_rows,
                self._store,
                market_key,
                session_dates.get(market_key) or summary.get("date"),
                events=decision_events,
            )
        summary["cross_market_learning"] = build_cross_market_learning(
            all_rows,
            self._store,
            session_dates,
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

        summary["report_module"] = {
            "version": self.version,
            "market_id": market_key,
            "enabled_markets": list(markets),
            "coverage_rule": "MARKET_REGISTRY_DRIVEN_NO_SILENT_OMISSION",
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
        return {
            "version": self.version,
            "report_type": "INVESTMENT_STRATEGY_DAILY",
            "markets": list(markets),
            "market_count": len(markets),
            "reports": reports,
            "integrity": {
                "passed": not missing and not extension_errors,
                "registry_market_count": len(markets),
                "report_market_count": len(reports),
                "missing_markets": missing,
                "extension_errors": extension_errors,
                "rule": "EVERY_ENABLED_MARKET_MUST_HAVE_A_DAILY_REPORT",
            },
        }
