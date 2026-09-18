# TRIAID C7 Independent Implementation Specification 001

Status: frozen replication specification derived from Round 7 Protocol 002 and its canonical runner.

## Object being replicated

This package does not ask a replicator to prove that TRIAID is a better planner. The frozen object is the decision-interface relation tested in Round 7: propagation/context, certification-aware selective action, verified actuation, re-observation, and recovery certification should alter safe terminal outcomes relative to simpler controls and a propagation ablation, while a strong generic MPC may tie or outperform on scalar utility.

## Inputs

Use frozen_inputs/C7_FROZEN_CASE_BANK_001.jsonl, one record per episode. Policy code may receive only the nested obs object at its first decision. Hidden fields and frozen potential outcomes belong to the environment.

For generic MPC, use frozen_inputs/C7_MPC_POSTERIOR_BANK_001.jsonl. These 24 posterior particles per episode are frozen only to remove dependence on RNG implementation. They are part of the comparator's legal belief calculation, not environment truth.

## Environment equations

First action:
- HOLD: benefit 0, cost 0.02, collateral 0.
- LOCAL: cost 0.15, collateral 0.06, benefit (0.38 + 0.12*critical) * (0.65 if common else 1).
- GLOBAL: cost 0.32, collateral 0.22, benefit 0.66 + 0.12*common.
- PROBE: cost 0.08, benefit 0, collateral 0.

For actuation-mismatch episodes, LOCAL fails when u_act_local <= 0.55 and GLOBAL fails when u_act_global <= 0.25. A failed action retains 10% of nominal benefit. Policies with verified actuation observe true success; static and graph-only behave as if actuation succeeded.

risk1 = true_risk - benefit + 0.15*max(0,demand-0.7) + 0.10*common

backlog1 = clip(backlog + 0.28*max(0,risk1-0.85) - 0.16*benefit, 0, 1.4)

Then risk1 += 0.22*backlog1.

Re-observation uses eps_probe for PROBE and eps_reobs otherwise. Noise is 0.035 after PROBE and 0.07 otherwise. Residual risk is clip(risk1 + noise*eps,0,2.5), observed backlog is clip(backlog1 + 0.7*noise*eps,0,1.5). Common re-observation uses u=(0.73*u_act_global+0.27*u_act_local) mod 1; truthful after PROBE or when u>0.12, otherwise flipped. Freshness is 0.95 after PROBE, else 0.35 for stale episodes and 0.85 otherwise.

Second stage:
- RECOVER: +0.04 cost. If risk>0.75 or backlog>0.45 or first actuation failed, premature=true and risk +=0.45.
- LOCAL: +0.15 cost, +0.05 collateral, subtract (0.30+0.10*critical)*(0.65 if common else 1) from risk and 12% of that benefit from backlog.
- GLOBAL: +0.32 cost, +0.20 collateral, subtract 0.58+0.10*common from risk and 15% of that benefit from backlog.
- HOLD: +0.02 cost, add 0.08*max(0,demand-0.65)+0.06*common risk and 0.12*max(0,risk-0.85) backlog.

Unsafe = risk>1.05 or backlog>0.82 or premature.
Safe recovery = not unsafe and risk<0.88 and backlog<0.68.
Diagnostic utility = 1[safe] - 0.55*cost - 0.45*collateral - 0.7*unsafe.

## Policies

Static: score=0.75*sev+0.45*dem+0.25*backlog. GLOBAL if >0.95, LOCAL if >0.58, else HOLD. No actuation verification. RECOVER after LOCAL/GLOBAL, else HOLD.

Graph only: score=0.60*sev+0.45*dem+0.25*backlog+0.20*critical+0.25*common. GLOBAL if observed common and score>0.70; else LOCAL if observed critical and score>0.55; else GLOBAL if score>0.92; else LOCAL if score>0.58; else HOLD. No actuation verification. RECOVER if reobserved risk<0.9; otherwise GLOBAL if common, else LOCAL.

Full interface: risk_est=0.72*sev+0.48*dem+0.30*backlog+0.20*critical+0.28*common. Ambiguous when freshness<0.55 or 0.58<risk_est<0.78 and observed common=false. Ambiguous cases PROBE; then GLOBAL if reobserved common and risk>0.78, LOCAL if risk>0.64, else HOLD. Otherwise GLOBAL if observed common and risk_est>0.74; LOCAL if risk_est>0.63; else HOLD. Actuation is verified. On failed actuation choose GLOBAL if systemic/risk>0.9 else LOCAL; else RECOVER when risk<0.72 and backlog<0.42 after LOCAL/GLOBAL; else GLOBAL when risk>0.92 or common and risk>0.78; else LOCAL when risk>0.68; else HOLD.

No-propagation: risk_est=0.72*sev+0.48*dem+0.30*backlog. Ambiguous when freshness<0.55 or 0.58<risk_est<0.78; PROBE then LOCAL if risk>0.64 else HOLD. Otherwise LOCAL if risk_est>0.63 else HOLD. Verified actuation; on failure LOCAL; otherwise RECOVER if risk<0.72 and backlog<0.42 after LOCAL, LOCAL if risk>0.68, else HOLD.

Generic MPC: evaluate all four first actions on the 24 frozen posterior particles with actuation failure disabled in belief particles. Use the frozen second-stage thresholds in the distributed bundle. Choose maximum mean diagnostic utility, tie-breaking HOLD, LOCAL, GLOBAL, PROBE. Execute on the real frozen case with verified actuation and actual re-observation.

## Controlling gates

Use the ten gates in FROZEN_CLAIM_001.json. Do not retune thresholds.

## Independent implementation rule

Do not import, copy functions from, or call the author runner or internal clean-room runner. Report source hash, runtime/dependencies, and a result JSON following EXTERNAL_RESULT_TEMPLATE_001.json. A faithful failure narrows or falsifies C7; it is not to be rhetorically repaired.
