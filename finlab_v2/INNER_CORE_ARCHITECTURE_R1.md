# TRIAID FIN Inner-Core Architecture Freeze — R1

Version: inner-core-architecture@1.0.0

## Purpose

Long-duration Desktop testing requires the inside of the system to remain stable while providers, storage, reports, GPT exchange, UI, market packs and operating-system integration remain replaceable. This freeze therefore starts from the inside and moves outward.

## Ring 0 — Decision kernel (stable)

The first frozen ring is deliberately small:

- `triaid_constitution.py`
- `triaid_fin/objective.py`
- `triaid_fin/contracts.py`
- `triaid_fin/kernel_contract.py`
- `triaid_fin/core.py`

Ring 0 may depend on Python/Pydantic primitives and the listed Ring-0 modules. It must not depend on persistence, providers, market routes, runtime schedulers, APIs, UI or Desktop integration.

`TriaidCoreModule.decide()` remains the existing decision algorithm. R1 does not change its logic. `CoreParameters` is moved to the pure kernel contract so importing the core no longer imports `EvolutionModule -> RunStore`.

## Ring 1 — Evolution and domain state (stable interfaces, evolvable implementation)

Evolution, evaluation, observations, state, risk, strategy population and experiment ledgers may evolve, but they consume Ring 0 through contracts. They may not make provider/UI concerns part of the kernel contract.

## Ring 2 — Application ports

Runtime, persistence, market-data and feedback functionality must be exposed through narrow ports. New outer capabilities are added through ports/adapters rather than by adding dependencies to Ring 0.

## Ring 3 — Replaceable adapters

Providers, file/SQLite/Parquet persistence adapters, report writers, GPT bundle transports, market packs and OS services belong here. Multiple adapters may coexist and be selected by configuration.

## Ring 4 — Surfaces

Desktop UI, browser UI, CLI, exports and future connectors are projections. They must not become authorities for scientific state.

## Identity rule

Every long-duration experiment must be able to distinguish three identifiers:

1. core version — parameter/evolution lineage;
2. kernel fingerprint — exact Ring-0 source identity;
3. core decision-logic fingerprint — AST identity of `TriaidCoreModule.decide`.

Outer changes are therefore allowed without silently creating a new inner scientific kernel. A change to the decision-logic fingerprint is a Core change and must enter candidate/shadow validation rather than being treated as a packaging update.

## R1 change boundary

R1 changes structure only:

- extracted `CoreParameters` from persistence-aware `evolution.py`;
- added the pure kernel contract;
- added kernel identity/fingerprint reporting utilities;
- added static and executable boundary tests.

No provider, strategy, decision ranking, risk rule, UI or scheduler behavior is intentionally changed in R1.
