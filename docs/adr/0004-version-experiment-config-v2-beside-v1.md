# ADR-0004: Version Experiment Config v2 beside v1 and bind canonical file bytes

- Status: Proposed
- Date: 2026-07-31

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
- Changing canonicalization, metric semantics, or evaluator result shape requires a new version or
  a superseding ADR.
- This proposed decision authorizes no production implementation until Issue #36 receives an
  exact-candidate `APPROVED FOR TDD IMPLEMENTATION` review.
