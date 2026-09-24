# TRIAID FIN Market Page Projection Contract

Version: market-page-projection@1.2.0

## Purpose

The market UI must never assemble business state by independently joining daily, strategy, run, live-market, scheduler, capacity, and posterior APIs in the browser.

All US, CN, and HK market pages consume one server-side Market Page Projection contract. The projection layer is the boundary between domain modules and presentation.

## Non-negotiable invariants

1. Single page source of truth

The full market page reads /api/ui/market-page/{market_id}. High-frequency refresh reads /api/ui/market-page/{market_id}/live.

Legacy domain APIs may remain for research, compatibility, or diagnostics, but the page must not use them to reconstruct its own state.

2. Explicit availability state

Every section is one of READY, WAITING, STALE, NOT_APPLICABLE, or ERROR. Every non-READY section must provide a machine-readable reason. An unexplained blank section is a contract violation.

3. READY means render-complete

READY is not equivalent to an object existing. Every field required by the rendered surface must satisfy its type and numeric contract before the section can be READY.

For the US Return-Max route this includes frozen decision identity and timestamp, strategy weights, Generic Core control weights, all underlying ETF weights, displayed state-return estimates, cash residual weight, execution parameters, exactly four USD capital sleeves, and every rendered sleeve field.

If realized posterior evidence is READY, all rendered posterior sleeve and path fields must also be complete.

4. Intraday and formal posterior are separate evidence layers

Intraday prices, transition states, and recomputations can never be presented as realized posterior returns.

The live projection is strict during an active session. The full page is resilient: a transient live-data failure degrades the live surface without erasing valid frozen strategy, route, capacity, or historical evidence.

5. Fail closed on structural incompleteness

If a required full-page contract is structurally incomplete, the projection endpoint returns a failing integrity result and the HTTP endpoint rejects it instead of returning a superficially successful page full of dashes.

The browser cache may preserve the last valid projection. It must not replace valid state with a structurally incomplete refresh.

6. Server owns joins and semantics

The projection layer owns source joins, availability semantics, evidence-layer separation, timestamps and provenance, completeness validation, and graceful degradation. The browser owns rendering only.

## Release gates

A release is blocked if the frontend resumes direct multi-API fanout for core page state, a non-READY section lacks a reason, a required section is not READY, an active-session live contract is stale or incomplete, a rendered US Return-Max field is missing, the four capital sleeves are incomplete, projection integrity is not frontend-safe, or runtime does not expose the same contract that passed build audit.

## Extension rule

New markets, new page modules, and new columns must first extend the projection contract and its validator, then add rendering. A UI element may not be added first and wired directly to a domain API afterward.

Architecture boundary:

domain modules -> market page projection -> UI

Forbidden regression:

domain modules -> many browser fetches -> implicit browser joins -> partial page
