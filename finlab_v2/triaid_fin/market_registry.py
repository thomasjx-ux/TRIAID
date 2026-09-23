from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class MarketSpec:
    market_id: str
    benchmark: str
    assets: tuple[str, ...]
    risk_assets: tuple[str, ...]
    defensive_assets: tuple[str, ...]
    currency: str
    reference_capital: float
    base_cost_bps: float
    impact_coefficient_bps: float
    max_participation_adv: float
    timezone: str = "UTC"
    aliases: tuple[str, ...] = ()
    balanced_risk_weight: float = 0.60
    research_indexes: tuple[tuple[str, str], ...] = ()
    primary_index_label: str | None = None
    session_schedule: Mapping[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True


class MarketRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, MarketSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, spec: MarketSpec, *, replace: bool = False) -> MarketSpec:
        key = spec.market_id.strip().upper()
        if not key:
            raise ValueError("market_id cannot be empty")
        if key in self._specs and not replace:
            raise ValueError(f"market already registered: {key}")
        normalized = MarketSpec(
            market_id=key,
            benchmark=spec.benchmark,
            assets=tuple(spec.assets),
            risk_assets=tuple(spec.risk_assets),
            defensive_assets=tuple(spec.defensive_assets),
            currency=spec.currency,
            reference_capital=float(spec.reference_capital),
            base_cost_bps=float(spec.base_cost_bps),
            impact_coefficient_bps=float(spec.impact_coefficient_bps),
            max_participation_adv=float(spec.max_participation_adv),
            timezone=spec.timezone,
            aliases=tuple(a.strip().upper() for a in spec.aliases if a.strip()),
            balanced_risk_weight=float(spec.balanced_risk_weight),
            research_indexes=tuple((str(a), str(b)) for a, b in spec.research_indexes),
            primary_index_label=spec.primary_index_label,
            session_schedule={
                str(k).upper(): tuple((str(a),str(b)) for a,b in v)
                for k,v in dict(spec.session_schedule).items()
            },
            metadata=dict(spec.metadata),
            enabled=bool(spec.enabled),
        )
        self._specs[key] = normalized
        self._aliases[key] = key
        for alias in normalized.aliases:
            existing = self._aliases.get(alias)
            if existing and existing != key:
                raise ValueError(f"market alias collision: {alias} -> {existing}/{key}")
            self._aliases[alias] = key
        return normalized

    def resolve_id(self, value: str) -> str:
        key = str(value).strip().upper()
        resolved = self._aliases.get(key)
        if not resolved or resolved not in self._specs:
            raise KeyError(f"unsupported_market:{value}")
        if not self._specs[resolved].enabled:
            raise KeyError(f"disabled_market:{value}")
        return resolved

    def get(self, value: str) -> MarketSpec:
        return self._specs[self.resolve_id(value)]

    def ids(self, *, enabled_only: bool = True) -> tuple[str, ...]:
        rows = [
            key for key, spec in self._specs.items()
            if spec.enabled or not enabled_only
        ]
        return tuple(sorted(rows))

    def mapping(self) -> Mapping[str, MarketSpec]:
        return MappingProxyType(self._specs)

    def pairwise_market_ids(self) -> tuple[tuple[str, str], ...]:
        return tuple(combinations(self.ids(), 2))

    def snapshot(self) -> dict:
        return {
            "markets": [
                {
                    "market_id": spec.market_id,
                    "currency": spec.currency,
                    "timezone": spec.timezone,
                    "benchmark": spec.benchmark,
                    "assets": list(spec.assets),
                    "risk_assets": list(spec.risk_assets),
                    "defensive_assets": list(spec.defensive_assets),
                    "aliases": list(spec.aliases),
                    "enabled": spec.enabled,
                    "session_schedule":{k:[list(x) for x in v] for k,v in spec.session_schedule.items()},
                    "metadata":dict(spec.metadata),
                }
                for spec in (self._specs[key] for key in self.ids(enabled_only=False))
            ]
        }


MARKET_REGISTRY = MarketRegistry()

for _spec in (
    MarketSpec(
        "US", "SPY", ("SPY", "QQQ", "IWM", "TLT", "GLD"),
        ("SPY", "QQQ", "IWM"), ("TLT", "GLD"),
        "USD", 10_000_000.0, 1.5, 45.0, 0.03,
        timezone="America/New_York",
        aliases=("USA", "US_EQUITY"),
        balanced_risk_weight=0.60,
        research_indexes=(("SP500", "^GSPC"), ("NASDAQ_COMPOSITE", "^IXIC")),
        primary_index_label="SP500",
        session_schedule={
            "PREOPEN":(("04:00","09:30"),),
            "OPEN":(("09:30","16:00"),),
            "POSTCLOSE":(("16:00","20:00"),),
        },
        metadata={"primary_experiment_mode":"US_RETURN_MAX_CAPACITY"},
    ),
    MarketSpec(
        "CN", "510300.SS", ("510300.SS", "510500.SS", "159915.SZ", "512100.SS", "511010.SS"),
        ("510300.SS", "510500.SS", "159915.SZ", "512100.SS"), ("511010.SS",),
        "CNY", 50_000_000.0, 2.5, 60.0, 0.02,
        timezone="Asia/Shanghai",
        aliases=("A", "A_SHARE", "ASHARE", "CN_EQUITY"),
        balanced_risk_weight=0.70,
        research_indexes=(
            ("SHANGHAI_COMPOSITE", "000001.SS"),
            ("CSI300", "000300.SS"),
            ("SHENZHEN_COMPONENT", "399001.SZ"),
            ("CHINEXT", "399006.SZ"),
        ),
        primary_index_label="SHANGHAI_COMPOSITE",
        session_schedule={
            "PREOPEN":(("09:15","09:30"),),
            "OPEN":(("09:30","11:30"),("13:00","15:00")),
            "BREAK":(("11:30","13:00"),),
            "POSTCLOSE":(("15:00","18:00"),),
        },
        metadata={"primary_experiment_mode":"CN_RETURN_MAX_CAPACITY"},
    ),
    MarketSpec(
        "HK", "2800.HK", ("2800.HK", "2828.HK", "3033.HK", "2819.HK"),
        ("2800.HK", "2828.HK", "3033.HK"), ("2819.HK",),
        "HKD", 50_000_000.0, 2.0, 55.0, 0.02,
        timezone="Asia/Hong_Kong",
        aliases=("HKG", "HK_EQUITY"),
        balanced_risk_weight=0.60,
        research_indexes=(("HANG_SENG", "^HSI"), ("HANG_SENG_CHINA_ENTERPRISES", "^HSCE")),
        primary_index_label="HANG_SENG",
        session_schedule={
            "PREOPEN":(("09:00","09:30"),),
            "OPEN":(("09:30","12:00"),("13:00","16:10")),
            "BREAK":(("12:00","13:00"),),
            "POSTCLOSE":(("16:10","19:00"),),
        },
        metadata={"primary_experiment_mode":"HK_RETURN_MAX_CAPACITY"},
    ),
):
    MARKET_REGISTRY.register(_spec)

MARKETS = MARKET_REGISTRY.mapping()


def register_market(spec: MarketSpec, *, replace: bool = False) -> MarketSpec:
    return MARKET_REGISTRY.register(spec, replace=replace)


def normalize_market_id(value: str) -> str:
    return MARKET_REGISTRY.resolve_id(value)


def market_ids() -> tuple[str, ...]:
    return MARKET_REGISTRY.ids()


def market_pairs() -> tuple[tuple[str, str], ...]:
    return MARKET_REGISTRY.pairwise_market_ids()
