# ADR-0001: Manifest-driven benchmark execution

- Status: Accepted
- Date: 2026-07-19

## Context

The benchmark must distinguish code state, dataset state, experiment configuration, runtime
attempts, and scientific decisions across local and cloud execution.

## Decision

Dataset versions, splits, experiments, and runs are represented by machine-validated immutable
manifests. A canonical RunSpec produces a content-derived run ID. Runtime attempts are append-only
children of a run and do not change the run identity.

## Consequences

- Runs are deduplicated and resumable.
- Scientific artifacts remain traceable to code, data, configuration, and seed.
- Schema evolution must be explicit and versioned.
- Ad hoc scripts that bypass manifests cannot produce release evidence.
