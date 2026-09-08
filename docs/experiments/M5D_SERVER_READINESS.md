# M5D server-first readiness — Issue #104

Tracking authority: `github_issue`,
https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/104.
The owner approved implementation on 2026-09-08 and selected `129.28.97.247`
(`VM-0-8-debian`) as the sole authoritative data and execution server for this work.
This supersedes the proposed use of another host for future work; historical M5C
Gate records and successful artifacts retain their original identities.

## Decision and boundary

Determine whether the exact accepted SKEMPI raw bundle can produce deterministic,
identity-aligned external-task candidates with conservative leakage groups and enough
candidates for the existing 32/64 initial observations plus a 16-candidate batch.
Readiness is not a strategy result. Formal M5D remains downstream of M5C and a
reviewed external execution contract; no formal held-out partition is opened here.

The readiness smoke uses one process, at most 2 CPU, 1.5 GiB RSS, 20 minutes and
1 GiB scratch, on the selected host. Two independent builds must have identical
bytes. Hash mismatch, ambiguous identity, unresolved leakage links, resource excess
or unexpected output causes a stop. No fitting, acquisition, high-value membership,
effect estimates, COS writes/deletes, new dependencies or paid resources.

## Source and storage

The two source objects retain the exact SHA-256 and size from the accepted #12
Dataset Manifest. Missing bytes may be reacquired from the frozen official URLs
and must match those identities before promotion to the server raw directory.
This is source recovery, not a new dataset revision. Original COS objects remain.

- Active raw: `/srv/pcdb/data/raw/skempi/v2.0/2018-06/source-bundle/`.
- Attempts: `/srv/pcdb/runs/M5D/<new-attempt-id>/`.
- Existing ProteinGym import stays in its verified server location.
- Git contains code, contracts and compact readiness summaries only.
- This slice does not attest production backup/RPO or scientific Gate acceptance.

## Processing contract candidate

Source semantics follow the official
[SKEMPI FAQ](https://life.bsc.es/pid/skempi2/info/faq_and_help).
The normalized target is binding improvement, `-delta_delta_g`, in kcal/mol:
`-(8.314 / 4184) * temperature_kelvin * ln(Kd_mut / Kd_wt)`.
Larger means stronger binding relative to the matched wild type. Exact positive
affinities and explicit numeric Kelvin temperatures are required; censored,
missing, ambiguous or non-finite values are excluded with reason counts.
No room-temperature imputation or complex-level label normalization is allowed.

Candidate identity uses the original PDB complex and sorted original mutation
tokens (including insertion codes). Original-to-cleaned positions and wild-type
residues are checked against the publisher mapping and cleaned PDB residue names;
no positional conversion or structure repair is invented. Conflicting mappings never
choose a first/last match: every candidate touching an ambiguous or missing residue
mapping is excluded and counted. Repeated measurements of the same candidate
are aggregated only at an identical temperature, by the median binding-improvement
target; candidates measured at multiple temperatures are excluded conservatively.
All duplicate source counts and exclusions remain auditable.

Observable fields contain only the complex, mutation tokens/count and residue
substitutions. Affinity, temperature, references, labels and evaluator-derived
membership are not features. Candidate, truth, provenance and grouping files are
separate. Feature encoding consumes an observable object only.

Leakage components conservatively union identical PDB IDs, identical normalized
protein names, protein-pair identities and publisher hold-out links/types. Malformed
hold-out links fail closed; syntactically valid absent complexes remain virtual graph
nodes so shared exclusions still connect. Components are a split constraint, not proof of complete
sequence-homology separation. Every component is assigned `external_candidate`;
no development/held-out train/test allocation is invented before review. A later
partitioner must keep whole components together.

Report candidate counts at predeclared pool sizes 48 and 80 per PDB complex and
the number of independent leakage components containing eligible complexes.
These are feasibility thresholds only, not an authorized scientific cohort. Candidate
counts, duplicate handling and group sizes cannot be used to select for result direction.

## Verification and next decision

Tests cover mutation and sign/unit semantics, censored values, repeated measurements,
cross-complex leakage, order-independent identities, schema validation and observable
invariance when hidden labels change. The server runs two isolated deterministic builds
and compares every output digest. No raw rows or candidate-level artifacts are published.

Proceed to external-contract review only if the source, A/B build and identity tests
pass; revise if pool size or grouping makes the planned comparison infeasible; stop
on validity failure. Formal budgets and statistical units must be fixed from this
readiness evidence before any strategy result, and must not be borrowed from another host.
