# Validation result records

This directory holds machine-readable records submitted through the TRIAID Open Validation Program.

Each result JSON should be self-contained enough to answer five questions:

1. What exact frozen claim/artifact was tested?
2. What environment/data/protocol was used?
3. What was changed relative to the canonical target?
4. What happened, including negative outcomes?
5. What evidence level and claim impact are justified?

Use `RESULT_TEMPLATE.json` as the starting point and `validate_result.py` as the minimum structural check.

The validator checks structure and allowed enumerations. It does not certify truth, independence, fairness, statistics, safety, or scientific validity. Those remain review questions.

## File naming

Recommended:

`YYYY-MM-DD_<track>_<short-name>.json`

Examples:

- `2026-10-01_reproduce_numpy-2-5.json`
- `2026-10-12_challenge_stronger-baseline.json`
- `2026-11-03_validate_new-simulator.json`
