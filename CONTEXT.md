# Domain Context

## Mission

Build a reproducible public-data benchmark that evaluates protein candidate discovery decisions
under small-sample and fixed-budget conditions.

## Ubiquitous language

- **Candidate**: one selectable item in an assay-specific candidate pool.
- **Candidate pool**: the finite offline pool available to an acquisition strategy.
- **Hidden oracle**: the component that owns candidate truth and reveals labels only after selection.
- **Observation**: a candidate whose high-fidelity label has been revealed.
- **Surrogate model**: a model fitted on observations that returns predictive mean and uncertainty.
- **Acquisition strategy**: a policy that selects candidate IDs without receiving hidden labels.
- **Trajectory**: the ordered, immutable record of one active-learning replay.
- **RunSpec**: the canonical configuration and provenance inputs that define one run.
- **Run ID**: the content-derived identifier for a canonical RunSpec.
- **Run attempt**: one append-only execution record for a Run ID, including actual runtime state,
  output checksums, and completion evidence.
- **Dataset manifest**: the source, license, version, download time, checksum, and limitation record
  for a dataset version.
- **Dataset ingest attempt**: one append-only execution record for transferring and independently
  verifying a dataset manifest and its raw objects; it does not redefine dataset identity.
- **Processing manifest**: the immutable transformation, duplicate-handling, field-visibility, and
  processed-output record derived from one dataset manifest.
- **Split manifest**: the immutable membership and leakage-control record for one evaluation split.
- **Experiment Config**: the immutable evaluation and method contract whose canonical file bytes
  are checksum-bound by a RunSpec.
- **High-value definition**: a prospective evaluator-owned rule over one frozen candidate-pool
  universe.
- **Evaluator**: the component that owns truth-derived metric computation without exposing
  unrevealed labels or high-value membership to model-side consumers.
- **Engineering readiness**: implementation and verification status only.
- **Scientific gate**: a prospectively defined evidence threshold; never inferred from CI status.
- **Held-out**: data that remains closed to method selection and development decisions.
- **Structure-compatible sequence**: a sequence compatible with a structure; not automatically a
  homolog.

## Core invariants

1. Hidden labels never enter surrogate or acquisition inputs before reveal.
2. Preprocessing and calibration fit on allowed development observations only.
3. Immutable scientific artifacts are content-addressed and checksum-bound.
4. Public and private protein project data remain operationally separated.
5. Engineering completion and scientific validation are reported independently.
6. Frozen protocols are superseded, never silently edited.
7. RunSpec binds the complete Experiment Config checksum and does not interpret its evaluation
   subfields.
8. High-value membership remains private to the evaluator or hidden oracle.

## Context boundary

This repository is a single bounded context. Dataset-specific adapters translate source formats
into the shared benchmark contracts. Cloud storage and execution infrastructure remain adapters,
not domain concepts.
