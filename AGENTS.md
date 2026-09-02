# Repository Guidelines

## Scope and instruction precedence

This file applies to the entire repository.

Codex reads repository instructions from the repository root toward the current working
directory. Add a nested `AGENTS.override.md` only when a subdirectory has genuinely different
requirements. Do not duplicate root-level guidance in nested files.

Direct user instructions and accepted ADRs take precedence over general conventions in this
file. If instructions conflict, stop and surface the conflict before changing code or scientific
contracts.

## Project mission

This repository implements a reproducible public-data benchmark for cost-aware, multi-fidelity
active learning in protein candidate discovery.

The primary question is not which surrogate model has the lowest average prediction error. The
benchmark evaluates which surrogate-model and acquisition-strategy combination discovers
high-value candidates most efficiently under fixed label or cost budgets.

The initial scope covers FLIP2, selected ProteinGym assays, SKEMPI 2.0 external validation,
single-fidelity replay, controlled and real multi-fidelity experiments, uncertainty calibration,
decision metrics, and cost-aware evaluation.

## Scientific and privacy boundaries

Only public data may be used. Never add, upload, infer from, or tune against private sequences,
internal targets, internal model outputs, private structural or MD results, wet-lab results,
internal thresholds, candidate counts, hit rates, or score distributions.

Do not describe structure-compatible sequences as homologs unless natural-sequence evidence
supports homology.

Keep these states separate in code, Issues, PRs, and reports:

1. implementation complete;
2. tests and CI green;
3. server execution complete;
4. benchmark evidence complete;
5. scientific gate passed.

Green tests do not imply a positive scientific result.

## Think before coding

Before a non-trivial change:

- read the linked Issue, relevant comments, `CONTEXT.md`, contracts, and ADRs;
- state assumptions that materially affect the implementation;
- surface ambiguity instead of silently choosing an interpretation;
- identify the smallest independently testable slice;
- define verification before editing;
- check whether the change affects a frozen dataset, split, metric, cost model, or held-out boundary.

Ask before adding a production dependency, changing a public or persisted contract, changing a
frozen protocol, accessing held-out data, introducing a cloud service, or changing privacy and
licensing boundaries.

## Simplicity first

Implement the minimum design required by the current Issue. Do not add speculative abstractions,
unused flexibility, or infrastructure that the pilot has not justified. Prefer explicit manifests
and immutable artifacts over hidden framework state. Start with local and COS implementations
behind the same narrow storage contract.

## Surgical changes

Every changed line should trace to the current Issue or an unavoidable verification fix. Do not
refactor or reformat unrelated code. Preserve user changes. Match established conventions. Remove
only artifacts made obsolete by the current change. Report unrelated problems rather than fixing
them silently. Never rewrite an accepted ADR in place; add a superseding ADR.

## Goal-driven execution

For multi-step work:

1. define or reproduce expected behavior;
2. add or update the smallest relevant test;
3. implement the bounded change;
4. run targeted checks;
5. run the appropriate wider checks;
6. inspect the diff for unrelated changes;
7. report engineering and scientific status separately.

## Repository architecture

- `src/pcdb/contracts/`: stable dataset, model, storage, and run contracts;
- `src/pcdb/datasets/`: public dataset adapters;
- `src/pcdb/storage/`: local and COS artifact backends;
- `src/pcdb/features/`: reproducible feature generation;
- `src/pcdb/surrogates/`: surrogate models;
- `src/pcdb/uncertainty/`: uncertainty estimation and calibration;
- `src/pcdb/acquisition/`: acquisition strategies;
- `src/pcdb/replay/`: hidden-label active-learning replay;
- `src/pcdb/multifidelity/`: fidelity and cost-aware logic;
- `src/pcdb/metrics/`: prediction and decision metrics;
- `src/pcdb/orchestration/`: run compilation, execution, and recovery;
- `src/pcdb/reporting/`: summaries, tables, and figures;
- `configs/`: versioned declarative configuration;
- `schemas/`: machine-validated manifests and run specifications;
- `tests/fixtures/`: small public test fixtures only;
- `docs/adr/`: architectural decision records.

Keep domain logic independent from COS, GitHub, Redis, PostgreSQL, and server runtime details.

## Hidden-label boundary

Candidate features, hidden truth, and revealed observations must use separate objects and
interfaces. Only the replay engine may call the hidden oracle. Surrogate models, acquisition
strategies, feature transformers, and metric-selection logic must not receive unrevealed labels.
Add fail-closed leakage tests whenever dataset, replay, split, feature, or evaluation code changes.

## Data and artifact rules

Git stores source code, configuration, schemas, manifests, documentation, small public fixtures,
and compact summaries. M0–M4 retain their historical COS storage identities. For M5+,
`/srv/pcdb/` is the active raw/processed/embedding/split and run data plane; COS receives only the
compact immutable archive of a valid terminal milestone after Gate B.

Do not treat COS as a POSIX shared filesystem. Every scientific artifact must be traceable to its
Git commit, environment or container digest, dataset manifest, split and feature hashes,
configuration, seed, runtime attempt, and output checksums. Never overwrite raw data, frozen
splits, successful runs, or releases. A run completes only after validation and `_SUCCESS`.

## Local and cloud execution

Formal experiments run on designated compute servers using an exact Git commit or container
digest. M5+ ordinary runs use append-only server attempt directories and must perform zero COS
writes. After Gate B, the separate archiver fresh-readback verifies a compact immutable COS bundle
before writing `_SUCCESS` last. Never store permanent COS credentials in Git, examples, Issues,
PRs, logs, or fixtures. GitHub Actions validates code and small fixtures; it does not run the full
benchmark or serve as the scientific artifact store. See `docs/GOVERNANCE_V2.md` and
`docs/GOVERNANCE_V2_RUNBOOK.md`; legacy entry points remain indexed in
`docs/LEGACY_EXECUTION_SURFACE.md`.

## Testing and verification

Use the canonical commands:

- `make format`: format owned files;
- `make lint`: static checks;
- `make test`: default test suite;
- `make governance-check`: Governance v2 docs/schema/example compatibility checks;
- `make check`: required pre-PR checks;
- `make replay-smoke`: deterministic small-fixture replay.

Run the narrowest relevant test first, then the wider required suite. Critical categories include
schema validation, deterministic replay, label-leakage prevention, split isolation, storage parity,
interrupted-run recovery, checksum verification, metric correctness, and provenance validation.
Never replace a missing scientific run with mocked evidence.

## GitHub workflow

GitHub Issues are authoritative for task-specific decisions and lifecycle. `docs/STATUS.md` is
authoritative for the cross-project current-state summary. Use short-lived Issue-linked branches
named `feat/`, `fix/`, `data/`, `exp/`, `infra/`, `docs/`, or `codex/`. Do not create long-lived
personal, `dev`, or `test` branches.

Open a Draft PR early for non-trivial work. State scope, out-of-scope items, verification, data and
privacy impact, protocol impact, and the boundary of scientific claims. Use `Closes #<issue>` only
when all acceptance criteria are met. Merge reviewed PRs through squash merge. Do not force-push
or commit directly to protected `main`.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for
`QUHONGYE-gif/protein-candidate-discovery-benchmark`. Use the GitHub connector first and the GitHub
CLI only when available. See `docs/agents/issue-tracker.md`.

### Triage labels

Use `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. Do not create
synonyms or duplicate lifecycle labels. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read `CONTEXT.md` and relevant ADRs before changing domain
terminology, public contracts, data protocols, storage semantics, or scientific gates. See
`docs/agents/domain.md`.

## Local-only agent resources

The `.agents/` directory contains local engineering skills and helper material. Keep it local-only
unless the user explicitly authorizes publishing it. Do not let local skills, scratch plans, or
agent runtime files enter project history.

## Definition of done

Before declaring work complete, confirm the linked Issue criteria, targeted and wider checks,
diff scope, secret/private-data safety, schema and documentation updates, provenance, checksums,
claim boundaries, and explicit remaining blockers.
