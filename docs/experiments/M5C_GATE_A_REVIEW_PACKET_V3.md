# M5C Gate A review packet v6

## Status and authority

This packet materializes the smallest reviewable M5C Governance v2 contract for
[MIRA-13](https://multica-server.tail8b7421.ts.net/issues/MIRA-13), under parent
[MIRA-10](https://multica-server.tail8b7421.ts.net/issues/MIRA-10). Multica is the sole tracking
authority; no GitHub parent Issue is created.

The candidate manifest remains at the legacy proposal path
`manifests/experiments/m5c/gate-a-proposal-v3/milestone-contract-manifest.json`. Its
`manifest_status: example` and the final decision carrier's pending placeholder attestations mean
the packet is schema-testable but **does not authorize execution**. Mika passed revision 6 at exact
HEAD `1542008c043cc5bbdc1fc9e30ca9188f1b0f29fa`; manifest revision 7 now contains the stable final
decision payload and marker. Both scientific authorities must still publish fresh authenticated
GitHub comments for that marker. Only after their exact envelopes replace the placeholders and the
production contract Check passes may the manifest become `authorized`.

Contract identity:

- ID/version: `m5c-proteingym-prospective-v1` / `6`;
- scientific-contract digest: `abc16ed38664d63efede535de3ecd8b8552031e52b156c37023e7b7e717d5278`;
- final decision digest: `2d7b6e869a56e364383e3f84ba4496a95e239a7766743af24cb3e3f8a1d7a7f0`;
- current final-decision carrier SHA-256: `8213cb52e512340a4d70880ee4be1637fcb34187bbed6eb5d039449ce79a8361`
  (pending envelopes; its file SHA-256 will change when the authenticated transport evidence is
  inserted, while the decision digest remains stable);
- base: `origin/main@9f332c2935b53ab1697688d0024944bc12a99161`;
- PR class: `Major / semantic PR`;
- authorities: `github:QUHONGYE-gif`, `github:willzhang`.

## Revision 6 result and findings-focused PASS

Both authorities published native GitHub comments against exact PR HEAD `77ccde1c` before the
authorized deadline:

- `github:willzhang`: [comment 5566509712](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/103#issuecomment-5566509712), exact body SHA-256
  `3ee865f6aea498b99ede0a49e29c44e7e3720864019894cb9f4e80a81ff8f6ef`;
- `github:QUHONGYE-gif`: [comment 5566572994](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/103#issuecomment-5566572994), exact body SHA-256
  `fd0875072d3e089c54a5e09f47064c7dc2ac37573939cc381a59a6758df115fd`.

They ratify the outcome-independent `ESM1v_single` / direction `+1` identity, authenticate the
migration receipt's 45 path/size/SHA-256 tuples as the accepted Issue #11 terminal inventory,
authorize one zero-retry summary-only continuation within a specifically attested remaining cap,
and conditionally approve the proposed main, aggregate-only reproduction and terminal archive
operations. Both comments explicitly remain pre-final decisions, not Gate A attestations.

Moka then ran one bounded attempt as ordinary server user `siyuan` on `129.28.97.247`. It read only
the migration receipt, four `score-coverage.json` files and four paired processing manifests. The
receipt remained exact at 12,282 bytes / SHA-256 `c0a9e9916572eba22054b8bdc1cfb64eee199abe5a8102017dd45a5bf8eb55e8`.
All four summary/manifest pairs matched the authenticated receipt tuples; `ESM1v_single` was present
and finite for 4,783 / 42,328 / 51,714 / 536,962 candidates, with each finite count equal to both
truth-candidate and score-row counts and all three missing/non-finite/alignment counters zero. No
raw-score data row or truth file was opened; server/COS writes, performance analysis and automatic
retries were zero.

The attempt read 91,898 content bytes total. Its conservative operation bounds were 180 wall
seconds, 10 CPU seconds, 256 MiB peak RSS, 1 MiB scratch, 1 MiB combined read/network and 0 CNY,
inside the specifically attested residual and the unchanged original cumulative bucket. The exact
passing replacements are:

- input/capability manifest: 15,234 bytes / SHA-256
  `130d9f3d9086274c7209158cdcbb3a4054ff3742e3711220542adecae14828fc`;
- finite-coverage report: 3,649 bytes / SHA-256
  `2d37b203515fd4009c067d6be81e6ff80c878560ee4f1bbe9dfc815fd31e6f0a`.

The v4 blocked artifacts remain immutable historical failure evidence. Revision 6 replaces their
normative role with the passing v5 pair while retaining both histories. It does not authorize a
formal run. Mika's findings-focused review passed exact HEAD `1542008c043cc5bbdc1fc9e30ca9188f1b0f29fa`,
tree `04d7034fe9d694a9336e309ee778aeb5bec252df`, scientific-contract digest
`abc16ed38664d63efede535de3ecd8b8552031e52b156c37023e7b7e717d5278`, with no remaining necessary
finding. Manifest revision 7 therefore changes only the Gate finalization carrier: it fixes the
stable final decision payload/marker and keeps the manifest fail-closed pending two human
attestations and a production contract Check.

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
4. **Static proxy baseline.** The proposed archive column is `ESM1v_single`, direction +1. At the
   frozen ProteinGym upstream commit, the official config maps that export alias to
   `input_score_name = esm1v_t33_650M_UR90S_1`, while the official constants distinguish it from
   the five-model `ESM1v_ensemble`. This outcome-independent identity mapping still requires both
   authorities' ratification; it requires 100% finite coverage with no imputation and never enters
   the RF/XGBoost one-step arms.
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
revision 5 as evaluator/reporting inputs. Their status is `blocked`, not a passing final input
manifest. Generator identity is separately recorded as Moka plus remote principal `siyuan`, source
commit/tree `6e18a5d` / `a3478a5`, environment digest
`3d1b633ca515100d493e93f9620ad30221584314635a4285a947febdb22ecd17`; it is not a Run identity.

Mika's exact-head review in MIRA-13 comment `01a074c0-6e91-7493-892c-45e6c843dc61` passes the
header-only stop and both artifacts **as failure evidence**. It requires only two proposal
corrections: use the official outcome-independent single-seed alias mapping as the recommendation,
and replace the assumed derived-proxy prerequisite with a summary-only finite/alignment path plus
an independent accepted-inventory binding. It does not authorize either operation or Gate A.

## Mika REVISE disposition

The initial independent review is recorded in MIRA-13 at comment
`01a07097-73ee-70ee-8088-ae3d517e3545`. Revisions 2 through 5 respond as follows:

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
   replacement proxy or reading a raw-score data row. Revision 5 then records the official
   `ESM1v_single` export-alias mapping and the smaller summary-only validation route without treating
   either as already ratified or authorized.
4. **Operation authorization and reproduction — revised as a proposal.** Exact actors, host/paths,
   validity windows, rollback/stop conditions and disjoint main/reviewer/archive ceilings are now
   normative. Human authorization remains outstanding in the single list below.
5. **CLI wording — revised.** The Gate sequence now distinguishes authoring the JSON from computing
   its decision digest/marker.

Deferred as non-blocking engineering, consistent with Mika's review: runner/schema/golden-vector
implementation, target-host provisioning and generation of the final execution Run identity occur
only after the relevant authorization. A derived candidate-id/proxy-only artifact is not a Gate A
prerequisite while the existing summary and future static-evaluator capability boundaries suffice;
if Stage 2 cannot enforce the latter boundary, it must fail closed and add that artifact as an
engineering repair. The pre-Gate generator may record a separate generator identity, but it is never
described as final run source. The absent Multica linked-PR display is tracking UI state, not a
scientific or contract blocker; PR #103 remains routable by title and branch. No new assay, seed,
model, beta, round, approval layer, service or subtask is introduced.

## Five-item decision disposition

The original five-item list is fully resolved in authenticated GitHub comments 5566509712 and
5566572994; no parameter is inferred from the user's Multica message.

1. **Scientific contract and proxy identity — resolved.** Both authorities ratify
   `ESM1v_single` / direction `+1` as the archive alias for the selected single-seed baseline at
   upstream commit `144fe22b`; `ESM1v_ensemble` remains distinct. They do not use proxy performance
   as rationale, and a departure from single-seed remains a semantic revision.
2. **Summary-only finite/alignment and source identity — resolved and completed.** Willzhang
   authenticates the 12,282-byte receipt digest as the canonical tuple binding to the accepted
   Issue #11 inventory; QUHONGYE-gif accepts that binding. Both bound one zero-retry operation to the
   eight summary/manifest inputs, no raw-score row/truth access, an attested residual of at least
   600 wall seconds / 600 CPU seconds / 256 MiB RSS / 1 MiB scratch / 1 MiB read-network / 0 CNY,
   and the unchanged `2026-09-07T11:20:44Z` deadline. The operation passed and ended at its first
   complete result.
3. **Provision, stage and run — conditionally resolved.** Both approve the named actors, paths,
   rollback/stop policy and main cumulative caps of 24 h wall, 384 CPU h, 48 GB RSS, 30 GiB scratch,
   4 GiB network and 100 CNY across at most two attempts. Permission activates only after valid
   final Gate A and every namespace/ACL/backup/RPO/source/environment/input/capacity check passes.
4. **Mika reproduction — conditionally resolved.** Both approve one post-main aggregate-only run on
   the same host with 1 h wall, 2 CPU h, 4 GB RSS, 2 GiB scratch, 1 GiB network and 5 CNY. It cannot
   access candidate-level truth or perform model/acquisition work or produce a second result.
5. **Terminal archive — conditionally resolved.** Both approve `service:pcdb-archiver` only after a
   valid Gate B, with at most 128 non-marker objects, 1 GiB archive/network, 2 h wall, 4 CPU h,
   4 GB RSS, 2 GiB scratch and 10 CNY; overwrite/delete remain forbidden and `_SUCCESS` is last.

The monetary ceiling is therefore 120 CNY across disjoint preflight/main/reviewer/archive buckets.
Everything else remains withheld: M5D/M6, pool-external truth, multi-round, new paid resources,
raw-data publication, candidate-level reviewer access and evidence promotion before valid Gate B.

## Gate sequence

1. Mika's findings-focused revision 6 review is complete and PASS; the passing input/capability
   manifest remains a pre-Gate generator artifact, not a final execution Run identity.
2. The final Gate A payload has been authored and its stable marker computed with
   `pcdb governance-decision-digest`; placeholder envelopes remain explicitly non-authorizing.
3. Both `github:QUHONGYE-gif` and `github:willzhang` post fresh authenticated comments containing
   the exact marker and explicit approval of all four major boundaries. GitHub review or CI alone is
   insufficient.
4. Bind both attestations and the input/capability-manifest digest, change the manifest to
   `authorized`, run the final production contract check, and update `docs/STATUS.md` for the Gate A
   scientific transition.
5. Only after valid Gate A may the parent barrier promote Stage 2. Stage 2 must create the final Run
   identity from the actual execution commit/tree/environment and reverse-bind the authorized
   contract and input/capability-manifest digests before its first truth read or protected side
   effect.

No formal experiment, held-out access, label reveal, COS write, server provisioning, M5D/M6 start or
evidence promotion is performed by this packet.
