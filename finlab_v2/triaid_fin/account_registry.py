from __future__ import annotations

from types import MappingProxyType

from .contracts import AccountProfile, StrategyPoolSpec
from .market_registry import normalize_market_id


class AccountRegistry:
    def __init__(self) -> None:
        self._accounts: dict[str, AccountProfile] = {}
        self._pools: dict[str, StrategyPoolSpec] = {}

    def register_pool(self, pool: StrategyPoolSpec, *, replace: bool = False) -> StrategyPoolSpec:
        key = pool.pool_id.strip()
        if not key:
            raise ValueError("pool_id cannot be empty")
        if key in self._pools and not replace:
            raise ValueError(f"strategy pool already registered: {key}")
        self._pools[key] = pool.model_copy(update={"pool_id": key})
        return self._pools[key]

    def register_account(self, account: AccountProfile, *, replace: bool = False) -> AccountProfile:
        key = account.account_id.strip()
        if not key:
            raise ValueError("account_id cannot be empty")
        if key in self._accounts and not replace:
            raise ValueError(f"account already registered: {key}")
        if account.strategy_pool_id not in self._pools:
            raise KeyError(f"unknown strategy pool: {account.strategy_pool_id}")
        normalized_markets = [normalize_market_id(x) for x in account.allowed_markets]
        self._accounts[key] = account.model_copy(
            update={"account_id": key, "allowed_markets": normalized_markets}
        )
        return self._accounts[key]

    def get_account(self, account_id: str = "GLOBAL") -> AccountProfile:
        try:
            return self._accounts[account_id]
        except KeyError as exc:
            raise KeyError(f"unknown account: {account_id}") from exc

    def get_pool(self, pool_id: str = "GLOBAL") -> StrategyPoolSpec:
        try:
            return self._pools[pool_id]
        except KeyError as exc:
            raise KeyError(f"unknown strategy pool: {pool_id}") from exc

    def resolve_strategy_ids(
        self,
        market_id: str,
        candidate_ids: tuple[str, ...] | list[str],
        *,
        account_id: str = "GLOBAL",
        pool_id: str | None = None,
    ) -> tuple[str, ...]:
        market = normalize_market_id(market_id)
        account = self.get_account(account_id)
        if account.allowed_markets and market not in account.allowed_markets:
            return ()
        pool = self.get_pool(pool_id or account.strategy_pool_id)
        rows = list(dict.fromkeys(str(x) for x in candidate_ids))
        explicit_market = pool.market_strategy_ids.get(market)
        if explicit_market:
            allowed = set(explicit_market)
            rows = [x for x in rows if x in allowed]
        elif pool.allowed_strategy_ids:
            allowed = set(pool.allowed_strategy_ids)
            rows = [x for x in rows if x in allowed]
        denied = set(pool.denied_strategy_ids)
        return tuple(x for x in rows if x not in denied)

    def snapshot(self) -> dict:
        return {
            "accounts": {k: v.model_dump(mode="json") for k, v in self._accounts.items()},
            "strategy_pools": {k: v.model_dump(mode="json") for k, v in self._pools.items()},
        }

    def accounts(self):
        return MappingProxyType(self._accounts)

    def pools(self):
        return MappingProxyType(self._pools)


ACCOUNT_REGISTRY = AccountRegistry()
ACCOUNT_REGISTRY.register_pool(StrategyPoolSpec(pool_id="GLOBAL"))
ACCOUNT_REGISTRY.register_account(
    AccountProfile(
        account_id="GLOBAL",
        strategy_pool_id="GLOBAL",
        objective="MAXIMIZE_NET_RETURN",
    )
)


def register_strategy_pool(pool: StrategyPoolSpec, *, replace: bool = False) -> StrategyPoolSpec:
    return ACCOUNT_REGISTRY.register_pool(pool, replace=replace)


def register_account(account: AccountProfile, *, replace: bool = False) -> AccountProfile:
    return ACCOUNT_REGISTRY.register_account(account, replace=replace)


def account_strategy_ids(
    market_id: str,
    candidate_ids: tuple[str, ...] | list[str],
    *,
    account_id: str = "GLOBAL",
    pool_id: str | None = None,
) -> tuple[str, ...]:
    return ACCOUNT_REGISTRY.resolve_strategy_ids(
        market_id, candidate_ids, account_id=account_id, pool_id=pool_id
    )
