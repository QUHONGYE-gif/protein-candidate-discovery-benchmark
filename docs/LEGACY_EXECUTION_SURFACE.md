# Legacy Execution Surface index

Governance v2 adds new entry points beside the historical implementation. This index is the only
new label applied to the old surface: the listed files, commands, object paths, manifests, hashes,
terminals and verdicts retain their original meaning and exact bytes. They are not Governance v2
templates and are not batch-renamed or rewritten.

## Historical entry points

| Scope | CLI / script entry | Implementation and evidence retained in place |
|---|---|---|
| M2 | `pcdb m2-synthetic`, `pcdb m2-preflight`, `pcdb m2-gate-b` | `src/pcdb/m2/`, `scripts/build_m2_gate_a_packet.py`, `scripts/build_m2_gate_b_packet.py`, `manifests/experiments/m2/`, `docs/M2_GATE_B_REVIEW_PACKET.md` |
| M3 | `pcdb m3-gate-a-packet`, `pcdb m3-gate-a`, `pcdb m3-gate-b-packet`, `pcdb m3-gate-b` | `src/pcdb/m3/`, `manifests/experiments/m3/`, `docs/M3_GATE_A_REVIEW_PACKET.md`, `docs/M3_GATE_B_REVIEW_PACKET.md`, `docs/M3_SINGLE_FIDELITY_REPLAY_PROTOCOL_V1.md` |
| M4 | `pcdb m4-pilot-preflight`, `pcdb m4-pilot-run`, `pcdb m4-conditional-preflight`, `pcdb m4-conditional-run` | `src/pcdb/m4/`, the Issue #76 packets, immutable COS prefixes, attempt ledgers and Gate 3 terminal |
| Existing issue-scoped M5 analysis | `pcdb m5-seed-tracer-preflight`, `pcdb m5-seed-tracer-run`, `scripts/run_m5_reconstruction_smoke.py`, `scripts/run_m5_formal_reconstruction.py`, `scripts/run_m5b_iteration.py` | `src/pcdb/m5/`, existing append-only attempt roots and accepted evidence identities |
| Existing dataset publication | `pcdb proteingym-four-assay`, `pcdb proteingym-local-bundle`, `pcdb proteingym-continue` | Existing issue-scoped ProteinGym authorization, local handoff and COS publication paths |

The historical COS adapters in `src/pcdb/storage/cos.py` remain issue-scoped capabilities. The new
milestone archiver uses a separate prefix and does not broaden, reinterpret or replace them.

## Compatibility rules

- Read-only audit and explicitly labelled historical replay remain allowed.
- A milestone whose first Gate A is authorized after ADR-0005 merges uses `pcdb governance-check`
  and server-first storage. Current #91/M5B and other already-authorized work stay on their reviewed
  commands and data plane; v2 does not become a mid-chain prerequisite.
- A correctness or security patch to a legacy path must preserve its scientific contract and create
  a new source identity. It must not overwrite historical evidence.
- A path that can no longer run safely remains an indexed historical record with its limitation;
  its terminal is not reinterpreted.
- Existing COS raw objects are not deleted by Governance v2.

## Active Governance v2 entry points

`pcdb governance-check` emits one versioned Check report and never emits an approval event.
`pcdb governance-archive` is the only new COS publication path: it accepts a valid Gate B terminal,
writes a compact immutable bundle to `milestone-archives/v2/`, verifies a fresh readback and writes
`_SUCCESS` last. These entry points are intentionally independent of the legacy modules above.
