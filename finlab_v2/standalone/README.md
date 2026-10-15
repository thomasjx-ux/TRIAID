# TRIAID FIN Standalone

The standalone runtime reuses the existing FIN kernel and adds a detachable local Adaptive V3.6 sidecar. The legacy standalone core remains intact. If the adaptive sidecar fails, the base standalone experiment continues and the new capability degrades to SHADOW/OFF instead of creating a root core failure.

## Current architecture

Formal core remains:

`PREPARE -> FREEZE -> SETTLE -> COMPARE`

Adaptive V3.6 adds a non-gating local plane around it:

- pre-open warning and lead-time capture;
- local Price State transitions;
- append-only Evidence Events and Adaptive Epoch audit trail;
- dynamic driver universe governance;
- per-session policy/universe binding;
- pre-open brief generation;
- executable shadow action levels with reference, trigger, execution range, invalidation and targets;
- observation-time discipline so source time is never backdated into decision time.

Broker execution remains disabled. All action levels are research/shadow outputs.

## Dynamic driver universe

Driver membership and weights are not permanent. Each market follows:

`CANDIDATE -> PROBATION -> ACTIVE -> WATCH -> EXITED`

Admission and retention thresholds are separate so the pool does not churn on small ranking changes. A candidate must pass hard data/tradability gates and sustain the market-specific admission band through the configured probation period. ACTIVE members that fall below the retention band move to WATCH and exit only after the configured repeated failure count. A hard-gate failure exits on the next bound session. EXITED names may re-enter through PROBATION later.

The score surface includes index/free-float weight when available, market impact, propagation, liquidity, incremental information and stability. Missing index-weight data is treated as missing evidence rather than silently set to zero; the composite score renormalizes over available verified dimensions. Driver importance and purchase eligibility remain separate.

## Entrypoints

Run once:

`python finlab_v2/standalone/run_triaid_fin_v36.py`

Keep running:

`python finlab_v2/standalone/run_triaid_fin_v36.py --watch`

Windows:

`finlab_v2\standalone\start_windows.bat`

Launcher check:

`finlab_v2\standalone\start_windows.bat --check`

The Windows launcher now validates the Adaptive V3.6 smoke before declaring the standalone runtime ready.

## Local cadence

The old core cadence remains controlled by `poll_minutes`. Adaptive V3.6 uses its own event-observation cadence:

- `adaptive_v36.poll_seconds_active` while a market is PREOPEN or OPEN;
- `adaptive_v36.poll_seconds_idle` otherwise.

These are wake opportunities, not business-time gates. State changes, not exact clock minutes, create new adaptive records.

## Local files

Base runtime state remains under `standalone_runtime/data` and reports under `standalone_runtime/reports` by default.

Adaptive V3.6 additionally creates:

- `standalone_runtime/data/adaptive_v36.sqlite3`
- `standalone_runtime/reports/adaptive_v36_latest_status.json`
- `standalone_runtime/reports/adaptive_v36_runtime.jsonl`

Critical adaptive tables are append-only at the SQLite trigger layer.

## Pre-open action levels

A concrete stock/ETF suggestion is executable only when the local record has a VERIFIED price anchor and prospective fields for:

- reference price/time/source;
- trigger price/type;
- execution range;
- invalidation price;
- validity window;
- at least one target price for opportunity actions.

If a legal price anchor is unavailable, the object remains observation/PRICE_PENDING and no price is invented.

## Market and strategy customization

`standalone_config.json` is the local control surface.

- `markets` selects any subset of `US`, `CN`, `HK`.
- strategy-pool and account controls keep their existing behavior.
- `adaptive_v36.driver_governance.markets.<MARKET>.candidates` defines the candidate search seed, not a permanent ACTIVE list.
- market-specific `admission_percentile`, `retention_percentile`, `probation_sessions`, and `exit_reviews` are versionable local policy inputs.
- `adaptive_v36.action_levels` versions the prospective shadow execution-level rule.

US currently ships with the previously used seven names only as initial candidate seeds. CN/HK are intentionally not populated with arbitrary permanent names; their candidate seeds can be added locally and are still subject to the same admission/exit lifecycle.

## Shutdown and restart discipline

If the machine is off for multiple sessions, missing prospective decisions are not reconstructed. The base runtime resumes from current evidence; Adaptive V3.6 likewise records only what was actually observed after restart. Source timestamps and observed timestamps remain separate.
