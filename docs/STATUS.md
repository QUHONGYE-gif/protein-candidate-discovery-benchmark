# Current project status

> Snapshot last verified: 2026-08-28. Update this file only when one of the nine project-level
> fields below changes materially. PID, individual attempts, routine Issue comments and code-review
> events do not trigger an update. Prefer lifecycle closeout before taking a new snapshot, but do
> not require this file to be transactionally synchronized with every GitHub event.

## Live state

| Field | Current fact |
|---|---|
| Verified upstream base | [`main@30f0c862`](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/commit/30f0c86224c7757496c9661d713f8df769552f72); PR #92 remains unmerged on top of this base |
| Current scientific stage | M4 has an accepted negative terminal; M5A has a terminal server-local ITERATION result candidate that is not yet governance-accepted; M5B remains blocked |
| Latest accepted scientific result | M4 prospectively returned `Gate 3 = STOP`: 0 retained, 6 fragile-inconclusive and 2 eliminated cells; #83 is accepted exploratory mechanism evidence, not causal confirmation |
| Current decision question | Can the M5A result candidate be accepted under its exact execution/integration identities and narrow within-environment repeatability claim, given the missing independently auditable pre-run authorization reference? |
| Active implementation PR | Draft [PR #92](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/92) carries M5A implementation and review remediation; it is unmerged and awaiting fresh non-author review |
| Active experiment | None running. M5A formal `attempt-001` is terminal and preserved; no M5A rerun, M5B execution or evidence promotion is authorized |
| Next promotion gate | Resolve the [Issue #91](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/91) `REQUEST_CHANGES`, obtain fresh exact-head review, and explicitly decide whether the preserved M5A artifact is governance-accepted; this does not authorize M5B |
| Known blockers | The formal M5A pre-run authorization is not independently auditable from a GitHub-time-stamped exact contract reference; the remediated PR head and claim boundary still require fresh human review |
| Superseded plans | For M4 onward, [`M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`](M4_TO_M7_REVISED_EXPERIMENT_PLAN.md) supersedes the corresponding forward-looking sections of [`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md). The old roadmap remains historical context |

Stage summary: M0-M3 are complete within their accepted engineering/evidence scopes; M4 terminal is
`STOP`; M5A has an unaccepted ITERATION result candidate under review; M5B and M6-M7 have not started.

## Immediate closeout queue

1. Amend Draft [PR #92](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/92)
   and [Issue #91](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/91)
   so execution identity, integration identity, CI, authorization status and claim wording agree.
2. Request fresh non-author review of the exact remediated PR head and the unchanged executed
   evidence identity; do not represent author remediation as approval.
3. Preserve the M5A artifact as an unaccepted result candidate unless the human governance gap is
   explicitly resolved. Keep M5B blocked and separately scoped/authorized.

The queue records the order visible at the snapshot date. Routine progress within it does not require
transactional STATUS synchronization. No Issue, PR, experiment, credential, server or COS state is
changed by this file.

## Evidence boundaries

- `Gate 3 = STOP` is not a runtime failure and is not `FROZEN/PASS`.
- The M5A artifact token `RECONSTRUCTION_VALID` records same-implementation, same-environment
  repeatability and 800/800 historical selection matching. It is not an independent implementation
  replication, statistical significance result, governance acceptance or causal mechanism claim.
- No experiment is currently active. No rerun, M5B, held-out evaluation, new COS publication,
  multi-fidelity run, SKEMPI external validation or final scientific validation is authorized by
  this file.
