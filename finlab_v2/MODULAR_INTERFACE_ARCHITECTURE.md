# TRIAID FIN Modular Interface Architecture

Version: modular-interface-architecture@1.0.0

## Goal

TRIAID FIN must optimize for change isolation. A new market, provider, experiment, runtime task, storage backend, or UI module should be added by extending one stable interface boundary rather than editing multiple unrelated modules.

The architecture is intentionally biased toward replaceable modules and explicit contracts instead of tightly coupled convenience calls.

## Dependency direction

Provider adapters -> ProviderRegistry -> MarketDataHub

Market-specific configuration -> MarketInterfaceRegistry

Domain engine -> RuntimeServices / RuntimeJournal -> Scheduler and Runtime Jobs

Domain read models -> UI Projections -> Browser

Storage backend -> repositories / journal ports -> domain and runtime modules

The browser never joins domain APIs into business state.

Runtime orchestration never reaches through the engine to internal modules or concrete storage.

Common infrastructure never switches on literal market IDs to decide behavior.

## Market extension contract

All enabled markets must have one MarketInterfaceProfile.

The profile owns provider chains by data mode, route projection specification, rendered route validation contract, instrument display labels, optional sparse-symbol policy, minimum alignment requirements, product capability declarations, and runtime job assignments.

Adding a market must not require editing MarketDataHub routing control flow, MarketDataAutomation control flow, or MarketPageProjection market-specific branches.

Provider implementations may remain market-specific. Market-specific code belongs inside adapters, not shared orchestration.

## Provider interface

Providers are registered once by name. MarketInterfaceProfile selects ordered provider chains for each capability such as DAILY, INTRADAY, PREOPEN, REALTIME, and QUOTE_L1.

MarketDataHub resolves the registered chain, checks configuration, fetches and validates data, compares freshness, performs failover, manages cache, and aligns panels. It does not decide that a named market must use a named provider.

## Runtime interface

MarketDataAutomation and DecisionScheduler consume RuntimeServices and RuntimeJournal.

RuntimeServices is the stable application port. It exposes orchestration-safe methods and hides EvolutionLabEngine internals.

RuntimeJournal is the stable coordination persistence port. Runtime modules do not depend on Supabase, filesystem storage, or RunStore implementation details.

Market-specific scheduled work is implemented as Runtime Jobs. A MarketInterfaceProfile assigns runtime jobs to a market. The automation loop only executes registered jobs at PRE_REFRESH or POST_REFRESH stages. It does not contain market-ID conditionals.

## UI interface

The market page consumes /api/ui/market-page/{market_id}. High-frequency market state consumes /api/ui/market-page/{market_id}/live. The risk center consumes /api/ui/risk-center.

UI projections own joins, availability state, reason codes, freshness semantics, validation, graceful degradation, and last-known-good compatibility. The browser owns rendering only.

A browser component must not independently fetch multiple domain endpoints and infer whether data are complete.

## Availability semantics

Projection sections use explicit states such as READY, WAITING, STALE, NOT_APPLICABLE, and ERROR.

No unavailable surface may be represented only by an empty object, missing field, dash, or null without an explicit reason.

READY means render-complete according to the registered contract, not merely that a Python object exists.

## Storage boundary

Runtime coordination state uses RuntimeJournal. Domain persistence remains behind storage or repository modules. New runtime code must not access engine.store directly.

A future storage migration should require changes in the adapter layer, not scheduler, runtime jobs, or UI code.

## Agile extension workflow

For every new capability:

1. Define the contract and ownership boundary.
2. Add or extend the adapter, profile, or port.
3. Add a focused contract smoke test.
4. Integrate the module through the registry.
5. Keep existing interfaces backward-compatible where practical.
6. Run full release audit.
7. Deploy one exact Git commit.
8. Require build audit and runtime audit receipts before readiness.

Do not add a UI field first and wire it directly to a domain endpoint. Do not add a provider by inserting market-specific conditions into MarketDataHub. Do not add a scheduled experiment by inserting market-specific conditions into MarketDataAutomation. Do not add scheduler behavior by reaching into EvolutionLabEngine internals.

## Allowed market-specific code

Market-specific behavior is valid when it represents real market rules or a market-specific model. It must live in a replaceable module or profile.

Examples include CN opening-auction provider adapters, HK sparse defensive-instrument handling, US Return-Max route implementation, market-specific trading calendar rules, and strategy universe definitions.

What is prohibited is market-specific branching inside shared transport, orchestration, projection, or persistence infrastructure.

## Release gates

The release audit blocks regressions when an enabled market lacks a MarketInterfaceProfile, provider routes diverge from the profile, runtime jobs are referenced but not registered, common market-data control flow reintroduces literal market branching, runtime or scheduler reaches through self.engine, browser risk or market pages resume multi-domain API joins, projection contracts are missing, or runtime deployment does not expose the audited contracts.

This architecture is the default for all future TRIAID FIN development.
