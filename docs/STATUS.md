# Current project status

> Snapshot last verified: 2026-08-25. Update this file only when one of the nine project-level
> fields below changes materially. PID, individual attempts, routine Issue comments and code-review
> events do not trigger an update. Prefer lifecycle closeout before taking a new snapshot, but do
> not require this file to be transactionally synchronized with every GitHub event.

## Live state

| Field | Current fact |
|---|---|
| Verified upstream base | [`main@254a39a`](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/commit/254a39aeb4d083f1e02fbb46465f0f0e18ebabb2); this is the snapshot base, not the future squash-merge commit |
| Current scientific stage | M4 has an accepted negative terminal; M5 mechanism identification is in preparation/integration |
| Latest accepted scientific result | M4 prospectively returned `Gate 3 = STOP`: 0 retained, 6 fragile-inconclusive and 2 eliminated cells; #83 is accepted exploratory mechanism evidence, not causal confirmation |
| Current decision question | After integrating the completed #85 tracer, what is the smallest M5A/M5B experiment that distinguishes Greedy redundancy, uninformative deviation, opportunity cost and conditional benefit? |
| Active implementation PR | [PR #86](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/86) integrates the completed #85 execution implementation; it is not yet in `main` |
| Active experiment | None. The #85 28-RunSpec seed-source tracer is terminal with `MIXED-INCONCLUSIVE`; no further seed/task/model expansion is authorized by that result |
| Next promotion gate | Close completed lifecycles, align the phase policy and split experiment template, then review one bounded M5A/M5B contract; M5A deterministic reconstruction precedes any M5B truth-bearing branch evaluation |
| Known blockers | #85 implementation integration and the smoke/development versus frozen experiment-template split are unfinished; #7/#76 closeout is administrative, not a scientific blocker |
| Superseded plans | For M4 onward, [`M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`](M4_TO_M7_REVISED_EXPERIMENT_PLAN.md) supersedes the corresponding forward-looking sections of [`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md). The old roadmap remains historical context |

Stage summary: M0-M3 are complete within their accepted engineering/evidence scopes; M4 terminal is
`STOP`; M5 is in preparation/integration; M6-M7 have not started.

## Immediate closeout queue

1. Close [Issue #7](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/7)
   and [Issue #76](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/76)
   after their compact lifecycle reconciliation; those closures alone do not require a STATUS PR.
2. Integrate [Issue #85](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/85)
   through [PR #86](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/86)
   without changing execution provenance. Its seed-tracer runtime is legacy/frozen-specific and
   must not become the M5A/M5B runner base.
3. Make one separate small change for the phase policy and split experiment templates so smoke and
   development work no longer require frozen protocol inputs or expected COS artifacts.
4. Only then scope M5A/M5B; do not start an experiment from this closeout branch.

The queue records the order visible at the snapshot date. Routine progress within it does not require
transactional STATUS synchronization. No Issue, PR, experiment, credential, server or COS state is
changed by this file.

## Evidence boundaries

- `Gate 3 = STOP` is not a runtime failure and is not `FROZEN/PASS`.
- The #85 terminal is an executed result candidate until its implementation is integrated and its
  lifecycle is closed; it must not be represented as merged `main` evidence.
- No experiment is currently active. No held-out evaluation, new COS publication, multi-fidelity
  run, SKEMPI external validation or final scientific validation is authorized by this file.
