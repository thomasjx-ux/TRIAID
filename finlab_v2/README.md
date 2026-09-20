# TRIAID FIN Evolution Lab V2

TRIAID FIN V2 is a secondary-market research environment for continuous TRIAID Core improvement. It is deliberately smaller than a general trading platform.

## Research loop

Real market data → dynamic strategy population → TRIAID Core → next-period outcome → evaluation and attribution → diagnosis → Candidate Core → replay/holdout/shadow/audit gate → promotion or rejection.

The goal is not to force the current Core to beat the strategy population immediately. The goal is to make Core improvement observable, attributable, reproducible and continuously testable.

## Modules

- Market Data: modular provider hub with DAILY / INTRADAY / PREOPEN / REALTIME interfaces and point-in-time discipline
- Strategy Registry: 29 migrated strategies from the audited Cloud 007 policy bank
- Strategy Population: market-specific admission, exit, cooldown, weighting and cash handling
- Population State: persistent lifecycle state and automatic daily updates
- TRIAID Core: independently versioned intervention module
- Evaluation: baseline versus TRIAID outcome and strategy-level attribution
- Audit: structural and result integrity checks
- Review: daily detailed report and continuous curves
- Evolution: diagnosis, Candidate generation, validation gate and promotion ledger
- Run Store: persistent when /data is mounted, local fallback otherwise

## Markets

US uses SPY, QQQ, IWM, TLT and GLD.

CN uses 510300.SS, 510500.SS, 159915.SZ, 512100.SS and 511010.SS.

## Market data

Market data is separated from trading decisions. Data refresh never calls Strategy Population or TRIAID Core by itself.

Current Yahoo Chart provider:
- DAILY: US + CN, research-grade daily data
- INTRADAY: US + CN, 5-minute research data
- PREOPEN: US indicative extended-hours data; CN call-auction remains unsupported until a dedicated provider is connected
- REALTIME: US + CN 1-minute indicative chart data; explicitly not execution-grade and does not include bid/ask, order book or broker fills

Automatic refresh is session-aware:
- OPEN: INTRADAY every 5 minutes, indicative REALTIME every 2 minutes
- US PREOPEN: PREOPEN every 5 minutes, indicative REALTIME every 2 minutes
- POSTCLOSE: DAILY every 10 minutes
- CLOSED: DAILY every 60 minutes

The refresh scheduler does not create trading runs or change weights. Adjustment frequency remains a separate TRIAID research decision.

API:
- GET /api/market-data/status
- GET /api/market-data/capabilities
- GET /api/market-data/snapshot/{market_id}/{mode}
- POST /api/market-data/refresh/{market_id}/{mode}

## Live execution

A Run Now request creates a run identifier immediately. Market retrieval and calculation continue in the background. If the provider timestamp has not changed, the run is marked NO_NEW_DATA. On a new trading day, the previous day's pending decision is evaluated with the newly observed next-period return before a new decision is produced.

## Strategy population rules

US:
- 21/63/126/252-day evidence windows
- 3-day entry confirmation
- 3-day exit confirmation
- 5-day cooldown
- 28% maximum risky strategy weight

CN:
- 21/63/126/252-day evidence windows
- 5-day entry confirmation
- 3-day exit confirmation
- 10-day cooldown
- 28% maximum risky strategy weight

SHADOW strategies receive no experimental allocation. Cash is explicit. Risk, liquidity, capacity, cost and concentration are constraints, not diversity objectives.

## Core evolution

Candidate generation is permitted from verified diagnostic history. Promotion is not automatic. A Candidate must pass all four gates:

- Replay
- Holdout
- Shadow
- Audit

The active Core version and every promotion event are recorded.

## Storage

If Railway mounts a persistent Volume at /data, V2 automatically stores runs and evolution state under /data/triaid_fin_v2. Without /data it falls back to local runtime storage, which is suitable for testing but not durable across redeployments.


## Observation and transition research

Automatic data refresh is intentionally separated from TRIAID adjustment.

Every fresh market-data timestamp is recorded as a market observation. Consecutive observations of the same market and mode are converted into research-only transition records containing raw cross-asset price-change features such as:
- per-symbol return
- mean return
- mean absolute return
- maximum absolute move
- cross-sectional dispersion
- advancers / decliners
- elapsed source time

These transition records never generate an action and never change strategy weights. They exist so future research can align real state changes with later outcomes and estimate when TRIAID intervention is actually justified.

API:
- GET /api/market-data/observations
- GET /api/market-data/observation-status
- GET /api/market-data/transitions

## Provider and product boundary

The market-data layer now exposes provider routing and explicit product capability status.

Current state:
- Yahoo Chart: default research bars for US/CN daily and intraday data
- Alpaca adapter: implemented for US L1 latest bid/ask and bars, dormant until API credentials are configured
- US/CN arbitrary supported stock symbols: research bars available on demand, but not automatically admitted to Strategy Population
- A-share call auction: unavailable until an authorized provider is connected
- L2 order book: unavailable
- derivatives chain: unavailable
- broker fills: unavailable

Unavailable data products are reported as unavailable; the system does not synthesize or infer them from close prices.
