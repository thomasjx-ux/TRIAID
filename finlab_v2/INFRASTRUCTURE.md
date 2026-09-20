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
