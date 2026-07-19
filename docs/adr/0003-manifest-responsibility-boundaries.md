# ADR-0003: Separate specification, provenance, split, and attempt manifests

- Status: Accepted
- Date: 2026-07-19

## Context

ADR-0001 requires machine-validated immutable manifests and defines runtime attempts as append-only
children of a content-derived Run ID. A single dataset manifest cannot clearly own upstream source
provenance, processing decisions, split membership, evaluation thresholds, and runtime evidence.
Likewise, naming the canonical run input a run manifest obscures the distinction between scientific
run identity and execution attempts.

## Decision

Use separate contracts with narrow ownership:

- Dataset Manifest owns upstream identity, license, download time, raw checksums, and source limits.
- Processing Manifest owns transformation provenance, duplicate handling, field visibility, and
  processed-output checksums.
- Split Manifest owns immutable membership, grouping, held-out partitions, and leakage controls.
- Experiment Config owns thresholds, metrics, budgets, methods, and held-out authorization.
- RunSpec owns only canonical inputs that contribute to the content-derived Run ID.
- RunAttempt owns actual execution state, compute metadata, failures, output checksums, and the
  `_SUCCESS` completion marker.

Cross-field scientific invariants that standard JSON Schema cannot express reliably remain
fail-closed domain validation responsibilities.

## Consequences

- Updating runtime status or retrying execution does not change the Run ID.
- Upstream, processing, splitting, evaluation, and execution provenance evolve independently.
- Consumers must validate the complete checksum-linked manifest chain before accepting evidence.
- Schema versions and accepted manifest artifacts remain immutable; incompatible evolution requires
  a new schema version and, when it changes this decision, a superseding ADR.
