# Contributing to TRIAID

TRIAID currently accepts contributions primarily through the Open Validation Program.

Please read `OPEN_VALIDATION.md` before opening an issue or pull request.

## What we want

We welcome:

- independent reproductions, including failures;
- counterexamples and proof objections;
- stronger matched-information baselines;
- portability reports across numerical environments;
- new public/sandboxed validation environments;
- negative results that narrow the claim boundary;
- corrections to factual, mathematical, or reproducibility errors.

## What we do not want

Please do not submit:

- cosmetic rewrites of the frozen v1.0.0 scientific package;
- post-outcome threshold tuning presented as confirmatory evidence;
- private or identifying partner data;
- claims of clinical efficacy, financial alpha, production safety, or universal transfer that exceed the submitted evidence;
- changes that weaken a baseline merely to improve TRIAID's relative result.

## Frozen public release

TRIAID Paper 1 v1.0.0 is frozen. Its canonical release SHA-256 is:

`fc84ab5acb5e0ce776560ae293286b8a1a63292087c70b65ed8622c9f8043983`

The existing release checksum manifest protects the scientific snapshot. New validation files should be additive. If you believe a frozen file must change, open a Challenge issue first and explain which of the three hard-change conditions applies:

1. mathematical error;
2. reproducibility failure;
3. factual error.

## Result contributions

Structured external results belong under:

`validation/results/`

Use `validation/RESULT_TEMPLATE.json` and run:

```bash
python validation/validate_result.py validation/results/<your-result>.json
```

A result PR should also include enough material for another person to independently inspect or rerun the test.

## Pull requests

Keep one scientific question per pull request when practical. State clearly:

- track: REPRODUCE / CHALLENGE / VALIDATE;
- exact frozen target;
- what changed and why;
- whether outcomes were seen before the change;
- evidence level;
- PASS / FAIL / PARTIAL / INCONCLUSIVE / OUT_OF_SCOPE;
- whether a matched baseline won;
- any claim you believe should be narrowed or corrected.

Negative outcomes are first-class contributions.
