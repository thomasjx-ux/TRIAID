# TRIAID FIN Standalone

This standalone runtime reuses the same FIN kernel as the cloud research path. It does not maintain a forked strategy engine.

## What this runtime changes

- Uses local file storage only by default.
- Recovers stale incomplete runs after a process restart.
- Refreshes the latest complete market evidence for US, CN and HK.
- Refuses to reconstruct missed prospective decisions retrospectively after the computer was off.
- Resumes prospective freezing only from the latest currently observable complete bar.
- Writes every run summary into one report folder as JSON and plain text.
- Keeps one market failure isolated so US, CN and HK do not block each other.
- Uses the risk-aware US route with State Break fast braking and underlying exposure concentration limits.
- Broker execution remains disabled. This is a research runtime.

## Run once

From the repository root, install the existing FIN requirements and run:

`python finlab_v2/standalone/run_triaid_fin.py`

## Keep running

`python finlab_v2/standalone/run_triaid_fin.py --watch`

The polling interval is controlled by `standalone_config.json`. The runtime still freezes only complete daily evidence. Polling more frequently does not turn provisional intraday data into official evidence.

## Local files

Runtime state is written under `standalone_runtime/data` by default.

All user-facing reports are written into the single folder `standalone_runtime/reports`:

- `TRIAID_FIN_YYYYMMDD_HHMMSS.json`
- `TRIAID_FIN_YYYYMMDD_HHMMSS.txt`
- `latest_status.json`

Paths can be changed in `standalone_config.json`.

## Shutdown and restart discipline

If the machine is off for multiple trading sessions, the runtime refreshes the missing market data when it starts again, but it does not create fake historical prospective freezes. Missed decision windows are explicitly recorded as gaps. This preserves the no-hindsight validation discipline.

## Market customization

The `markets` list can contain any subset of `US`, `CN`, and `HK`. Market and strategy-pool customization remain separate from the shared core. The standalone shell must not alter global strategy definitions merely to satisfy a local UI preference.
