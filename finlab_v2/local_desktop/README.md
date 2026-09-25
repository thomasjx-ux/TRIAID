# TRIAID FIN Local Desktop — development branch

This branch adds a single-user native desktop wrapper around the COMPLETE existing finlab_v2 application. The legacy application and original HTML are not copied or rewritten. The desktop workbench embeds the original home route and adds a second local-only real-time research view.

**Status: development scaffold.** This commit is not an audited release and the complete three-market regression suite has not yet passed on a Windows PC. Railway production is not altered by this branch.

## Deployment design

- Runtime: the original FastAPI/TRIAID engine and every existing API, strategy, market page, risk module and experiment.
- Desktop shell: pywebview 6.2.1 using Microsoft Edge WebView2 on Windows. The original dashboard loads from the untouched root route, retaining all original UI elements. The additional Live Lab is a separate view.
- Scheduler: one local Uvicorn worker independently runs the existing US/CN/HK market automation, official calendar sync and decision scheduler.
- Storage: dedicated local file backend under the signed-in user's data directory. The cloud Supabase endpoint and token are explicitly discarded by the local launcher.
- Security: 127.0.0.1 only; exact Host and Origin checks; session cookie is HttpOnly and SameSite Strict; no public ports, no embedded cloud admin credentials, no injected Python-to-JavaScript API bridge. All existing API requests require the local session. The local admin header is added by the trusted HTTP server rather than exposed in browser JavaScript.
- Process lifecycle: the backend starts independently from the desktop shell. Closing the UI does not terminate data collection or experiments.

## First-time development startup

On Windows use Python 3.12 and install the Microsoft Edge WebView2 Runtime, then from finlab_v2:

    python -m pip install -r requirements-desktop.txt
    python -m local_desktop.smoke
    python -m local_desktop.client

On a supported Linux desktop, install the pywebview GTK/WebKit runtime prerequisites before starting the native UI. For headless operation run:

    python -m local_desktop.server

The first client launch starts the local backend if necessary and reads its signed health proof. The current authenticated server uses port 8765, changeable with TRIAID_DESKTOP_PORT before starting both client and server. Run only one backend per data directory. A different service occupying the expected port is treated as an error.

For unattended Windows operation, set a per-user Task Scheduler task at logon that starts python -m local_desktop.server with working directory finlab_v2. Do not run the backend as a privileged service or expose the port outside loopback.

## Design constraints

1. Keep the original app.py and its UI as the compatibility baseline; never trade away existing interaction for the Live Lab.
2. Add new visualizations through independent route/view modules instead of adding more tightly coupled business logic to app.py.
3. Data refresh does not equal TRIAID decision, and research decisions never imply broker execution.
4. Every market datum and research result must retain source time, version, experiment mode and completeness. Do not invent realtime values from browser refreshes.
5. Freeze a commit and pass local end-to-end smoke, offline replay, official-calendar checks, restart recovery, permission checks and full legacy UI checks before labeling a local release.
6. The existing file backend's cloud mount durability probe currently labels local PC directories EPHEMERAL; introduce a separately validated local-disk durability probe before production use. Do not misreport a successful write as proof of power-loss recovery.
7. GPT analysis requires an authorized outbound report/export connector. The local runtime does not inject files directly into a ChatGPT conversation.
8. The historical GitHub repository is publicly visible. Store no keys, private credentials, unreleased proprietary implementation, or real data in this branch. Move private-only source to a private repository before adding it.

## Research screens

- Full original workbench: entire legacy dashboard, all US/CN/HK pages, all tables/tooltips, risk and evolution views, bilingual existing controls.
- Live Lab: current observations from all three markets, local-exchange timestamps, source freshness, selected-market recorded price curve, actual instruments, a selectable TRIAID/market event timeline with original evidence.
- Next phase: validated low-latency event push, deeper risk-factor/time-aligned visualization, export/backup/restore user controls, packaging and performance profiling.

This package does not silently change the Railway deployment or enable automated trading.
