# TRIAID FIN Local Desktop — full-function research workstation

This is a separate, **single-user local product** built on the most recently
merged, reviewed FIN core. It does not require Railway or Supabase. The complete
original three-market UI runs alongside an additional local realtime research
workbench. The backend remains alive when the desktop window is closed.

## Windows 10/11 installation (Python 3.12, x64)

1. Use a release ZIP from the **TRIAID Local Desktop** GitHub Actions build;
   verify the SHA-256 file shipped with that ZIP.
2. Extract the archive to a private local folder, keeping the finlab_v2 folder.
3. Verify Windows Edge WebView2 Runtime is installed.
4. Open PowerShell from finlab_v2 and run:
   .\local_desktop\install.ps1
5. Launch the "TRIAID FIN Desktop" shortcut created on your desktop.

The installer creates a per-user virtual environment, installs direct-version
pinned dependencies from the bundled offline Windows wheelhouse if available,
runs desktop/API security and UI-parity tests, and creates a data durability
checkpoint. It also installs a per-user login-startup supervisor (unless
-NoAutostart is passed). No administrator rights or public listener is needed.

This is a development ZIP, NOT a code-signed Windows installer. Never disable
Windows protections or give administrator credentials to install this package.
Before sensitive use, confirm package origin, verify hashes and enable
BitLocker/full-disk encryption.

## Developer startup

From finlab_v2 with Python 3.12:
    python -m pip install -r requirements-desktop.txt
    python -m local_desktop.smoke
    python -m local_desktop.integration_smoke
    python -m local_desktop.durability
    python -m local_desktop.client

Background/headless runtime:
    python -m local_desktop.supervisor

The GUI and background runtime have separate lifecycles. The first GUI launch
also starts the background server if it is absent. Never start multiple server
instances for the same data directory.

## Current UI

- "完整原版界面": original unmodified FIN V2 page: all US/CN/HK market pages,
  strategies, tooltip content, risk and evolution modules, daily reports,
  experiments, controls, tables and visualizations.
- "实时研究实验台": live indicators, actual observed price curves, source
  timestamp and freshness, asset table, select-and-inspect market/TRIAID
  timeline, original event evidence.
- The research screen listens to local server-sent **invalidation signals**.
  A periodic fallback checks for stale data. A browser refresh is NOT an
  exchange tick: data freshness still depends on each provider's permission,
  source publication and TRIAID's separate decision frequency.

## Security

The server listens only on 127.0.0.1 and requires an OS-user-scoped local
session for every original endpoint. It validates Host and Origin, blocks
foreign-site requests, and adds the existing admin token only inside the
trusted server. There is no JavaScript-to-Python native bridge. Cloud tokens
and Railway deployment identity are stripped before app import. All experiment
data and generated session tokens stay under the user's local data folder.
The repository remains publicly visible: never add proprietary algorithms,
API keys, real account data or personal secrets here.

## Persistence and operational limits

Each server start verifies the local data directory. The first installation
and subsequent server start must pass the two-independent-start marker test.
The local file backend flushes critical writes to disk. This establishes
process-restart persistence only; it does NOT certify survival of sudden
power loss or successful off-device backup restoration. Disk encryption,
independent encrypted backups, a UPS, and a restoration drill remain required.

Windows login startup is not pre-login system-service startup. An unattended
machine must remain logged in or use a separately audited operating-system
service configuration. The server supervisor retries after crashes; network
outages and unavailable provider feeds remain explicit, not silently filled.

The existing official market calendars and release contract apply. The
2026 US/CN/HK calendars are present; later-year HK synchronization still needs
an independently verified official-feed adapter. Do not guess unknown holiday
dates. Data refresh, observation, TRIAID intervention and broker execution
remain strictly separate; broker execution remains disabled.

## Release gate

The desktop is **not certified** merely because source exists. Release checks:
- pinned source revision from latest approved main core;
- Linux full original regression/release audit;
- Windows security, local-storage, API integration and UI-parity smoke tests;
- single-instance background operation and restart demonstration;
- three-market calendar, live-source and risk/strategy function checks;
- long-duration PC run, power-loss recovery and encrypted backup restore
  before replacing any continuous production evidence chain.

The GitHub Actions ZIP includes a SHA-256 sidecar and source revision file.
The source package and generated GitHub Actions artifact are NOT digitally signed.

GPT analysis is a separate, explicitly authorized export/connector workflow.
The local runtime cannot push directly into an arbitrary ChatGPT conversation.
