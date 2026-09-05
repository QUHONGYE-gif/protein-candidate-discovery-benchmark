# M5C Gate A review packet v2

## Status and authority

This packet materializes the smallest reviewable M5C Governance v2 contract for
[MIRA-13](https://multica-server.tail8b7421.ts.net/issues/MIRA-13), under parent
[MIRA-10](https://multica-server.tail8b7421.ts.net/issues/MIRA-10). Multica is the sole tracking
authority; no GitHub parent Issue is created.

The candidate manifest is
`manifests/experiments/m5c/gate-a-proposal-v2/milestone-contract-manifest.json`. Its
`manifest_status: example` and the referenced Gate A record's `record_status: example` mean the
packet is schema-testable but **does not authorize execution**. After technical review and the
confirmations below, the final decision record must be regenerated, bound to fresh authenticated
GitHub comments from both scientific authorities, and only then may the manifest become
`authorized`.

Contract identity:

- ID/version: `m5c-proteingym-prospective-v1` / `2`;
- scientific-contract digest: `a2c39412000d26f80c025a37f7553ccfb918b4652ff940f30b76f533ee994014`;
- proposal decision digest: `2676ca4824d8122790276e441919477a092c51d3a96c02d884c0f23ab5503008`;
- base: `origin/main@9f332c2935b53ab1697688d0024944bc12a99161`;
- PR class: `Major / semantic PR`;
- authorities: `github:QUHONGYE-gif`, `github:willzhang`.

## Existing decisions used directly

- The revised M4–M7 plan fixes the complete four-assay cohort, result-independent eligibility,
  assay-level inference, single-step G/E/Matched confirmation and the prohibition on treating seeds
  as independent assays: `docs/M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`.
- Issue #11 fixes the cohort purpose and exclusion boundary in
  [comment 5336871661](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11#issuecomment-5336871661),
  and its completed immutable readiness identity is independently accepted in
  [comment 5357910563](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11#issuecomment-5357910563).
- M5B's final scientific review fixes the one-step contrast meanings, preserves `INCONCLUSIVE`,
  prohibits a behavioral-redundancy claim and defers aggregate taxonomy inputs to M5C:
  [comment 5509182540](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/91#issuecomment-5509182540).
- Governance v2 and ADR-0005 make M5C server-first, append-only and zero-COS-write during ordinary
  runs; Gate A must bind all four major boundaries and both configured authorities.

## Frozen proposal

The candidate manifest is normative for review; this section is a navigation summary.

1. **Question and cohort.** Test whether the frozen same-state one-step intervention has immediate
   discovery value relative to Greedy and overlap-matched Random, and whether mechanism labels
   replicate, reverse or differ across all four independent assays. M5B is motivation only.
2. **Data and eligibility.** Bind ProteinGym v1.3 readiness config
   `ee631056…` and unified terminal `5dad6220…`. Exclusion is permitted only before any outcome read
   for a retained failure of the six frozen readiness/leakage checks.
3. **Split and features.** Each assay's complete candidate-pool is its independent replay universe.
   Initial 32/64 observations are nested truth-blind SHA-256 samples for seeds 1729–1738. RF and
   XGBoost use only stateless `sequence-composition-420-v1`; there is no cross-assay fitting.
4. **Static proxy baseline.** Proposed proxy is the exact ProteinGym column
   `esm1v_t33_650M_UR90S_1`, direction +1, requiring 100% finite coverage with no imputation. It is
   evaluated separately and never enters the RF/XGBoost one-step arms.
5. **One-step arms.** Exact-bind RF `418b0fd…`, XGBoost `31eb5bc…`, Greedy `3c7c537…`,
   Uncertainty `7748917…`, UCB `9b132ec…`, and the full M5C Matched Random state/counter tuples.
   Every selected batch has size 16; each model/state has 64 matched controls.
6. **Primary endpoint.** Branch recall is exactly `|selected_batch ∩ H_0.01| / |H_0.01|`;
   primary contrasts are E-G and E-M. Truth-boundary ties expand `H` and are never split by
   candidate ID; `delta=1/K_nominal`. Upper 0.5%/2% remain sensitivity only.
7. **Uncertainty and classification.** Forty paired states per assay, one seed-slot resample shared
   by all two-model × two-nested-initial-size cells in every draw, nested matched-replicate resampling,
   20,000 draws and 99.375% type-7 intervals. The contract enumerates eligibility, validity,
   informative and inconclusive labels plus directional-reversal/heterogeneity precedence.
   `REPLICATED_*` means at least two non-conflicting M5C assays, never all four by implication and
   never M5B replication. No pooled-seed inference is allowed.
8. **Input capability manifest.** Before final Gate A and any truth read, an outcome-free exact
   Run-pinned manifest must bind the accepted 45-object/1,758,940,765-byte Issue #11 prefix, server
   staging, source/environment, and per-assay candidate/proxy/truth visibility and consumers. The
   selected proxy requires a finite-coverage-only readback with no performance access.
9. **Auditability.** Aggregate output must expose every non-sensitive classifier input requested by
   the M5B review: counts, 40 divergence counts, cell estimates, interval/status, sensitivity inputs,
   validity flags, cohort precedence/reason and attempt selection. Candidate identities and truth
   remain private.
10. **Execution and operations.** One sealed slice contains the four-assay M2-static baseline and
    one-step comparison; no multi-round run is covered. The proposal now names the provisioning/
    staging operator, service principals, exact server paths, time windows, stop/rollback rules,
    main/reviewer/archive caps and immutable terminal prefix.

## Outcome-free server observation

Read-only observation on 2026-09-05 found:

- `protein-6000` resolves to an authenticated host whose kernel hostname is `ps`;
- 192 logical CPUs; 263,713,240 kB RAM total; 203,107,364 kB RAM available;
- 122,686,394,368 bytes currently free on the candidate filesystem;
- `/srv/pcdb` and Unix group `pcdb` are absent;
- `protein-6001` was not accessible with the available key.

Therefore no target is currently Governance v2-ready. No directory, group, account, data, process,
credential or infrastructure was created or changed during this check.

## Mika REVISE disposition

The initial independent review is recorded in MIRA-13 at comment
`01a07097-73ee-70ee-8088-ae3d517e3545`. Revision 2 responds as follows:

1. **Nested bootstrap — revised.** One assay/draw now creates one ten-slot seed index vector and
   reuses it across both models and both nested 32/64 cells. Matched-replicate resampling remains
   inside each sampled state occurrence. Interval interpretation is limited to within-assay
   algorithm/initialization uncertainty.
2. **Cohort taxonomy — revised.** Eligibility exclusion, `VALIDITY_STOP`, five informative labels,
   four inconclusive/unstable labels, directional reversal, informative-label conflict,
   `REPLICATED_*` and the final `INCONCLUSIVE` fallback now have explicit precedence and claim limits.
3. **Machine-protectable comparison/input boundary — revised at contract level.** All existing
   model/acquisition hashes, recall formula, truth-tie rule, state identity and counter tuples are
   exact in the digest. A per-assay Run-pinned capability manifest and finite proxy-coverage readback
   are mandatory before final Gate A/truth access.
4. **Operation authorization and reproduction — revised as a proposal.** Exact actors, host/paths,
   validity windows, rollback/stop conditions and disjoint main/reviewer/archive ceilings are now
   normative. Human authorization remains outstanding in the single list below.
5. **CLI wording — revised.** The Gate sequence now distinguishes authoring the JSON from computing
   its decision digest/marker.

Deferred as non-blocking engineering, consistent with Mika's review: runner/schema/golden-vector
implementation, target-host provisioning and generation of the final Run-pinned artifact occur only
after the relevant authorization. The absent Multica linked-PR display is tracking UI state, not a
scientific or contract blocker; PR #103 remains routable by title and branch. No new assay, seed,
model, beta, round, approval layer, service or subtask is introduced.

## One confirmation list

These are the only unresolved items. The recommendation is part of the proposal, not an
authorization.

1. **Scientific contract and proxy risk.** Both authorities must approve version 2's complete cohort,
   nested design/bootstrap, exact comparison hashes/counters, recall/tie rule, 99.375% intervals,
   assay/cohort taxonomy and one-batch stopping; and explicitly accept the disclosed proxy drafting
   exposure while keeping `esm1v_t33_650M_UR90S_1` descriptive-only, +1, 100%-finite/no-imputation
   and unable to affect eligibility, method or taxonomy. Recommendation: approve; this is the
   smallest outcome-independent continuation of M5B and does not treat M5B as replicated evidence.
2. **Outcome-free pre-Gate read.** Authorize `github:willzhang`/server user `siyuan` once to read only
   the accepted candidate/score identities needed to create the Run-pinned capability manifest and
   finite-coverage report, with read-only source/COS/server data planes, zero DMS truth/performance
   read, a 48-hour window, 1 h wall, 2 CPU h, 4 GB RSS, 2 GiB scratch/read/network and 5 CNY caps.
   Only the generated small manifest/report may be committed to the review branch. Any inability to
   separate score from truth stops the read. Recommendation: approve so the exact manifest/hash can
   be added before final Gate A; this limited permission is an operation authorization, not an extra
   scientific Gate.
3. **Provision, stage and run.** Authorize the proposed subject/path/window/rollback policy:
   `github:willzhang`/`siyuan` provisions `protein-6000`/`ps` and byte-stages exactly the accepted
   45-object prefix; `service:pcdb-runner` owns one formal slice under `/srv/pcdb/runs/M5C/`.
   Confirm the main cumulative caps: 24 h wall, 384 CPU h, 48 GB RSS, 30 GiB scratch, 4 GiB network,
   100 CNY across at most two allowed attempts. Recommendation: approve only conditional on every
   namespace/ACL/backup/RPO/source/environment/input/expected-peak check passing and the 50% target/
   70% hard-cap rule; otherwise stop without cap expansion.
4. **Mika reproduction.** Authorize one capability-limited aggregate-only run on the same host after
   the first valid main terminal, exact-bound to clean source/environment and a sealed aggregate
   input, with 1 h wall, 2 CPU h, 4 GB RSS, 2 GiB scratch, 1 GiB network and 5 CNY. Recommendation:
   approve; it may reproduce only hashes, intervals and taxonomy/roll-up, never candidate-level truth,
   model/acquisition work or a second scientific result.
5. **Terminal archive.** Authorize `service:pcdb-archiver` only after valid Gate B to publish at the
   new digest-keyed M5C prefix: at most 128 non-marker objects, 1 GiB archive/network, 2 h wall,
   4 CPU h, 4 GB RSS, 2 GiB scratch and 10 CNY; no overwrite/delete, exact fresh readback and
   `_SUCCESS` last. Recommendation: approve now as the same milestone operation boundary. If it is
   withheld, archive remains unauthorized and Stage 3 cannot execute until a bounded operation
   authorization is recorded; that would not be a new scientific Gate or a new project stage.

The monetary ceiling is therefore 120 CNY across disjoint preflight/main/reviewer/archive buckets.
Everything else remains withheld: M5D/M6, pool-external truth, multi-round, new paid resources,
raw-data publication, candidate-level reviewer access and evidence promotion before valid Gate B.

## Gate sequence

1. Mika re-reviews this exact revision against the five recorded findings and records a verdict in
   MIRA-13; optional engineering improvements remain deferred.
2. The two authorities resolve the single confirmation list. If item 2 is authorized, Moka performs
   only that outcome-free preflight and exact-binds its artifacts; any necessary change receives
   findings-focused recheck without creating another approval stage.
3. Author the final Gate A JSON, then compute its exact decision marker with
   `pcdb governance-decision-digest`.
4. Both `github:QUHONGYE-gif` and `github:willzhang` post fresh authenticated comments containing
   the exact marker and explicit approval of all four major boundaries. GitHub review or CI alone is
   insufficient.
5. Bind both attestations, change the manifest to `authorized`, run the final production contract
   check, and update `docs/STATUS.md` for the Gate A scientific transition. Only then may Stage 2 be
   promoted.

No formal experiment, held-out access, label reveal, COS write, server provisioning, M5D/M6 start or
evidence promotion is performed by this packet.
