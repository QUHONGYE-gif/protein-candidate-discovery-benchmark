# Current project status

> Snapshot last verified: 2026-08-30. Update this file only when one of the nine project-level
> fields below changes materially. PID, individual attempts, routine Issue comments and code-review
> events do not trigger an update. Prefer lifecycle closeout before taking a new snapshot, but do
> not require this file to be transactionally synchronized with every GitHub event.

## Live state

| Field | Current fact |
|---|---|
| Verified upstream base | [`main@bce9c707`](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/commit/bce9c707f8c069f6b58973626ba785b5af3d8258); Draft PR #93 carries the synthetic-only M5B implementation on top of this base |
| Current scientific stage | M4 has an accepted negative terminal; M5A is governance-accepted and integrated; M5B v2 SCOPING is approved, while implementation is limited to the synthetic engineering gate and scientific evidence has not started |
| Latest accepted scientific result | M4 prospectively returned `Gate 3 = STOP`: 0 retained, 6 fragile-inconclusive and 2 eliminated cells; #83 is accepted exploratory mechanism evidence, not causal confirmation |
| Current decision question | Does the bounded M5B v2 implementation uniquely enforce canonical state/inventory identity, branch isolation, Matched Random, bootstrap/taxonomy and retry rules before any Alpha/IRED truth-bearing execution is considered? |
| Active implementation PR | Draft [PR #93](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/93) carries only the authorized M5B synthetic implementation/smoke; it does not materialize the real child inventory or execute Alpha/IRED |
| Active experiment | None. The M5B focused synthetic suite is engineering verification, not a scientific attempt; no Alpha/IRED truth, server/COS, held-out, retry or evidence promotion is authorized |
| Next promotion gate | Complete independent review of PR #93 and its synthetic smoke, then materialize and fresh-review the outcome-free exact 80-state child inventory and an exact execution contract before a separate human execution authorization |
| Known blockers | PR #93 is still Draft; exact `m5b_state_inventory_sha256`, execution source/tree/environment/authority/budget and a truth-bearing authorization remain open gates |
| Superseded plans | For M4 onward, [`M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`](M4_TO_M7_REVISED_EXPERIMENT_PLAN.md) supersedes the corresponding forward-looking sections of [`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md). The old roadmap remains historical context |

Stage summary: M0-M3 are complete within their accepted engineering/evidence scopes; M4 terminal is
`STOP`; M5A is accepted and integrated; M5B synthetic engineering is active but its evidence stage
has not started; M6-M7 have not started.

## Immediate closeout queue

1. Complete the bounded synthetic implementation and required checks in Draft
   [PR #93](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/93), then obtain
   independent exact-head review; synthetic PASS remains engineering evidence only.
2. In a later outcome-free preflight, materialize the exact 80-state child inventory SHA and bind
   the execution source/tree/environment/authority/budget for fresh review.
3. Require a separate human authorization before any Alpha/IRED branch truth is read or a
   truth-bearing M5B attempt starts; keep [Issue #91](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/91)
   open through M5B terminal closeout.

The queue records the order visible at the snapshot date. Routine progress within it does not require
transactional STATUS synchronization. No Issue, PR, experiment, credential, server or COS state is
changed by this file.

## Evidence boundaries

- `Gate 3 = STOP` is not a runtime failure and is not `FROZEN/PASS`.
- The accepted M5A artifact token `RECONSTRUCTION_VALID` records same-implementation,
  same-environment repeatability and 800/800 historical selection matching. It is not an
  independent implementation replication, statistical significance result or causal mechanism
  claim.
- M5B v2 is accepted only as the bounded SCOPING input for synthetic implementation. CI or focused
  smoke success cannot become a scientific result, open the Alpha/IRED truth boundary or establish
  an immediate-effect mechanism.
- No experiment is currently active. No held-out evaluation, new COS publication, multi-fidelity
  run, SKEMPI external validation or final scientific validation is authorized by this file.
