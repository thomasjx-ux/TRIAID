# TRIAID FIN V2 Infrastructure Contracts

## Goal

Infrastructure should make research iteration cheaper without coupling data refresh, state transition, TRIAID adjustment, and trading.

The invariant is:

Market Data Refresh != Observation != Transition != Decision != Trade

## Module boundaries

### app.py
Composition only:
- constructs the engine
- constructs automation
- installs routers
- exposes the dashboard and research APIs

Market-data scheduling and provider logic must not be added here.

### market_runtime.py
Owns automatic refresh cadence only.

It may:
- decide when to refresh data
- call Market Data
- record observations

It must not:
- call Strategy Population
- call TRIAID Core
- create trading runs
- change weights

### market_api.py
Owns Market Data HTTP endpoints and input validation.

It must not contain provider-specific logic.

### provider_registry.py
Maps capabilities to providers.

Adding a new market-data vendor should normally require:
1. provider adapter
2. registry registration
3. capability/product declaration
4. provider contract tests

TRIAID Core must not import a vendor adapter.

### market_data.py
Standardizes provider output into market-data contracts and explicit capability status.

Unavailable execution data must remain unavailable. Never infer L1/L2/order-book/fills from close prices.

### observation.py
Stores point-in-time observations and derives research-only transition records.

Transition records must keep:
- research_only = true
- action_generated = false

until a separately validated intervention policy exists.

### storage_backend.py
Owns physical storage backend selection.

RunStore, Observation, Population State and Evolution depend on the storage contract, not on a specific persistence technology.

Supported backends:
- supabase: current production backend; persistent across Railway container replacement
- file: local/offline fallback; persistent only when its root is on a durable mounted volume

Production durability:
- TRIAID_STORAGE_BACKEND=supabase
- persistence is verified by the external Supabase backend health/probe contract
- Railway local volumes are not required for the current production service

File-backend durability:
- PERSISTENT when rooted under a verified durable mount such as /data
- EPHEMERAL otherwise

New storage implementations must preserve the same external behavior without changing Engine callers.

## Deployment

Current production service remains the validated image-based Railway runtime. The start command downloads one explicit Git commit and logs TRIAID_SOURCE_COMMIT before starting the application. The deployed source revision is also exposed through /health and /api/status via the deployment identity contract.

A GitHub-native sidecar deployment was tested but Railway bound the service to the repository default branch (main) instead of the requested research branch, so it was not promoted. Do not change repository default branch merely to satisfy deployment convenience.

Production Python dependencies are exact-version pinned in requirements.txt. Dependency upgrades are treated as audited code changes: update the pins, run the full CI regression suite, then redeploy the explicit validated commit.

## External infrastructure dependency

Current production persistence is external Supabase storage, not a Railway volume. Required production configuration includes:
- TRIAID_STORAGE_BACKEND=supabase
- TRIAID_SUPABASE_PERSISTENCE_URL
- TRIAID_SUPABASE_TOKEN

A Railway /data volume is only required when intentionally operating the file backend with durable local storage.

## Required regression gates

Every infrastructure change must keep passing:
- selftest.py
- bootstrap_live.py for US and CN
- ui_smoke.py
- market-data capability boundaries
- Observation/Transition no-action invariant
- existing strategy registry and lifecycle tests


## Frequency policy

Observation/update frequency is independent from TRIAID adjustment frequency.

Default discipline:
1. Start at the highest effective frequency supported by the source.
2. Do not downgrade on intuition or polling cost alone.
3. Require enough prospective/replay samples and confidence.
4. Compare the higher frequency's marginal net return/information value against the next lower frequency.
5. If marginal value is not positive, step down exactly one level.
6. Re-evaluate after the step-down.
7. If higher-frequency marginal value returns, step up exactly one level.
8. Manual lock/override is always available.

Current ladders:
- REALTIME: 60, 120, 300, 600, 900, 1800 seconds
- INTRADAY: 300, 600, 900, 1800, 3600 seconds
- PREOPEN: 300, 600, 900, 1800 seconds
- DAILY: 600, 1800, 3600 seconds

The frequency controller changes observation cadence only. It does not create runs, alter strategy weights, or trigger trades.


## Automatic research decision scheduler

Decision automation is a separate layer from Market Data refresh and from broker execution.

Lifecycle:

1. PREOPEN
   - establish one baseline per market session;
   - CN explicitly labels the baseline as prior-close based while no authorized call-auction feed is connected.

2. OPEN
   - fresh observations may produce Transition records;
   - during the first 20 valid transitions, use high-sensitivity warmup and evaluate each fresh transition;
   - after warmup, salience is adaptive: compare the current transition score with rolling median + MAD and also detect direction/breadth state changes;
   - only accepted transitions invoke a research-only TRIAID Core recomputation;
   - these recomputations are stored in decision_events.jsonl, not sent to a broker.

3. BREAK
   - no research decision is generated.

4. POSTCLOSE
   - on a fresh DAILY source timestamp, execute a normal daily research run;
   - resolve the prior daily outcome when available;
   - update Population State exactly once per market date;
   - if the provider has not published the new daily bar yet, record waiting state and retry only after the source timestamp changes.

Hard invariants:

- Data Refresh != Observation != Transition != Research Decision != Broker Trade.
- broker_execution_enabled is always false in V2.
- Population State daily advancement is idempotent by DAILY:<date>.
- provider boundaries cannot create Transition records.
- duplicated source timestamps cannot create duplicate research decisions.
- incomplete CREATED/FETCHING_DATA runs from a terminated process are marked FAILED at the next startup rather than left indefinitely pending.

APIs:

- GET /api/decision-scheduler/status
- GET /api/decision-scheduler/events


## Official trading calendar

Automatic market refresh is gated by a dedicated official-calendar module.

Sources:
- US: NYSE Holidays & Trading Hours. Embedded coverage: 2026, 2027, 2028.
- CN: Shanghai Stock Exchange and Shenzhen Stock Exchange official 2026 holiday closure notices. Embedded coverage: 2026.

Rules:
- weekends are closed;
- official exchange holidays are closed;
- US official early-close dates shorten the OPEN phase to 13:00 ET;
- CLOSED and CALENDAR_UNAVAILABLE phases schedule no automatic market-data refresh;
- a year without an embedded official exchange calendar is fail-closed as CALENDAR_YEAR_UNAVAILABLE. The system does not guess future holiday dates.

Calendar lookup APIs:
- GET /api/market-data/trading-calendar
- GET /api/market-data/trading-calendar/{market_id}?date=YYYY-MM-DD

The exchange calendar gates scheduling only. It does not alter historical market data or infer unavailable session data.


## Autonomous official-calendar maintenance

The official exchange calendar is self-maintaining.

Module:
- `trading_calendar_sync.py`

Runtime:
1. Load the last verified dynamic calendar from persistent storage at process start.
2. Keep the embedded verified calendar as a safe fallback.
3. Poll official exchange sources every six hours, with an internal interval guard.
4. US: parse the NYSE Holidays & Trading Hours table and validate the number and structure of official closures/early closes.
5. CN: discover the annual holiday notice independently on SSE and SZSE, parse both notices, and promote a year only when the two official closure sets match exactly.
6. Persist each verified year and source SHA-256 to Supabase.
7. Hot-load newly verified years without requiring a code deployment.
8. If a source is unavailable, parsing fails, or SSE/SZSE disagree, preserve the last verified calendar and never replace it with inferred dates.
9. If a year has neither an embedded nor a dynamically verified calendar, the market is fail-closed as `CALENDAR_YEAR_UNAVAILABLE`.

Operational endpoints:
- GET /api/market-data/trading-calendar-sync
- POST /api/market-data/trading-calendar-sync?force=true

The temporary deployment smoke server has calendar sync disabled so external exchange-site availability cannot make application deployment flaky. The production server runs calendar sync independently from Market Data automation and Decision Scheduler.
