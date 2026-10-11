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
- Prediction Adjustment: H20-primary forecast adjustment, persistence, Opportunity Density, No Trade and invalidation contracts for US/CN/HK
- Evaluation: baseline versus TRIAID outcome and strategy-level attribution
- Audit: structural and result integrity checks
- Review: daily detailed report and continuous curves
- Evolution: diagnosis, Candidate generation, validation gate and promotion ledger
- Run Store: Supabase-backed persistent production storage, with a file backend for local/offline testing

## Markets

US uses SPY, QQQ, IWM, TLT and GLD.

CN uses 510300.SS, 510500.SS, 159915.SZ, 512100.SS and 511010.SS.

HK uses 2800.HK, 2828.HK, 3033.HK and 2819.HK in the current tracked product set.

## All-market opportunity gate

US, CN and HK all expose an explicit market-opportunity state before risk is committed. The common contract is:

Evidence → H20 state → persistence → Opportunity Density → No Trade / dynamic exposure → invalidation → outcome → learning.

H20 is the primary promotion and opportunity horizon. H5 can only break an exact H20 tie, and H1 remains diagnostic-only. An incomplete current daily bar is excluded during OPEN/BREAK.

The gate is market-specific rather than copied mechanically across markets:
- US uses the least restrictive normal/high exposure profile because same-day risk reduction is executable.
- CN uses a more conservative profile because T+1 and price-limit structure increase reversal and exit risk.
- HK sits between US and CN because trading is T+0 but liquidity is generally thinner than the US route.

No Trade is a valid decision state. A positive isolated signal does not force allocation when opportunity density is too low. Opportunity-profile parameters are fixed design priors and are not retuned from posterior outcomes.

Primary market routes are wired to the same discipline:
- US Return-Max applies H20 prediction ranking after immediate meta-switch cost and then applies the US Opportunity Density exposure gate.
- CN Recovery Wave applies the CN Opportunity Density gate to its route-native analog recovery evidence and recomputes capacity after exposure scaling.
- HK Return-Max consumes the prediction-adjusted TRIAID Core weights and exposes the HK Opportunity Density and invalidation state in its frozen route decision.

## Market data

Market data is separated from trading decisions. Data refresh never calls Strategy Population or TRIAID Core by itself.

Current Yahoo Chart provider:
- DAILY: US + CN + HK, research-grade daily data where the provider supplies the tracked instruments
- INTRADAY: provider-dependent research data, explicitly not execution-grade
- PREOPEN: US indicative extended-hours data; CN call-auction remains unsupported until a dedicated provider is connected
- REALTIME: indicative chart data where available; explicitly not execution-grade and does not include bid/ask, order book or broker fills

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

## Immediate preview versus official evidence

The dashboard Run Now controls create MANUAL_PREVIEW runs. Market retrieval and TRIAID calculation continue in the background, but the preview is intentionally non-evidence:

- it is kept in process memory only and is not written to the persistent run ledger;
- it does not advance Population State or SHADOW counters;
- it does not resolve prior outcomes;
- it does not register or advance prospective experiments;
- it does not write Recovery Wave or US Return-Max ledgers;
- it cannot accept a posterior outcome or enter Core/strategy evolution;
- the UI labels the result PREVIEW_READY and explicitly marks it as outside the evidence chain.

Official evidence is produced by the automatic research scheduler and explicit official engine path. At settled post-close, the official path may resolve the prior complete daily outcome, advance lifecycle state once, freeze the new daily research decision, and update the corresponding immutable evidence ledgers.

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

Production uses the Supabase storage backend and persists official runs, observations, lifecycle state and evolution ledgers across container replacement. The file backend remains available for deterministic CI and local/offline testing; it is durable only when its root is on a verified persistent mount.

Manual previews are deliberately excluded from both production and file persistence so operator clicks cannot contaminate the official research evidence history.

Deployment/bootstrap validation follows the same rule. `bootstrap_live.py` refuses non-file storage by default because bootstrap can create official evidence. Deployment preflight runs bootstrap against an isolated temporary file backend; writing bootstrap output to persistent production storage requires an explicit one-time migration override.

Application construction and read/status endpoints are also storage-neutral. Core/strategy state defaults and schema upgrades are prepared in memory and are not persisted merely because a candidate process imported the application or a user opened a status page. Stale incomplete official runs are recovered only by explicit startup maintenance, which emits an auditable maintenance receipt.

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

The market-data layer exposes provider routing and explicit product capability status.

Unavailable data products are reported as unavailable; the system does not synthesize or infer them from close prices. L2 order books, derivatives chains and broker fills remain unavailable unless an authorized provider is explicitly connected.
