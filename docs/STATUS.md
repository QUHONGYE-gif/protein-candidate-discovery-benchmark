# Current project status

> Snapshot last verified: 2026-09-08. Update this file only when one of the nine project-level
> fields below changes materially. PID, individual attempts, routine Issue comments and code-review
> events do not trigger an update. Prefer lifecycle closeout before taking a new snapshot, but do
> not require this file to be transactionally synchronized with every GitHub event.

## Live state

| Field | Current fact |
|---|---|
| Verified upstream base | [`main@9f332c2`](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/commit/9f332c2935b53ab1697688d0024944bc12a99161); Governance v2 migration rehearsal PR #102 is merged; PRs #103 and #105 remain open Drafts |
| Current scientific stage | M4 retains its accepted negative terminal; M5A is accepted; M5B retains its accepted L1 exploratory terminal and disclosed source deviation. M5C has a GFP-only L1 result in #103; its four-assay formal cohort is incomplete. Owner-authorized formal M5D external execution is complete, pending independent review |
| Latest accepted scientific result | M5B `attempt-002` is the first complete terminal over the frozen 80 Alpha/IRED development same-states: all four primary contrasts are inside their frozen 98.75% equivalence intervals, while both task taxonomies and the overall taxonomy are `INCONCLUSIVE` |
| Current decision question | Evaluate the existing M5D external result: neither fixed model has both primary E-G and E-Matched lower bounds above zero. Four conservative groups limit interpretation; no outcome-driven rerun or method selection |
| Active implementation PR | [M5C #103](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/103) and [M5D #105](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/105); M5D readiness, execution code and compact results are proposed for review |
| Active experiment | None running. M5D `formal-20260908-001` completed 360 states / 23,760 branches on sole server `129.28.97.247`, with successful fresh-process disk reconstruction |
| Next promotion gate | Independent code/scientific review of M5D #104/#105, then a separate acceptance decision. Execution completion and `_SUCCESS` do not confer scientific Gate acceptance or COS archival permission |
| Known blockers | No M5D execution blocker remains. M5D independent review/evaluation is pending. M5C four-assay formal restrictions remain recorded in #103 and are not changed by this M5D authorization |
| Superseded plans | [`M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`](M4_TO_M7_REVISED_EXPERIMENT_PLAN.md) remains the scientific roadmap. The owner explicitly superseded waiting for M5C/prior review for this M5D attempt in [#104](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/104#issuecomment-5583110386); [the prospective M5D contract](experiments/M5D_FORMAL_EXECUTION.md) records the bounded change |

Stage summary: M0–M3 and accepted M4/M5A/M5B results retain their historical scopes.
The current unreviewed M5D result is recorded in
[the formal execution report](results/m5d-formal-20260908/README.md).
The GFP-only result in #103 does not complete the M5C four-assay cohort. M6–M7 have not started.

## Next bounded queue

1. Review and evaluate the existing M5D result in #104/#105; keep positive, negative
   and zero directions and the four-group limitation visible. No rerun is implied.
2. Continue M5C under its existing #103 authorities and scope; do not infer completion
   or new execution permission from M5D's separate owner instruction.
3. Consider later milestone acceptance/promotion only through a separate decision.

The queue records the order visible at the snapshot date. Routine progress within it does not require
transactional STATUS synchronization. No Issue, PR, experiment, credential, server or COS state is
changed by this file.

## Evidence boundaries

- `Gate 3 = STOP` is not a runtime failure and is not `FROZEN/PASS`.
- The accepted M5A artifact token `RECONSTRUCTION_VALID` records same-implementation,
  same-environment repeatability and 800/800 historical selection matching. It is not an
  independent implementation replication, statistical significance result or causal mechanism
  claim.
- M5B `attempt-002` is accepted only as an L1 exploratory ITERATION terminal with the actual-source
  deviation permanently disclosed: authorization named `a797efa` / `e8a01ce`, while the attempt
  used the pre-attempt feature-root correction `2aefbe0` / `00aaf06`. This is not retrospective
  exact-source authorization and does not permit a rerun.
- On the frozen 80 development same-states, the four primary immediate recall contrasts are inside
  their frozen 98.75% equivalence intervals. The registered task and overall classifications remain
  `INCONCLUSIVE`; this is not global equivalence, behavioral redundancy, multi-round benefit,
  causal mechanism, cross-assay generalization or benchmark evidence.
- M5D was prospectively authorized by the owner for execution before review, exact-bound
  to source `ff9528ca5721c5ebb5397789681065d7af112625`. It is a completed formal external
  attempt pending independent review, not a newly accepted scientific result.
- No experiment is currently active. This status record grants no rerun, new COS publication,
  multi-fidelity run or evidence-promotion authority.
