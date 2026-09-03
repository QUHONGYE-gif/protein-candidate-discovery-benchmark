# Governance v2 migration rehearsal and closeout

This record closes the repository-level Governance v2 migration rehearsal for MIRA-6. It records
engineering evidence only. It is not a Gate A or Gate B decision, a production deployment record,
an operation authorization, or a scientific result.

## Exact source chain

- Stage 3 merge baseline: `7d6c72b9d680d4fe779fe44e882d7551388643a1` (PR #98).
- Required Stage 4 handoff: `5eb8d704bf66467f9dedf4e3bf9c2e1fa013781e`.
- Stage 4 commits retained in order:
  - `af4334d87f95aed6dde240d8637a23a8f87da223` — repository workflow docs and templates;
  - `5eb8d704bf66467f9dedf4e3bf9c2e1fa013781e` — repository workflow Check.
- Stage 5 implementation rehearsal commit:
  `a19c2caaa187e23e2e9c6ed5d73baf69cc9ee679`, tree
  `48daf96f6f27c9209d32a5325dbd39e93380623d`.
- Accepted RFC SHA-256 after the rehearsal:
  `e9f5fbdadfc3f82fe1011fbc7e1e55ecc4d4c6a0c86db15473da3cf76b7a05d1`.

The final documentation/evidence commit necessarily follows the implementation commit above. The
final PR must contain the complete chain from Stage 3 through Stage 4 and Stage 5; it must not use
close intent because Multica `done` and merge remain member decisions.

## Rehearsal results

### M0–M4 and grandfathered execution surfaces

The rehearsal was read-only. No historical command was executed as a new scientific run, and no
historical result was republished. A path-scoped Git comparison from the Stage 3 baseline through
the Stage 5 implementation commit had an empty diff for the indexed M0–M4/M5 legacy modules,
manifests, packets, scripts and COS adapter. The following Git object identities were identical at
both ends:

| Surface | Git object ID |
|---|---|
| `src/pcdb/m2` | `6f2ccd2ebe74276641615eacfa1a2c25df7e7995` |
| `src/pcdb/m3` | `ea88ca2d0d630dc73188e7eb4f6f28b9befbaba3` |
| `src/pcdb/m4` | `64def911df769154ffe3e87f33e205bed20326cd` |
| `src/pcdb/m5` | `ecc1770ce62c6c1fd25c8a435e50865c49ba1a14` |
| `src/pcdb/storage/cos.py` | `05a43099136362c2c9c9a9598ae50c6a32b29476` |
| `manifests/experiments/m2` | `e3350968d40ac3f673ead35bbb77d68ab8844f76` |
| `manifests/experiments/m3` | `d68308855fd1053ad8afce988df7b2c9b72c13ba` |

The CLI help still exposes every entry listed in `LEGACY_EXECUTION_SURFACE.md`. This proves
addressability and unchanged repository bytes only; it does not claim replayability of external
data, credentials or historical infrastructure.

### Repository and M5+ ordinary-run fixture

The repository Check passed with report ID
`7c96f571b4e7781ff5d8dffd10735602614c1309b3536870f9e3f7cf47f987cc`. It included the
legacy surface, accepted RFC digest, Stage 4 PR/Issue/STATUS workflow, schemas, CI entry point and
example contract checks. The report conforms to `schemas/check-report.schema.json` and records
`approval_events_emitted = 0`.

The M5+ server-first example was checked with its exact manifest, Gate files, input root, run
identity, commit/tree and environment digest. Report
`a0107f87d5c0cd9f19412f1a7ddf5c994f44b1fbcfa6c8226a3c2a06c1b98b91` passed the
contract/input exact-byte binding, Gate structure, hidden-label boundary, cross-links, Run-pinned
identity and resource caps. Its run identity records `cos_write_count = 0`.

This command used `--allow-example`. The decision attestations remain non-final placeholders and
the report explicitly says `authentication = example_not_authenticated`; therefore it proves only
the fixture/schema/control path. It does not authorize a formal run or prove a human decision.

### Target-host server preflight

The real preflight on the local development host produced schema-valid report
`989b615ea38f29f62d0ba3c7eb5a14aff867d07935cada9ca5b7d12801ba2d99` with overall
status `fail` and `approval_events_emitted = 0`:

- configuration: `pass`;
- namespace/Unix group/mode: `fail` because group `pcdb` and every required `/srv/pcdb/`
  directory are absent;
- capacity: independently observed `10,585,026,560` free bytes on the nearest existing filesystem
  (`/`) and passed the 50% target for the one-byte rehearsal plan.

The probe was corrected during the rehearsal so a missing group or directory no longer prevents
capacity from being observed. The overall `fail` remains authoritative: the host is not provisioned
and cannot be used for a formal Governance v2 run. Tests cover the exact 50% boundary, above-target
failure, the 70% hard-cap failure, unexpected membership and unsafe modes.

### Local immutable terminal archive

The automated local-store matrix passed 12 tests:

- `accept`, `reject` and `inconclusive` each produce an immutable archive;
- `invalid_run`, `debug_run`, `aborted_attempt` and `failed_implementation_run` produce only an
  append-only server attempt record and zero archive writes;
- the archive manifest is schema-valid and contains the exact role/path/hash/size inventory;
- Gate B `evidence_digest`, the inventory `gate_b_decision` and the CLI `--gate-b` path/bytes are
  cross-bound;
- every object is fresh-read and SHA-256 checked, `_SUCCESS` is empty and written last;
- an existing prefix cannot be overwritten;
- a corrupted readback leaves no `_SUCCESS`, records failure, and a later attempt cannot resume the
  same partially written prefix.

The test CLI uses a fixture verifier so the local archive control path can be exercised without
network evidence. No authenticated GitHub Gate B comment was created or claimed. No COS adapter was
instantiated and the rehearsal performed zero COS writes.

### Backup, restore and reproducibility proxy

In the authorized local test boundary, the example evidence root was copied with `openrsync`, the
snapshot was made read-only, and it was restored to a new root. Canonical relative-path/SHA-256
inventory digests were identical for source, snapshot and restore:

`1697d36f6e1d022aaf063eddf931a8f98628a299f70f9c2b520244aca348e374`.

The same example contract Check was then run before backup and from restored bytes. After removing
the report timestamp and report ID, both reports had digest
`0ab8dd4de4578b687e4ec3aaa4e481f2b031abb5c2e5808e25dd43c70d5cc2e7` and status
`pass`.

This is a local mechanism/readback/reproducibility proxy. It does **not** satisfy production backup
deployment: no high-cost trajectory or release candidate was available, no daily scheduler was
observed, and an approximately 24-hour RPO has not been demonstrated on a target server.

## Commands and schemas

The verified operator entry points remain:

```bash
pcdb governance-check repository --repository-root . [--output <report.json>]

pcdb governance-decision-digest \
  --decision <gate-a-or-gate-b-decision.json> \
  [--output <decision-digest.json>]

pcdb governance-check contract \
  --manifest <milestone-contract-manifest.json> \
  --gate-a <gate-a-decision.json> \
  [--gate-b <gate-b-decision.json>] \
  [--run-identity <run-identity.json>] \
  --config configs/governance/v2.json \
  --source-root <exact-input-root> \
  [--runtime-git-commit <40-hex>] \
  [--runtime-git-tree <40-hex>] \
  [--runtime-environment-digest <64-hex>] \
  [--output <report.json>]

pcdb governance-check server \
  --config configs/governance/v2.json \
  --expected-peak-bytes <bytes> \
  [--output <report.json>]

pcdb governance-archive \
  --request <archive-request.json> \
  --source-root <sealed-evidence-root> \
  --gate-b <gate-b-decision.json> \
  --config configs/governance/v2.json \
  --local-store <immutable-test-store> \
  --attempt-root <append-only-attempt-root> \
  [--output <archive-receipt.json>]
```

Production COS publication substitutes the separately authorized `--cos-bucket` destination; this
rehearsal did not do so. The active schema list is:

- `schemas/governance-config.schema.json` — `pcdb-governance-config-v1`;
- `schemas/milestone-contract-manifest.schema.json` —
  `pcdb-milestone-contract-manifest-v1`;
- `schemas/gate-a-decision.schema.json` — `pcdb-gate-a-decision-v1`;
- `schemas/gate-b-decision.schema.json` — `pcdb-gate-b-decision-v1`;
- `schemas/governance-run-identity.schema.json` — `pcdb-governance-run-identity-v1`;
- `schemas/check-report.schema.json` — `pcdb-governance-check-report-v1`;
- `schemas/milestone-archive-request.schema.json` —
  `pcdb-milestone-archive-request-v1`;
- `schemas/milestone-archive-manifest.schema.json` —
  `pcdb-milestone-archive-manifest-v1`;
- `schemas/archive-attempt-record.schema.json` — `pcdb-archive-attempt-record-v1`.

## Evidence and claim boundary

Completed in this repository/local test boundary:

- exact Stage 4 handoff continuation and immutable legacy-byte comparison;
- repository, contract fixture and real local-host server reports;
- all three valid terminal verdicts and all four excluded attempt classes in a local-store matrix;
- exact Gate B/inventory/readback/prefix/marker failure controls with offline fixture evidence;
- one local snapshot/restore/readback/reproducibility proxy;
- Stage 4 PR/Issue/cross-link/maintenance/STATUS rules checked against config, docs, templates and
  commands.

Still required before a production run or closeout:

- provision `/srv/pcdb/`, group `pcdb`, exact members/service accounts and safe setgid modes on the
  designated host, then rerun the real server preflight;
- deploy and observe the daily snapshot/rsync path on actual high-cost trajectories or release
  candidates, and perform a target-host restore/reproducibility test demonstrating the intended
  approximately 24-hour RPO;
- create final Gate A/B records backed by fresh-authenticated GitHub authority comments;
- obtain separate operation authorization before any real COS write, migration, credential use or
  deletion.

No COS write/delete, raw-data migration, hidden/truth access, scientific experiment, server
provisioning, GitHub ruleset/required-check change, or independent GitHub Issue occurred. GitHub
Issue #101 was not restored. `docs/STATUS.md` was not changed because this engineering rehearsal did
not create a Gate A scientific transition, Gate B terminal or scientific milestone closeout.
