# Governance v2 server-first and archive runbook

The normative rules are in [`GOVERNANCE_V2.md`](GOVERNANCE_V2.md). This runbook turns those rules
into the minimum operator path. It does not authorize a scientific run, hidden-label access, COS
credentials, data deletion or a GitHub ruleset change.

[`ADR-0005`](adr/0005-adopt-governance-v2-for-new-milestones.md) fixes the activation boundary. V2
applies only to a milestone first authorized after that decision and this implementation merge.
Current #91/M5B and all already-authorized work remain on their reviewed legacy path. The RFC's
section 1 “本阶段” restrictions describe its Stage 2 drafting turn; its section 10 defines the later
implementation sequence, subject to ADR-0005.

## Repository PR and Issue workflow

Every change entering `main` still uses a reviewed PR. The two workflow classes decide review
scope and timing; neither is an approval event or a ruleset bypass.

| Question | Major / semantic PR | Maintenance batch PR |
|---|---|---|
| What belongs | Scientific protocol, dataset/split, metric, cost model, architecture, major runner, storage policy, governance, release | Tests, docs, logging, small bugs, minor refactors, plots and CLI polish |
| Override | Any possible change to scientific semantics, data identity, execution boundary, storage responsibility or public interface makes the change Major | Every item must leave all five boundaries unchanged and be independently reversible |
| Earliest PR trigger | As soon as the smallest reviewable semantic unit exists | First of: 10 logical small changes; current Issue/milestone closeout; the next experiment needs the information |
| Evidence | Contract/ADR and compatibility impact, claim boundary, tests | Atomic commits, parent Issue checklist, per-item tests and no-semantic-change statement |

Classify the effect, not the diff size or label. A one-line primary-metric, split-membership, archive-
prefix or CLI-output-contract change is Major. A typo, a behavior-preserving test, extra logging or
a plot-label fix may be Maintenance. A small bug that changes candidate identity or a public
interface is Major. Split mixed work; if it cannot be split safely, treat the whole PR as Major.

Select exactly one tracking authority, `github_issue` or `multica`, when any of these thresholds
applies: Major/semantic work; independent user/scientific value, acceptance criteria, risk or
authorization boundary; cross-milestone, multi-PR, multi-owner or external coordination; data
migration, held-out, permission, cost, privacy, security, release or external-configuration risk;
or no suitable parent Issue. A pure maintenance item with a suitable authority record stays there.
Git commits plus that authority's checklist/comment are the only ledger; do not add
`maintenance-batch-ledger.yaml`, `MulticaGithubLinkRegistry` or another status registry.

Every declaration includes `authority` and `authorization_and_acceptance`. A `github_issue`
declaration also includes `repository`, `issue_number` and `url`. A `multica` declaration includes
the stable `workspace_id` and, for every tracked object, `object_id`, MIRA `key` and `url`. Bare MIRA
keys are not sufficient. The migration-review decision authorizing this choice is recorded in
[PR #102](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/102#issuecomment-5536839481).

When `github_issue` is selected, one GitHub Issue may cover several Multica execution issues. Use
only these minimal cross-links:

```text
Multica title: [GH#123] Improve repository workflow checks
Multica description: Source of truth: https://github.com/OWNER/REPO/issues/123
GitHub Issue comment: Execution tracked in Multica: https://MULTICA-EXAMPLE/issues/MIRA-123
```

The URLs are placeholders; do not create external objects for an example. When `multica` is
selected, the PR carries the declaration and no formal GitHub parent or cross-link is required. A
maintenance authority comment can remain a normal checklist:

```text
Maintenance batch (3/10 logical changes)
- [x] abc1234 docs: clarify retry wording — docs check
- [x] def5678 test: cover empty log field — targeted pytest
- [x] 012abcd cli: clarify help text only — CLI snapshot test
Trigger: current milestone closeout
Semantic/data/interface impact: none
```

The PR template records exactly one class, one tracking authority with its required fields, and
(for a Maintenance batch) the first trigger reached. GitHub Issue forms confirm that GitHub was
intentionally selected instead of Multica-only tracking. These records organize engineering
review; they do not produce Gate A/B or Check approvals.

## Deployment identities

`configs/governance/v2.json` is the machine-readable source for the two GitHub authority identities,
their server usernames and deployment names. Governance v2 uses Unix group `pcdb`; `pcdb-runner` owns ordinary
server attempts and has no COS write capability, while `pcdb-archiver` receives only the temporary
credentials required for immutable milestone publication. Human write membership is limited to
the two configured scientific authorities.

Final Gate records do not trust those strings by themselves. Each human decision references a
GitHub issue/PR comment and binds its authenticated actor, comment/node ID, `updated_at`, exact body
SHA-256 and exact decision digest. The checker fresh-fetches that object from GitHub; a missing,
edited or differently authored comment fails closed. `GITHUB_TOKEN` may provide read-only access for
a private repository and is never passed in a manifest or CLI argument.

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

Run this command on the target host before starting a milestone:

```bash
pcdb governance-check server \
  --config configs/governance/v2.json \
  --expected-peak-bytes <bytes>
```

The checker itself reads the host name, Unix group and primary/supplementary membership, directory
type/group/mode and filesystem free space. The caller cannot supply observed ACL or capacity facts.
Every required directory must be group-owned, group-writable, setgid and closed to other users. A
peak at or below 50% of currently available space passes the target. Above 50% requires an
explicit smaller plan or more space; 70% is the hard fail-closed cap and cannot be overridden by a
human approval event. Public raw keeps source URL and SHA-256 so it can be downloaded again.
Low-cost processed/embedding data may be recomputed. High-cost trajectories and release candidates
receive a daily snapshot or rsync copy with target RPO of about 24 hours. Restore/readback is tested
before treating the backup as evidence.

## Contract and run preflight

Create one `Milestone Contract Manifest` and Gate A record from the schemas in `schemas/`. Validate
the exact files before the first formal side effect:

Before filling an attestation envelope, compute the decision digest and post the emitted marker as
an exact line in the approving authority's GitHub comment:

```bash
pcdb governance-decision-digest --decision <gate-a-or-gate-b-decision.json>
```

Then record the comment's authenticated actor, numeric ID, node ID, `updated_at` and exact UTF-8 body
SHA-256. For a final decision, the contract Check fresh-fetches the comment and verifies all fields.
Examples use non-final placeholder attestations and never authorize execution.

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

The Gate B `evidence_digest` is the canonical digest of the proposed non-decision archive inventory.
The archiver requires its CLI Gate B path to be the same exact object as the inventory's
`gate_b_decision`, checks the evidence digest, verifies the authenticated reviewer comment, checks
that the new prefix is empty, uses forbid-overwrite writes, fresh-reads every object, reconciles the
exact inventory and writes the empty `_SUCCESS` marker last. A partial or
failed publication has no marker and is never resumed in place; retry with a new archive ID/prefix.
Every invocation leaves an append-only server-side attempt record. Excluded attempt classes leave
only that record and perform zero COS writes. `pcdb governance-archive` is the synchronous mandatory
closeout entry point; no background daemon or event queue is claimed in this PR. The first future v2
milestone must invoke it after Gate B and treat a nonzero exit as incomplete closeout.

## Migration and closeout

The repository-level Stage 5 rehearsal and its explicit deployment gaps are recorded in
[`GOVERNANCE_V2_MIGRATION_CLOSEOUT.md`](GOVERNANCE_V2_MIGRATION_CLOSEOUT.md). That engineering
record does not replace final authenticated Gate evidence, target-host preflight, production backup
evidence or a separate COS operation authorization.

M0–M4 and current #91/M5B keep their reviewed COS identities. A future milestone first authorized
under ADR-0005 starts server-first; needed legacy raw is copied on demand, checksum-verified and
shadow-read before cutover. Never delete the legacy object during this procedure. Deletion remains
unauthorized until server copy, reconciliation, a real milestone and a restore/reproducibility check
have all completed and a separate decision is made.

Update `docs/STATUS.md` only for Gate A scientific transition, Gate B terminal or milestone
closeout. Record contract/evidence/archive identities and claim boundary; do not treat a PR, CI or
server completion as a scientific result.
