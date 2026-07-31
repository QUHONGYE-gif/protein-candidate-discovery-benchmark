# ADR-0004: Version Experiment Config v2 beside v1 and bind canonical file bytes

- Status: Accepted
- Date: 2026-07-31
- Accepted candidate: `7fb56b8dbbc5e25f1c62277c5df488135b9c40c2`
- Authorization: Issue #36 comment `5145054493`

## Context

Experiment Config v1 cannot persist the frozen Alpha Amylase and IRED high-value semantics without
losing candidate-universe identity, primary-versus-sensitivity purpose, tie and non-finite policy,
metric ownership, or task-first aggregation. The Issue #28 prototype demonstrated that a richer v2
can express those semantics, but it deliberately left config identity and production dispatch
unresolved.

## Decision

Introduce production Experiment Config v2 beside the unchanged v1 contract. A production config is
identified by the SHA-256 of its exact canonical UTF-8 JSON file bytes. The production loader must
parse with duplicate-key and non-finite rejection, render with the versioned reference serializer,
and require byte equality before Schema and domain validation. Semantically equal but
non-canonical files are rejected rather than silently re-identified.

RunSpec remains unchanged and binds only the complete `experiment_config_sha256`; it does not
interpret high-value definitions, metrics, or evaluator fields. High-value membership and
truth-derived evaluation stay private to the evaluator or hidden oracle. Experiment Config v1
remains valid through explicit version dispatch, while `2-prototype`, `proposal_status`, unknown
versions, and silent v1-to-v2 migration fail closed.

Metric evidence uses a versioned per-run envelope, while cross-seed task aggregates use a separate
versioned envelope bound to an ordered complete set of metric-result checksums. Definition-
independent metrics use an explicit null definition identity and are never duplicated per
high-value definition. Raw unitful values remain task-local; only frozen dimensionless metrics may
be compared through task-first aggregation.

Result envelopes preserve RunSpec `model_id`, `model_config_sha256`, `acquisition_id`, and
`acquisition_config_sha256` directly instead of introducing a free method identity. Result
artifacts reuse the canonical JSON byte procedure and are checksum-bound by their exact validated
bytes. A task-seed aggregate accepts exactly one outcome-independent, succeeded, completion-marked
result per preregistered seed; zero or multiple eligible results fail closed.

This ADR records a compatible extension of ADR-0003 and does not supersede it.

## Considered Options

- Hash arbitrary tracked file bytes: rejected because formatting-only differences create distinct
  scientific identities.
- Hash a parsed semantic value: rejected because parser and number-normalization choices can detach
  RunSpec identity from the exact reviewed file.
- Hash exact canonical file bytes: selected because one reviewed byte stream is both reproducible
  and directly auditable.

## Consequences

- Formatting a production config is identity-bearing; accepted files must use the reference
  serializer and one terminal newline.
- Schema v1 files and behavior remain unchanged; v2 has a separate Schema, loader branch, and
  domain validator.
- Task-seed aggregate evidence cannot be attached to an arbitrary run, attempt, or seed.
- Retry history cannot be filtered after observing outcomes; ambiguous successful attempts block
  aggregation until a separately reviewed selection contract exists.
- Raw `best_found_value` and `simple_regret` cannot become a cross-task scalar without a separately
  versioned normalization contract.
- Changing canonicalization, metric semantics, or evaluator result shape requires a new version or
  a superseding ADR.
- Production implementation remains limited to the exact Issue #36 Phase B boundary authorized by
  comment `5145054493`; real truth, held-out, COS/server, replay, M2, and scientific claims remain
  separately gated.
