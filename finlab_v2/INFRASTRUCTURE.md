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

RunStore, Observation, Population State and Evolution should depend on the storage contract, not on a specific persistence technology.

Current backend:
- file

Durability:
- PERSISTENT when mounted under /data
- EPHEMERAL otherwise

Future database/object-storage backends should implement the same external behavior without changing Engine callers.

## Deployment

Current production service remains the validated image-based Railway runtime.

A GitHub-native sidecar deployment was tested but Railway bound the service to the repository default branch (main) instead of the requested research branch, so it was not promoted. Do not change repository default branch merely to satisfy deployment convenience.

## External infrastructure dependency

Railway persistent Volume must be created and mounted at:

/data

The application automatically switches to:

/data/triaid_fin_v2

when the mount exists.

Without the mount, runs, SHADOW evidence, observations, transitions and evolution ledgers are ephemeral across container replacement.

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
