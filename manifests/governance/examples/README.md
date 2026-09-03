# Governance v2 examples

`m5-example/` demonstrates the exact `Milestone Contract Manifest`, Gate A, Gate B and server-first
run identity shapes. Every decision has `record_status: example`; it is test data, not scientific
authorization, evidence or a historical milestone. Production checks reject example records unless
the explicit developer-only `--allow-example` flag is used.

The example attestation envelopes use placeholder GitHub comment identities and body hashes. A
final record must instead bind a fresh-fetched GitHub actor, comment/node ID, object `updated_at`,
exact body hash and the digest emitted by `pcdb governance-decision-digest`; placeholder evidence
can never pass a production Check.

The manifest binds the canonical SHA-256 of its `scientific_contract` object and the exact bytes of
each decision/input. A terminal revision adds the Gate B reference without rewriting the frozen
scientific contract digest. Production revisions are append-only artifacts; the example file is
only maintained alongside code and schemas.

Repository-workflow examples are deliberately kept in
[`docs/GOVERNANCE_V2_RUNBOOK.md`](../../../docs/GOVERNANCE_V2_RUNBOOK.md), not in another manifest
or registry. They show Major / semantic PR and Maintenance batch PR classification, a parent Issue
checklist, and placeholder Multica/GitHub many-to-one links. The repository Check validates those
examples against `configs/governance/v2.json`; it does not create or approve an external Issue or
PR.
