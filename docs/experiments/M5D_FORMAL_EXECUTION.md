# M5D external execution v1 — Issue #104 / PR #105

## Prospective owner decision (2026-09-08)

The owner explicitly directed formal M5D execution now on `129.28.97.247`, with
results appended to #104 and #105 and independent review/evaluation afterward.
This supersedes the readiness document's wait for M5C completion and prior review
for this execution only. It does not retroactively pass any Gate or authorize COS
archival. This is an owner-authorized formal external attempt, pending review.

## Frozen inputs and question

Use the nine complexes with at least 80 processed candidates, selected without
strategy outcomes from the completed readiness artifact. All candidates of these
complexes form independent within-complex replay pools; no model transfers labels
across complexes. The four conservative publisher/protein leakage components stay
entirely in external evaluation and are the statistical resampling units. There is
no newly trained cross-complex model or claim of sequence-homology independence.
All other complexes are excluded solely by the prospective 80-candidate requirement.

Input processing SHA-256:
`4a9f491fa71b3630466de094e19f5e7b51765ff75e7ed10a345eee6cbcb2efdd`;
split SHA-256:
`b11beb72e951878d42f247478ad8e5bd0bca25a8eeb3fdb71350bd44171f7e72`.
Use exact manifest-bound candidates, 41 mutation-count features, membership and
hidden truth, with target `-delta_delta_g` in kcal/mol. No filtering, normalization,
feature selection, proxy, threshold tuning or model tuning on external outcomes.

Question: do the previously fixed RF + Uncertainty and XGBoost + UCB(beta=1)
strategies retain immediate discovery gains against model-matched Greedy and
64 exact-overlap-matched Random controls on these external protein complexes?

## Fixed comparison and analysis

- Reuse `model_effective_parameters` and `m4_model_config_bytes` unchanged.
  Fit a point model and five acquisition bootstrap members using the existing
  `bootstrap_draws` PCG64 rule, with this new protocol/task identity.
- Seeds 1729–1738; nested label-independent initial samples 32 and 64; batch 16.
  Initial ranking is SHA-256 of canonical `[protocol, complex, seed, candidate]`.
  Ties in acquisition scores break by candidate ID. 360 states, 23,760 branches.
- For each state, E and G see the identical revealed prefix. Every M preserves
  E intersect G and samples the remaining slots without replacement from pool
  minus G, using SHA-256 `[protocol, state, replicate, candidate]` ranking.
- Primary: immediate tie-inclusive top-1% recall increment (nominal ceil(0.01*N),
  all ties included), E-G and E-mean(M). Secondary: top-0.5%/2% recall, best-found
  improvement and equivalent simple-regret reduction. No outcome-selected subset.
- Average the ten seeds and two initial sizes within each complex and model,
  then equally average complexes within group, then equally average four groups.
  Report every complex, group and initial-size cell, including negative/zero values.
- 20,000 deterministic PCG64(seed=104105) cluster-bootstrap draws of four whole
  groups, shared across all contrasts. Type-7 99.375% percentile intervals. These
  are descriptive with only four groups; seeds and matched replicates are never
  counted as independent external units. No new multiplicity/decision tuning.
  A model receives a positive descriptive flag only if BOTH primary lower bounds
  exceed zero; this flag is not scientific Gate acceptance or general mechanism proof.

## Execution and validation

Exact clean Git commit, contract/config hashes, package versions, input hashes,
complete observable cohort and state inventory are persisted before reading labels.
Spawned selection workers receive only observable IDs/features and explicitly
revealed `Observation` values; no truth object/path is passed. Only the replay
parent reveals labels through `HiddenOracle` and evaluates selections.
One worker, one native thread per library, process-tree RSS <=1.5 GiB, wall <=2 h,
attempt bytes <=1 GiB. No new environment, dependency, service or COS operation.

Each fresh attempt is append-only under `/srv/pcdb/runs/M5D/`. No overwrite or
outcome-driven retry. Preserve failed attempts. Persist private selections and
branch aggregates, then reconstruct every branch metric and aggregate from disk,
validate cohort/state exact cover, initial nesting, branch uniqueness/disjointness,
matched-control identities, manifest and output checksums. Write `_SUCCESS` last
only after this engineering readback passes. This marker does not mean review or
scientific acceptance. Publish compact aggregate results and provenance only.
