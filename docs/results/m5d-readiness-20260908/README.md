# M5D SKEMPI server readiness — 2026-09-08

Authority: [Issue #104](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/104).
Protocol: [M5D readiness contract](../../experiments/M5D_SERVER_READINESS.md).
Sole server: `129.28.97.247` / `VM-0-8-debian`.

The exact accepted two-object SKEMPI raw bundle is now present on the server.
Two independent processing builds at `00c5791db99cd46d0387db82dd3bfd7a1f526e24`
produced identical bytes for all nine outputs. A separate process re-read both
builds and checked output digests and manifest cross-links. This is data readiness,
not a frozen external-validation cohort, scientific result or Gate decision.

## Observed readiness

| Item | Result |
|---|---:|
| Accepted raw files / bytes | 2 / 32,084,298 |
| Source records | 7,085 |
| Prepared candidates | 3,811 |
| Merged duplicate source rows | 393 |
| Excluded source rows | 2,881 |
| Complex pools | 245 |
| Conservative leakage components | 56 |
| Pools with at least 48 candidates / components | 14 / 7 |
| Pools with at least 80 candidates / components | 9 / 4 |
| A/B combined wall time | 7.122 seconds |
| Peak child RSS | 124,850,176 bytes |
| Attempt scratch before terminal write | 6,643,745 bytes |
| Model fitting / high-value computations / COS writes | 0 / 0 / 0 |

Source accounting is exact: `3811 + 393 + 2881 = 7085`. Exclusions comprise 2,241
missing/assumed-temperature rows, 295 censored/ambiguous affinities, 202 rows in
multiple-temperature candidates, 141 missing/mismatched/ambiguous publisher residue
mappings, and 2 invalid mutation-chain/duplicate-site/no-op rows. These rules are
identity/semantics rules, never selected by acquisition performance.

`external-candidate` is the only partition and `held_out_partitions=[]`. The grouping
code rejects any later assignment that separates a component, but this run did not
allocate train/test or evaluate held-out labels. Labels were normalized privately by
the processing component; they did not enter feature generation or grouping. No
target distribution, candidate identity, mutation table or feature matrix is published.

## Server locations and identities

- Raw: `/srv/pcdb/data/raw/skempi/v2.0/2018-06/source-bundle/`.
- Successful attempt: `/srv/pcdb/runs/M5D/readiness-20260908-003/`.
- Active derived root:
  `/srv/pcdb/data/processed/skempi/skempi-readiness-72bc80dd1524f7815039160bc095045308f4d56c9e819a84203a6fe70e508ec6/`.
- Processing contract SHA-256:
  `025ca8555a63e1422f7a5a690ff46804eee6fbeb98eb9bbafd04525fb6939540`.
- Processing Manifest SHA-256:
  `4a9f491fa71b3630466de094e19f5e7b51765ff75e7ed10a345eee6cbcb2efdd`.
- Split Manifest SHA-256:
  `b11beb72e951878d42f247478ad8e5bd0bca25a8eeb3fdb71350bd44171f7e72`.
- Successful terminal SHA-256:
  `ea6ffdf1a83b8b0634b51ac0c098951800ba5def1c4ffc08bab36a5ebffc653c`.

The active derived copy independently matches the successful build, and each file
is owner-readable only under a private directory. Original attempts remain intact.
The copy receipt is outside the sealed attempt in the recovery scratch directory.

## Preserved attempt history

| Attempt | Outcome |
|---|---|
| `m5d-raw-recovery-20260908-001` | CSV exact; archive transfer hit the 300-second recovery cap; partial preserved |
| `m5d-cos-recovery-20260908-002` | Narrow legacy COS read returned 403; zero COS writes |
| `m5d-relay-recovery-20260908-003` | Frozen official archive downloaded via temporary local relay; exact server readback |
| `readiness-20260908-001` at `2d754f8` | Stopped on publisher nonstandard residue-name syntax |
| `readiness-20260908-002` at `f349d95` | Stopped on ambiguous original-to-cleaned residue mappings |
| `readiness-20260908-003` at `00c5791` | Complete A/B byte-identical readiness terminal; first successful readiness result |

The repairs recognize publisher noncanonical names without admitting them as standard
amino acids and exclude every candidate touching ambiguous mapping sites. They do not
choose a favorable mapping, repair structures or inspect strategy effects.

## Reproduction and next decision

On the sole server, from a clean checkout of the recorded execution commit, with an
unused attempt ID:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 scripts/run_skempi_readiness.py \
  --attempt-id readiness-new-id \
  --git-commit 00c5791db99cd46d0387db82dd3bfd7a1f526e24
```

This is a reproducibility command, not an instruction to rerun the successful result.
The entry point enforces the host, clean Git identity, accepted raw hashes, one worker,
1.5 GiB address-space ceiling, 20-minute wall limit and 1 GiB output cap.

Decision: **continue to external-contract review; no strategy result is evaluated**.
Do not treat the 14 complexes as 14 independent biological replicates: they occupy
7 conservative components, and the 64-initial subset occupies only 4. The two initial
sizes have different feasible universes; a future protocol must prospectively choose
an aligned common cohort or report separate, explicitly noncomparable cohorts.

Three validity limits remain: name/publisher-link grouping is not a complete independent
sequence-homology audit; the strict temperature policy limits coverage; and the number
of independent feasible components is small. No M5C completion, M5D formal evaluation,
production backup/RPO acceptance or scientific evidence promotion is claimed.
