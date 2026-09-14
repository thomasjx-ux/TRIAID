# TRIAID Open Validation Program

Version: 1.0  
Status: public validation governance layer  
Canonical scientific target: TRIAID Paper 1 v1.0.0  
Frozen release SHA-256: `fc84ab5acb5e0ce776560ae293286b8a1a63292087c70b65ed8622c9f8043983`

TRIAID invites independent reproduction, adversarial challenge, and external validation.

The purpose of this program is not to recruit contributors to make TRIAID look correct. The purpose is to make it easy for outside researchers to reproduce the claims, find counterexamples, submit stronger baselines, and test whether the frozen ideas survive in environments the authors did not design.

A negative result is a valid and useful result.

## Three tracks

### 1. REPRODUCE

Use this track when you are testing a claim already made by a frozen TRIAID artifact.

Examples:

- run the v1.0.0 reproduction pipeline on a different OS, NumPy/BLAS build, CPU, or toolchain;
- independently recompute a figure or numerical diagnostic;
- rederive a theorem step or check an external theorem dependency;
- verify that the frozen source compiles and the published checks hold.

A reproduction should change as little as possible. If a change is necessary, disclose it exactly. A reproduction that fails is not to be silently repaired into a pass.

### 2. CHALLENGE

Use this track when you are actively trying to break, narrow, or outperform a TRIAID claim.

Examples:

- construct a counterexample to an assumption or proof step;
- find a stronger matched-information baseline;
- identify an estimator failure inside the stated regime;
- show that a diagnostic interpretation is not supported by the data;
- test a boundary case such as rank, sampling, calibration, noise, or finite-sample behavior;
- find a simpler explanation for a reported pattern.

Challenge submissions may modify code or design new experiments, but they must not rewrite the frozen target and then claim to have reproduced it. Keep the canonical artifact and the challenger artifact separate.

### 3. VALIDATE

Use this track when you apply a frozen TRIAID question or protocol to a new dataset, system, benchmark, simulator, or real environment.

Validation requires a declared contract before outcomes are inspected whenever the result is intended to be confirmatory. Record:

- observations/history available at decision time;
- intervention or action family;
- response target and horizon;
- data split and leakage controls;
- representation procedure;
- strongest credible matched baselines;
- failure criteria;
- costs/constraints where actions are involved;
- evidence level and provenance.

A new domain is not proof of universality. Domain-specific state variables, thresholds, safety semantics, and baselines must be calibrated locally.

## Evidence levels

Use the lowest level that fully describes the evidence.

| Level | Label | Meaning |
|---:|---|---|
| 0 | Conceptual analogy | Plausible mapping without a frozen empirical test. |
| 1 | Conditional mathematical result | Correct under stated assumptions; does not establish real-system membership. |
| 2 | Constructed / same-generator synthetic | Useful for mechanism, debugging, counterexamples, and protocol development. |
| 3 | Independent synthetic / destructive engineering QA | Stronger implementation independence, still not real-system evidence. |
| 4 | Reconstructed external trajectory | Public external events under a frozen reconstruction. |
| 5 | Recovered real artifact | Real logs/commits/reports, but not necessarily complete prospective telemetry. |
| 6 | Prospective raw or lightly redacted real trajectory | Frozen protocol applied prospectively with verified provenance and no outcome leakage. |
| 7 | Prospective intervention with verified actuation and recovery | Closed-loop real intervention evidence. |
| 8 | Independent replication | Separate team/environment reproduces the relevant result under a compatible frozen contract. |

Do not describe a lower evidence level using the claim language of a higher one.

## Frozen-target rule

Every submission must identify the exact target it is testing. For Paper 1 v1.0.0 the canonical release hash is:

`fc84ab5acb5e0ce776560ae293286b8a1a63292087c70b65ed8622c9f8043983`

The frozen paper/source/data package is not edited to make an external result pass. If a real mathematical error, reproducibility failure, or factual error is found, report it as such. The maintainers may then create an explicitly versioned correction.

## Required result states

Use one of:

- `PASS` — the declared test passed as frozen;
- `FAIL` — the declared test failed as frozen;
- `PARTIAL` — some prespecified criteria passed and others failed;
- `INCONCLUSIVE` — the test could not adjudicate the claim;
- `OUT_OF_SCOPE` — the tested setting does not satisfy the target claim's declared assumptions.

Do not convert `FAIL` into `PASS` by changing thresholds, exclusions, state definitions, data windows, or baselines after seeing the locked outcome. A scientifically justified redesign becomes a new exploratory or next-version protocol.

## Matched-baseline rule

Claims of incremental value must compare against the strongest credible simpler alternatives on the same information and task. Match, where relevant:

- observation information;
- train/calibration/test split;
- action/probe budget;
- response horizon;
- retained-state budget;
- online and offline compute accounting.

If a matched baseline wins, report `NO INCREMENTAL-VALUE` for that comparison. Do not weaken the baseline.

## Submission package

A strong submission contains:

1. a completed `validation/RESULT_TEMPLATE.json` or equivalent structured record;
2. exact commands or a runnable script/notebook;
3. environment/dependency information;
4. hashes or immutable identifiers for target artifacts and result artifacts;
5. data provenance and permission statement;
6. frozen protocol/preregistration for confirmatory validation;
7. raw or minimally processed result files when shareable;
8. plots/tables derived from those results;
9. explicit negative results and deviations from the frozen protocol.

Run:

```bash
python validation/validate_result.py path/to/result.json
```

before submitting a result file.

## Safety, privacy, and permissions

Do not submit secrets, credentials, private partner data, protected health information, personal identifiers, proprietary logs you lack permission to publish, or data obtained without authorization.

For medical work, public validation does not create clinical authorization and must not be represented as treatment guidance. For finance, backtests or observational results do not establish investable alpha or causal systemic-risk control. For agent/system work, only test systems and environments you are authorized to evaluate or control.

## How results affect TRIAID

External results are evidence, not automatic edits to the canonical theory.

A submission may:

- reproduce an existing claim;
- narrow its valid regime;
- identify a reproducibility defect;
- falsify a component;
- demonstrate no incremental value against a stronger baseline;
- establish evidence in a new declared domain;
- remain inconclusive.

Maintainers will preserve negative results and record the exact protocol/evidence class. Scientific promotion requires the evidence level appropriate to the claim.

## Start here

Choose the matching GitHub issue form:

- Reproduce a frozen result
- Challenge TRIAID
- Validate in a new environment/domain

For a code/result contribution, also follow `CONTRIBUTING.md` and the pull-request template.
