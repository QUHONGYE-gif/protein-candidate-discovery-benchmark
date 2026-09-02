# Governance v2 server-first and archive runbook

The normative rules are in [`GOVERNANCE_V2.md`](GOVERNANCE_V2.md). This runbook turns those rules
into the minimum operator path. It does not authorize a scientific run, hidden-label access, COS
credentials, data deletion or a GitHub ruleset change.

## Deployment identities

`configs/governance/v2.json` is the machine-readable source for the two scientific-authority
handles and deployment names. Governance v2 uses Unix group `pcdb`; `pcdb-runner` owns ordinary
server attempts and has no COS write capability, while `pcdb-archiver` receives only the temporary
credentials required for immutable milestone publication. Human write membership is limited to
the two configured scientific authorities.

Before production use, an operator must verify that the configured identities resolve to the
intended humans/accounts on the target server. A mismatch is a failed Check, not an approval prompt.

## Server namespace and ACL

Provision these setgid directories under one filesystem boundary:

```text
/srv/pcdb/data/{raw,processed,embeddings,splits}
/srv/pcdb/runs
/srv/pcdb/releases
/srv/pcdb/scratch
```

The operator creates group `pcdb`, adds exactly the configured users/service accounts, assigns the
directories to that group, removes world access and enables group inheritance. Verify the resolved
group, writer list and mode before the first run; do not store credentials in this config or Git.

Each M5+ attempt writes to a new append-only directory beneath
`/srv/pcdb/runs/<milestone-id>/<attempt-id>/`. Active raw, derived inputs, embeddings and split
identities are SHA-256-bound server artifacts. An ordinary run process has no COS destination and
must report `cos_write_count = 0`.

## Capacity and backup preflight

Run the same machine entry point used by CI before starting a milestone:

```bash
pcdb governance-check server \
  --config configs/governance/v2.json \
  --expected-peak-bytes <bytes> \
  --available-bytes <bytes> \
  --observed-group pcdb \
  --observed-writer github:QUHONGYE-gif \
  --observed-writer git:will-zhang \
  --observed-writer service:pcdb-runner \
  --observed-writer service:pcdb-archiver
```

A peak at or below 50% of currently available space passes the target. Above 50% requires an
explicit smaller plan or more space; 70% is the hard fail-closed cap and cannot be overridden by a
human approval event. Public raw keeps source URL and SHA-256 so it can be downloaded again.
Low-cost processed/embedding data may be recomputed. High-cost trajectories and release candidates
receive a daily snapshot or rsync copy with target RPO of about 24 hours. Restore/readback is tested
before treating the backup as evidence.

## Contract and run preflight

Create one `Milestone Contract Manifest` and Gate A record from the schemas in `schemas/`. Validate
the exact files before the first formal side effect:

```bash
pcdb governance-check contract \
  --manifest <milestone-contract-manifest.json> \
  --gate-a <gate-a-decision.json> \
  --run-identity <run-identity.json> \
  --config configs/governance/v2.json \
  --source-root <exact-input-root> \
  --runtime-git-commit <40-hex-commit> \
  --runtime-git-tree <40-hex-tree> \
  --runtime-environment-digest <64-hex-sha256>
```

A Check report has `pass`, `fail` or `error`; `fail` and `error` both block the protected action.
The report cannot authorize a contract or convert a Gate decision.

## Terminal milestone archive

After Gate B produces `accept`, `reject` or `inconclusive`, prepare the exact compact inventory and
run the independent archive command. The request includes contract manifest, run identities, final
tables/results, important logs, summary, checksums/provenance and Gate B decision. Large raw data and
invalid/debug/aborted/failed-implementation attempt payloads are excluded.

```bash
pcdb governance-archive \
  --request <archive-request.json> \
  --source-root <sealed-evidence-root> \
  --gate-b <gate-b-decision.json> \
  --config configs/governance/v2.json \
  --cos-bucket <bucket> \
  --cos-region ap-beijing \
  --attempt-root /srv/pcdb/runs
```

The archiver checks that the new prefix is empty, uses forbid-overwrite writes, fresh-reads every
object, reconciles the exact inventory and writes the empty `_SUCCESS` marker last. A partial or
failed publication has no marker and is never resumed in place; retry with a new archive ID/prefix.
Every invocation leaves an append-only server-side attempt record. Excluded attempt classes leave
only that record and perform zero COS writes.

## Migration and closeout

M0–M4 keep their historical COS identities. M5+ starts server-first; needed legacy raw is copied on
demand, checksum-verified and shadow-read before cutover. Never delete the legacy object during this
procedure. Deletion remains unauthorized until server copy, reconciliation, a real milestone and a
restore/reproducibility check have all completed and a separate decision is made.

Update `docs/STATUS.md` only for Gate A scientific transition, Gate B terminal or milestone
closeout. Record contract/evidence/archive identities and claim boundary; do not treat a PR, CI or
server completion as a scientific result.
