# ADR-0005: Adopt Governance v2 only for newly authorized milestones

- Status: Accepted by the project owner; implementation remains subject to PR review
- Date: 2026-09-02
- Decision artifact: `docs/GOVERNANCE_V2.md`
- Decision artifact SHA-256: `e9f5fbdadfc3f82fe1011fbc7e1e55ecc4d4c6a0c86db15473da3cf76b7a05d1`
- Execution tracking: MIRA-3 and MIRA-4; PR #98
- Owner decision inputs: MIRA-3 member comment `01a0612c-0994-7e9c-8547-a1b61bac4e9e` and
  MIRA-4 correction direction `01a061d8-aeab-7b0e-b056-491fadfbd31d`

## Context

ADR-0002 established COS as the immutable artifact plane for the original worker architecture.
Governance v2 instead proposes a server-first active data plane and milestone-only COS archives. The
accepted RFC defines that target, but its Stage 2 drafting boundary and the cutover point were not
explicit enough for a repository-only reviewer to determine whether an already-running M5 workflow
would be changed in place.

The project owner directed PR #98 to be corrected against the formal review. This ADR records the
least disruptive interpretation: adopt the exact RFC prospectively and do not insert it into an
already-authorized attempt chain.

## Decision

Governance v2 becomes eligible only after this ADR and its implementation are approved and merged.
It applies to a milestone whose first Gate A contract is created after that merge. The Gate A record
must identify Governance v2 and the exact contract digest before the first formal side effect.

Issue #91 / the current M5B chain, every other milestone already authorized or executing before
that point, and all M0–M4 paths are grandfathered. They keep their reviewed source, data plane,
operation authority, evidence stage and terminal semantics. Governance v2 must not block, migrate,
reinterpret or republish them. A later migration requires a new explicit decision and a new
contract; it is never inferred from this ADR.

For newly authorized Governance v2 milestones, this ADR supersedes ADR-0002 only where ADR-0002
assigns active datasets and ordinary run outputs to COS. ADR-0002 remains authoritative for the
object-store safety rule: COS is not a POSIX filesystem, publication is immutable, and `_SUCCESS`
is written last. Existing COS objects and all historical identities remain unchanged.

Before any production execution or archive, machine Checks must authenticate human Gate evidence
against its external actor/object/version and must observe server directory, group, ACL and capacity
state from the target host. Configuration declarations alone are not execution evidence.

## Consequences

- M5B/#91 can finish under its existing authorization without a Governance v2 prerequisite.
- Merging implementation code provisions no server, migrates no data, writes no COS object and
  grants no scientific or operational authorization.
- `/srv/pcdb`, service accounts and backup jobs remain undeployed until separately authorized and
  verified on the target host.
- The first future Governance v2 milestone is the earliest valid production trial. Until then the
  schemas, validators and local archive adapter are engineering capabilities, not scientific proof.
- Reverting this prospective adoption requires reverting the active documentation and entry points;
  it does not require changing historical evidence or data.
