# TRIAID C7 Independent Replication Challenge

Status: OPEN FOR EXTERNAL REPLICATION

This challenge tests one frozen constructed-domain relation. It does not ask a replicator to endorse TRIAID, prove real-world efficacy, or show that a TRIAID planner is better than a strong generic planner.

## Frozen object

After domain-local recalibration in the frozen constructed supply-chain environment, the decision-interface structure containing propagation/context, certifiability/selective action, verified actuation, re-observation, and recovery certification should survive against simpler controls and a propagation ablation, while a strong generic MPC may tie or outperform on scalar utility.

The controlling contract is in FROZEN_CLAIM_001.json. Thresholds must not be changed after observing results.

## Two evidence levels

External reproduction: run the author artifacts unchanged on an independent machine and reproduce the frozen metrics/gates. Valuable, but not strict C7 closure.

Independent replication: write a new implementation from INDEPENDENT_IMPLEMENTATION_SPEC_001.md, using the frozen input banks, without importing/copying the author runner or internal clean-room runner. Report source hash, runtime, deviations and the ten gate decisions.

## Frozen execution bundle

TRIAID_THIRD_PARTY_EXECUTION_BUNDLE_001.zip
SHA256: f3139c645d1d1e660431af02a2cbc7c5c33862c2f748e5b80d4ef762d82faf02

The frozen case/posterior banks are distributed in the execution bundle rather than committed here because the posterior bank is large. Verify the bundle SHA256 before use. A replicator may obtain the exact bundle directly from the TRIAID team or another byte-identical mirror.

## Required return

1. Completed EXTERNAL_RESULT_TEMPLATE_001.json
2. Completed EXTERNAL_REPLICATION_REPORT_TEMPLATE_001.md
3. Source code or public repository for the independent implementation
4. Source SHA256 and runtime/dependency versions
5. Any discrepancy, including failures

Run VERIFY_EXTERNAL_RESULT_001.py against the returned result. A faithful negative result is valid evidence and must not be retuned away.

## Boundaries

Not claimed: real supply-chain efficacy, zero-calibration transfer, planner superiority, clinical efficacy, universal research acceleration.

Current strict C7 status remains OPEN until an actually independent external implementation executes the frozen relation.
