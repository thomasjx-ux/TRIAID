# TRIAID FIN Local Desktop Architecture

Version: local-desktop-architecture@0.1.0

## Product definition

TRIAID FIN Local Desktop is a full-function, single-user research workstation. It is not a thin client, reduced backup, or Railway replica.

Baseline rule:
- every currently supported market, strategy, experiment, risk surface, daily report, audit surface, tooltip, table and visualization remains available;
- local-only enhancements are additive;
- no existing UI element is removed merely to simplify desktop packaging.

The desktop product may evolve independently from Railway after the initial functional migration.

## Design invariants

1. Flexible architecture first.
2. Modules communicate through explicit ports/contracts.
3. Desktop shell never imports TRIAID domain internals.
4. Browser/UI code renders UI projections and must not reconstruct business state from unrelated endpoints.
5. Market data refresh, observation, transition, decision, experiment and broker execution remain distinct concepts.
6. Local runtime is single-user and loopback-only by default.
7. Local data and secrets are independent from Railway and cloud persistence.
8. Realtime visibility is additive; official evidence discipline is unchanged.
9. UI completeness is a release gate.
10. Performance optimizations may change transport/caching, not domain semantics.

## Module map

DesktopShell
  -> LocalRuntimeSupervisor
      -> FastAPI application boundary
          -> UI Projections
          -> RuntimeServices
          -> MarketData / Scheduler / Experiments / Risk / Reports
          -> RuntimeJournal / StorageBackend

The shell owns only:
- starting and stopping the local runtime;
- loopback health checks;
- native window lifecycle;
- local window preferences;
- operator-visible startup failures.

The shell must not:
- calculate strategies;
- fetch market data directly;
- mutate experiment state directly;
- read storage files to render business state;
- contain market-specific logic.

## Desktop transport

Initial version reuses the existing complete browser UI inside a native WebView2/WebKit window through pywebview.

Runtime binding:
- host: 127.0.0.1
- public LAN/WAN binding: prohibited by default
- external port forwarding: not required

This gives exact UI continuity while keeping the domain/runtime implementation unchanged.

A later realtime transport may add WebSocket/SSE projections. That transport must sit beside existing projection endpoints and must not remove them.

## Local runtime profile

TRIAID_RUNTIME_PROFILE=local_desktop

Defaults:
- TRIAID_STORAGE_BACKEND=file
- TRIAID_DATA_DIR=<per-user durable application data>/TRIAID-FIN/data
- TRIAID_DATA_AUTOMATION=1
- TRIAID_DECISION_AUTOMATION=1
- TRIAID_STARTUP_MAINTENANCE=1
- TRIAID_LONG_RESEARCH_BOOTSTRAP=1
- TRIAID_RELEASE_AUDIT_REQUIRED=0 at ordinary boot

Release/build audit is required when installing or upgrading a version. It is not re-run on every desktop launch unless requested.

Cloud credentials are optional capabilities, not local-runtime prerequisites.

## UI preservation contract

The local desktop build must preserve:
- homepage and market selector;
- US / CN / HK market pages;
- market clocks and session state;
- strategy selected/candidate tables;
- strategy tooltips and status explanations;
- market-data status and provider information;
- live market window;
- TRIAID decision state and weights;
- validation summary;
- risk warning and risk-control surfaces;
- volatility forecast;
- prospective / recovery / long-cycle / cross-market / latent-hazard experiments;
- economic evolution / value frontier surfaces;
- daily report and capital comparisons;
- curves and posterior evidence;
- manual preview;
- Chinese / English switching;
- all existing degradation/error states.

No feature is considered migrated because its backend exists. It must also remain operable from the desktop UI.

## Local-only realtime workspace

After parity is achieved, Local Desktop adds an additive realtime research workspace:

Market Stream
- latest prices and returns;
- data source, source timestamp and measured staleness;
- intraday volatility and breadth;
- market transition timeline.

TRIAID Stream
- every accepted observation;
- transition score;
- trigger / skip reason;
- state-change confirmation;
- recompute event;
- before/after strategy weights;
- decision layer;
- allocation-action recommendation;
- evidence eligibility.

Experiment Stream
- active shadow/core candidates;
- baseline vs TRIAID;
- counterfactual alternatives;
- missed-opportunity and harmful-intervention flags;
- next validation condition.

All three streams share one event timeline so a market move can be aligned with the exact TRIAID response.

## Performance model

Local Desktop is not designed around Railway resource scarcity.

Performance priorities:
1. fast first paint;
2. market switching without recomputing unrelated sections;
3. live projection refresh independent from heavy full projections;
4. background precomputation of expensive research projections;
5. last-known-good rendering during transient provider failures;
6. bounded event history in the active UI with full history retained on disk;
7. no domain calculations on the UI thread.

Local resources may be used to cache more market history, maintain richer projections and run additional shadow experiments, but formal evidence and shadow evidence remain separate.

## Security boundary

Single user does not mean no security.

Required defaults:
- loopback-only HTTP server;
- no public listener;
- no router port forwarding requirement;
- secrets supplied from local environment/OS credential mechanism;
- no Railway admin token copied into Local Desktop;
- no cloud storage token required for local persistence;
- dedicated data directory outside source tree;
- source updates are explicit version changes, never silent auto-execution of latest main.

The native shell is an operator surface, not an authorization bypass. Mutating API endpoints keep their existing authorization rules until a dedicated local authorization adapter is introduced and audited.

## Reliability

The local workstation must support:
- automatic runtime restart after process failure;
- startup stale-run recovery;
- independent market session scheduling;
- recovery after network loss;
- duplicate-event prevention through source timestamps/idempotency;
- catch-up after reboot;
- health, audit and storage status;
- local backup/export.

A PC staying on is assumed for continuous operation, but continuity must not depend on perfect uptime.

## Migration phases

Phase 1: parity shell
- native desktop shell;
- loopback runtime;
- local durable storage;
- all current UI preserved.

Phase 2: local independence
- remove Railway identity assumptions from local status;
- local backup/recovery;
- local installer/update package;
- local calendar/provider configuration.

Phase 3: realtime research workspace
- push-based live projections;
- synchronized Market/TRIAID/Experiment timeline;
- richer local charts and replay.

Phase 4: research expansion
- parallel candidate cores;
- higher-frequency shadow experiments;
- replay and ablation laboratory;
- additional market/provider modules through registries.

## Release gates

A Local Desktop release is blocked when:
- any previously available UI surface disappears;
- a market page requires Railway-specific infrastructure;
- local runtime binds beyond loopback without explicit opt-in;
- the shell contains domain or market-specific business logic;
- local experiments can contaminate Railway/cloud production persistence by default;
- a heavy UI refresh blocks market automation;
- formal and shadow evidence are mixed;
- restart/recovery can duplicate official evidence.

