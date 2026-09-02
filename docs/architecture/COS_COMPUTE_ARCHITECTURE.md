# COS and Compute Architecture

> Historical M0–M4 architecture and adapter rationale. Governance v2 supersedes its active data
> plane for new M5+ milestones with server-first execution and milestone-only COS archive. Existing
> paths and objects remain a Legacy Execution Surface; see `../GOVERNANCE_V2.md` and
> `../GOVERNANCE_V2_RUNBOOK.md`.

## Decision summary

GitHub is the control plane, COS is the immutable data and artifact plane, and compute servers are
replaceable workers with local NVMe caches. COS is accessed through explicit storage operations;
it is not treated as a shared POSIX filesystem.

## Planes

### Control plane

- GitHub stores code, configuration, schemas, Issues, PRs, and small fixtures.
- A coordinator compiles immutable RunSpecs and tracks task state.
- The pilot may use SQLite and a local process queue.
- Scale-out may replace them with PostgreSQL and Redis without changing domain contracts.

### Data plane

- A data bucket stores raw public data, manifests, processed datasets, features, and splits.
- An artifact bucket stores attempts, trajectories, metrics, logs, summaries, and releases.
- Bucket region should match compute region to reduce latency and transfer cost.

### Compute plane

- CPU workers handle data processing, table models, and statistics.
- GPU workers handle selected embedding, TabPFN, and ensemble workloads.
- Workers stage inputs by checksum, execute one idempotent task, validate outputs, upload artifacts,
  and write `_SUCCESS` last.

## Initial object layout

```text
cos://pcdb-data-{region}/
├── raw/{dataset}/{version}/
├── manifests/{dataset}/{version}/
├── processed/{dataset}/{processing_hash}/
├── features/{dataset}/{feature_hash}/
└── splits/{dataset}/{protocol_hash}/

cos://pcdb-artifacts-{region}/
├── runs/{experiment_id}/{run_id}/attempts/{attempt_id}/
├── summaries/{experiment_id}/
└── releases/{release_version}/
```

## Security

- Prefer temporary CAM credentials and prefix-scoped permissions.
- Keep raw input read-only to workers.
- Restrict release writes to the coordinator or release identity.
- Never expose permanent COS credentials to pull-request workflows.
- Do not register a scientific compute server as an unrestricted PR self-hosted runner.

## Non-goals for M0

M0 does not provision buckets, credentials, Redis, PostgreSQL, Kubernetes, or paid compute. It
defines contracts that allow those adapters to be introduced after the pilot justifies them.
