# ADR-0002: COS is not a POSIX filesystem

- Status: Accepted
- Date: 2026-07-19

## Context

The project will use COS for datasets and benchmark artifacts while executing on replaceable
compute servers. Mounting object storage as a shared filesystem encourages partial writes,
small-object proliferation, and hidden assumptions about rename and append semantics.

## Decision

Domain and orchestration code access artifacts through a narrow `ArtifactStore` contract. Workers
stage immutable inputs to local cache, write outputs locally, validate them, upload final objects,
and publish `_SUCCESS` last. Databases, Git repositories, queues, and high-frequency checkpoints do
not run inside a COS mount.

## Consequences

- Local and COS adapters must share contract tests.
- Object paths and completion markers become part of the persisted protocol.
- Temporary local storage is disposable after remote verification.
- Aggregation ignores incomplete attempts.
