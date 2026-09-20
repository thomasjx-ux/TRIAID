# TRIAID FIN Evolution Lab V2

This branch contains a minimal modular scaffold for the secondary-market TRIAID evolution experiment.

The system is intentionally small:

1. Strategy Population
2. TRIAID Core
3. Evaluation
4. Audit
5. Daily review and continuous curves

The current Core is an identity scaffold and must not be interpreted as the research Core. It exists to prove that the Core can be replaced independently without modifying the surrounding modules.

A decision run is created first. Realized outcomes are submitted later through a separate endpoint. This preserves the T to T+1 boundary and avoids using future outcomes when making a decision.

The strategy registry is intentionally empty until the previously developed strategy-population rules have been independently reviewed and migrated.

No market credentials, private data, account data, or historical production artifacts are stored in this branch.
