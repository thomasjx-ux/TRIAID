# TRIAID FIN Risk Overlay Addendum

Status: PROSPECTIVE_RESEARCH_ADDENDUM
Version: gpt-risk-overlay@0.1.0
Base validation protocol: gpt-forward-validation@1.1.0

## Purpose

This addendum governs the State Break fast-brake and underlying-exposure concentration overlay used by the US prospective validation route. It does not replace the base validation protocol or the TRIAID constitution.

## Objective hierarchy

Long-horizon realizable after-cost value maximization remains the sole optimization objective. Risk, liquidity, capacity, concentration and execution remain constraints. The overlay must not introduce a second utility objective.

## State Break discipline

Fast 1/3/5-day evidence may determine that the previously dominant state no longer explains current observations. It may reduce risk exposure prospectively, but it must not reverse portfolio direction, promote a challenger, or use same-period realized outcomes to rewrite a frozen decision.

State Break uses only information observable at freeze time. The detector records its score, component evidence, confirmation count, brake level and resulting risk cap. A lower risk cap creates cash residual rather than an unregistered opposite-direction position.

## Underlying exposure discipline

Strategy labels do not constitute diversification. Before execution, the frozen strategy mix is projected into underlying tradable assets. Hard single-asset and risk-cluster concentration caps may reduce or redistribute exposure. The overlay records pre- and post-constraint concentration, HHI, effective asset count and cash residual.

## Evidence and evaluation

Every frozen decision must preserve the unmodified generic Core allocation, the return-max route before the overlay where available, the final overlaid allocation, State Break diagnostics and concentration diagnostics. T1 evaluation must report both avoided loss and missed upside so cash or braking cannot be treated as success by default.

## Versioning

Changes to State Break thresholds, component weights, concentration caps, asset clustering or execution semantics create a new overlay version segment. Older frozen records remain evaluated under the versions pinned at their own T0.
