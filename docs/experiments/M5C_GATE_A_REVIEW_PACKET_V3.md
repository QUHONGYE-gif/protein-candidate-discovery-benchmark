# M5C Gate A review packet v4

## Status and authority

This packet materializes the smallest reviewable M5C Governance v2 contract for
[MIRA-13](https://multica-server.tail8b7421.ts.net/issues/MIRA-13), under parent
[MIRA-10](https://multica-server.tail8b7421.ts.net/issues/MIRA-10). Multica is the sole tracking
authority; no GitHub parent Issue is created.

The candidate manifest remains at the legacy proposal path
`manifests/experiments/m5c/gate-a-proposal-v3/milestone-contract-manifest.json`. Its
`manifest_status: example` and the referenced Gate A record's `record_status: example` mean the
packet is schema-testable but **does not authorize execution**. After technical review and the
confirmations below, the final decision record must be regenerated, bound to fresh authenticated
GitHub comments from both scientific authorities, and only then may the manifest become
`authorized`.

Contract identity:

- ID/version: `m5c-proteingym-prospective-v1` / `4`;
- scientific-contract digest: `b0e2268fe2a89157b18501e7955566599ab6ab2d4b02fc875507b5212e547810`;
- proposal decision digest: `0d47458a4a8f948b2266e7d5535f2b67f5121b7c7efc803981eaadd52c67aee0`;
- Gate proposal SHA-256: `0431033dffffbe904ec1ce77c41fced67bc468e3399038e8d116004ecb7a2e23`;
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
4. **Static proxy baseline.** The frozen proposal names exact ProteinGym column
   `esm1v_t33_650M_UR90S_1`, direction +1, requiring 100% finite coverage with no imputation. The
   v4 preflight found that exact column absent from all four whitelisted raw-score headers and found
   the available related columns co-located with DMS truth columns. No substitute was selected.
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
8. **Input/capability manifest and Run identity.** Before final Gate A and any truth read, an
   outcome-free input/capability manifest must exact-bind the accepted
   45-object/1,758,940,765-byte Issue #11 prefix, server staging, per-assay candidate/proxy/truth
   identities, visibility and consumers, and finite-coverage report; the final contract binds that
   manifest's path/hash/size. Its optional generator identity is not execution identity. After Gate
   A, the final execution commit/tree/environment enters a separate Run identity, reverse-bound to
   the authorized contract and input/capability-manifest digests, before the first truth read or
   protected side effect.
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

## Preflight source handoff and access audit

The item 2 operation authorization began at Multica comment
`01a0714c-aa7b-7b0d-835d-40b703549198` (`2026-09-05T11:20:44Z`) and expires at
`2026-09-07T11:20:44Z`; the source supplement in comment
`01a071fb-dd2c-707a-b07b-cb6d280e5b47` does not reset that window or any cap. The supplement permits
the named operator `github:willzhang` / server user `siyuan` to use the reported server replica at
`129.28.97.247` solely for the same outcome-free preflight:

- data root: `/srv/pcdb/data/imports/proteingym/v1.3/four-assay-unified-v1/`;
- migration receipt:
  `/srv/pcdb/scratch/proteingym-migration-20260905-01/migration-receipt.json`.

The migration party reports 45 objects / 1,758,940,765 bytes, with source-download and independent
post-import size/SHA-256 agreement for 45/45 objects and zero COS write/delete. These are explicitly
**unverified handoff claims**, not Moka observations. Exact per-object source identity, size, SHA-256,
path mapping and coverage remain pending independent read-only verification.

The first COS-prefix List attempt remains a failed `403 AccessDenied` record with zero returned
objects and zero candidate/score/truth object bytes. A subsequent strict SSH attempt as `siyuan`
stopped before authentication with `Host key verification failed`: no password authentication was
attempted, no host key was accepted, no server path/receipt/data was read and no write occurred. The
attempt lacked the later-supplied legacy-address host-key alias and is retained as a failed historical
attempt rather than generalized as proof that no trusted key existed.

The follow-up authorization in Multica comment `01a07236-a0a9-765d-835a-0fa74af25582` supplied the
existing `HostKeyAlias=1.14.72.82` mapping. Strict verification independently recomputed the stored
ED25519 fingerprint as `SHA256:oLhxE/lEXpnkIF5eFE59kTaK7PcaRDZTCwA/2Si2i5k`, matched the authorized
fingerprint, and authenticated by public key as `siyuan` to kernel hostname `VM-0-8-debian` without
password fallback. The authenticated principal is in group `pcdb`. Exact read-only `stat` then found:

- `/srv/pcdb/data/imports`: `2700`, owner `hongye:pcdb`;
- `/srv/pcdb/scratch/proteingym-migration-20260905-01`: `2700`, owner `hongye:pcdb`.

Both block group traversal. The comment permits a one-time minimal administrator repair after
recording those modes, but this runtime has no administrator credential through a secure input
channel and `sudo -n true` reports `a password is required`. No interactive or password-supplied sudo
was attempted. Consequently no mode/ACL was changed, no receipt or data object was read, and the
preflight remains blocked on the administrator repair. This follow-up is recorded append-only at
`manifests/experiments/m5c/preflight-attempts/m5c-input-preflight-access-20260905T153753Z.json`.

This handoff changes only the permitted physical source for the outcome-free preflight. It does not
change the proposed formal execution host `protein-6000` / `ps`, authorize a new migration,
provisioning or formal run, or establish that the server replica is identical to the accepted Issue
#11 prefix. The exact machine-readable access audit is retained at
`manifests/experiments/m5c/preflight-attempts/m5c-input-preflight-access-20260905T143640Z.json` and
the append-only follow-up named above.

The access-restoration comment `01a07286-59c5-7c47-9783-940226988b4b` enabled a final bounded
attempt. Moka independently verified the receipt as 12,282 bytes / SHA-256
`c0a9e9916572eba22054b8bdc1cfb64eee199abe5a8102017dd45a5bf8eb55e8`, readable and not writable,
and observed exactly 24 readable/non-writable candidate/score/manifest whitelist objects. Raw and
processed truth artifacts remained unreadable. Header-only inspection then found in all four raw
score files:

- frozen column `esm1v_t33_650M_UR90S_1`: absent;
- related columns `ESM1v_single` and `ESM1v_ensemble`: present;
- prohibited columns `DMS_score` and `DMS_score_bin`: present in the same physical CSV.

No raw-score data row was read. The authorization's explicit inability-to-separate stop therefore
ended the attempt before finite-value scanning. The migration receipt exact-binds its 45 path/size/
SHA-256 records and matches the accepted Issue #11 total and final terminal identity, but a separately
readable accepted per-object terminal inventory was not available to this principal; full independent
cross-source per-object comparison is consequently also blocked rather than inferred from totals.

The exact blocked artifacts are:

- input/capability manifest: 12,186 bytes / SHA-256
  `3197bf3ce0c864b96528bd0c158862d14611776b846f02bf5cbeb6c43c6b1e7a`;
- finite-coverage report: 3,453 bytes / SHA-256
  `b427737c9d5e6e52e51fe7364f3f0e78a5d202ef84db424a2d2ffc27b16c5266`.

Both live under `manifests/experiments/m5c/preflight-result-v4/` and are exact-bound in manifest
revision 4 as evaluator/reporting inputs. Their status is `blocked`, not a passing final input
manifest. Generator identity is separately recorded as Moka plus remote principal `siyuan`, source
commit/tree `6e18a5d` / `a3478a5`, environment digest
`3d1b633ca515100d493e93f9620ad30221584314635a4285a947febdb22ecd17`; it is not a Run identity.

## Mika REVISE disposition

The initial independent review is recorded in MIRA-13 at comment
`01a07097-73ee-70ee-8088-ae3d517e3545`. Revisions 2 through 4 respond as follows:

1. **Nested bootstrap — revised.** One assay/draw now creates one ten-slot seed index vector and
   reuses it across both models and both nested 32/64 cells. Matched-replicate resampling remains
   inside each sampled state occurrence. Interval interpretation is limited to within-assay
   algorithm/initialization uncertainty.
2. **Cohort taxonomy — revised.** Eligibility exclusion, `VALIDITY_STOP`, five informative labels,
   four inconclusive/unstable labels, directional reversal, informative-label conflict,
   `REPLICATED_*` and the final `INCONCLUSIVE` fallback now have explicit precedence and claim limits.
3. **Machine-protectable comparison/input boundary — revised at contract level.** All existing
   model/acquisition hashes, recall formula, truth-tie rule, state identity and counter tuples are
   exact in the digest. Revision 3 separates the outcome-free pre-Gate input/capability manifest
   from the post-Gate Run identity: the former binds accepted objects, roles, consumers and finite
   coverage; the latter binds final execution source/environment and reverse-binds the authorized
   contract and input/capability-manifest digests before any protected side effect. Revision 4
   applies that boundary fail-closed and binds the resulting blocked artifacts without selecting a
   replacement proxy or reading a raw-score data row.
4. **Operation authorization and reproduction — revised as a proposal.** Exact actors, host/paths,
   validity windows, rollback/stop conditions and disjoint main/reviewer/archive ceilings are now
   normative. Human authorization remains outstanding in the single list below.
5. **CLI wording — revised.** The Gate sequence now distinguishes authoring the JSON from computing
   its decision digest/marker.

Deferred as non-blocking engineering, consistent with Mika's review: runner/schema/golden-vector
implementation, target-host provisioning and generation of the final execution Run identity occur
only after the relevant authorization. The pre-Gate generator may record a separate generator
identity, but it is never described as final run source. The absent Multica linked-PR display is
tracking UI state, not a scientific or contract blocker; PR #103 remains routable by title and
branch. No new assay, seed,
model, beta, round, approval layer, service or subtask is introduced.

## One confirmation list

These are the only unresolved items. The recommendation is part of the proposal, not an
authorization.

1. **Scientific contract and proxy identity.** Both authorities must approve version 4's complete cohort,
   nested design/bootstrap, exact comparison hashes/counters, recall/tie rule, 99.375% intervals,
   assay/cohort taxonomy and one-batch stopping. They must now decide outcome-independently whether
   the intended static baseline is `ESM1v_single`, `ESM1v_ensemble`, or neither; the frozen
   `esm1v_t33_650M_UR90S_1` identifier is absent. No recommendation is made between those scientific
   alternatives because the current authorization forbids outcome/performance use and did not
   authorize a proxy substitution.
2. **Capability-separable proxy input.** The original outcome-free read was authorized and stopped
   correctly before raw-score rows. If item 1 selects a present column, authorize a minimal method
   to create and independently verify an immutable candidate-id/selected-proxy-only artifact without
   exposing `DMS_score` or `DMS_score_bin` to Moka, the runner, surrogate or acquisition. It must have
   its own exact path/SHA-256/size, consumer boundary and fresh finite/alignment report. This is a
   necessary replacement for the failed preflight input, not permission to analyze proxy performance
   or a new scientific approval stage.
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

1. Mika reviews this exact revision, the two blocked preflight artifacts and the proxy/capability
   findings, and records whether the stop is correct and which changes are truly necessary; optional
   engineering improvements remain deferred.
2. The two authorities resolve the single confirmation list. Items 1 and 2 now require an explicit
   proxy identity choice and a bounded capability-separable artifact operation. A replacement
   outcome-free preflight must exact-bind a passing input/capability manifest by path, SHA-256 and
   size; any generator source/environment recorded for provenance remains a generator identity, not
   the execution Run identity. Necessary changes receive findings-focused recheck without creating
   another approval stage.
3. Author the final Gate A JSON, then compute its exact decision marker with
   `pcdb governance-decision-digest`.
4. Both `github:QUHONGYE-gif` and `github:willzhang` post fresh authenticated comments containing
   the exact marker and explicit approval of all four major boundaries. GitHub review or CI alone is
   insufficient.
5. Bind both attestations and the input/capability-manifest digest, change the manifest to
   `authorized`, run the final production contract check, and update `docs/STATUS.md` for the Gate A
   scientific transition.
6. Only after valid Gate A may the parent barrier promote Stage 2. Stage 2 must create the final Run
   identity from the actual execution commit/tree/environment and reverse-bind the authorized
   contract and input/capability-manifest digests before its first truth read or protected side
   effect.

No formal experiment, held-out access, label reveal, COS write, server provisioning, M5D/M6 start or
evidence promotion is performed by this packet.
