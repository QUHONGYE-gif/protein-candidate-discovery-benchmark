# ProteinGym v1.3 four-assay data readiness

## Scope

Issue #11 freezes exactly four substitution assays:

- `BLAT_ECOLX_Firnberg_2014`;
- `CAPSD_AAV2S_Sinai_2021`;
- `GFP_AEQVI_Sarkisyan_2016`;
- `SPG1_STRSG_Olson_2014`.

The implementation acquires only each assay's DMS truth CSV and same-name zero-shot score CSV from
the two official v1.3 ZIP archives. It must not download, retain or publish either complete archive or
any non-selected assay.

The versioned contract is
`configs/datasets/proteingym/v1.3/four-assay-unified-v1.json`. It freezes archive HTTP identity,
selected-member offsets/sizes/CRC-32, BLAT's expected 4,783 truth rows, provenance DOI, license
boundary and hard caps.

## Bounded local bundle and server continuation

After the implementation commit is merged and one separate execution authorization is recorded, the
operator first runs the local Range acquisition in a clean exact worktree:

```text
pcdb proteingym-local-bundle \
  --config configs/datasets/proteingym/v1.3/four-assay-unified-v1.json \
  --reviewed-git-commit <exact-merged-commit> \
  --approval "APPROVED FOR ISSUE #11 PROTEINGYM LOCAL BUNDLE CONTINUATION" \
  --approval-comment-id <positive-comment-id> \
  --original-approval-comment-id 5352364303 \
  --repository-root <clean-exact-worktree> \
  --scratch-root <new-empty-private-local-directory>
```

It transfers that private directory to the approved server without changing any byte, then runs:

```text
pcdb proteingym-continue \
  --config configs/datasets/proteingym/v1.3/four-assay-unified-v1.json \
  --reviewed-git-commit <exact-merged-commit> \
  --original-reviewed-git-commit cfad30502bf130adad2d4bd019f9ba207d7df841 \
  --approval "APPROVED FOR ISSUE #11 PROTEINGYM LOCAL BUNDLE CONTINUATION" \
  --approval-comment-id <same-positive-comment-id> \
  --bundle-approval-comment-id <local-bundle-comment-id> \
  --original-approval-comment-id 5352364303 \
  --cos-destination cos://<approved-bucket> \
  --repository-root <clean-exact-worktree> \
  --bundle-root <transferred-private-bundle-directory> \
  --scratch-root <new-empty-server-directory>
```

These commands are documentation, not current execution authorization. Credentials and the exact COS
destination remain outside Git. One new authorization/comment ID binds both stages. Completing the
local bundle does not consume its first server start. On that first start, `--approval-comment-id` and
`--bundle-approval-comment-id` are equal. Any replacement after a formal server start uses a new
`--approval-comment-id`, retains the original bundle ID, and records every prior server-start ID with
`--prior-continuation-approval-comment-id`; it does not reacquire or reseal the bundle.

The older approval string inside the frozen JSON config is retained only to preserve the already
published BLAT config/manifests' byte identity. It does not authorize v3 execution; both commands above
require the new exact continuation approval string independently.

The local command acquires all eight members and writes `local-acquisition-bundle.json` last. The
server command recomputes the full CAPSD/GFP/SPG1 output inventory before its first PUT, fresh-lists the
exact prefix, and fresh-reads every existing expected object. BLAT's complete 11-object chain must match
the local source bytes, its prior manifest chain and `main@cfad305`; it is reused without a PUT.

## Completed execution evidence

Issue #11 completed its authorized formal continuation on 2026-08-20:

- reviewed implementation `45f058d9aabb4745853451989f875850d453cb97` / tree
  `0e9dd0e802e5685f66d309bf8c9f50f8a3dbea23`;
- attempt `proteingym-server-continuation-5357037808` used the exact eight-member bundle; local
  source network was `487,415,441 / 550,000,000 B`, and server source network was zero;
- BLAT (4,783 rows / 11 objects) was fresh-read validated and reused without PUT; CAPSD (42,328
  rows), GFP (51,714 rows) and SPG1 (536,962 rows) were newly published and fresh-read validated;
- the final exact prefix contains 45 unique objects / `1,758,940,765 B`; automatic retries,
  full-archive acquisition, held-out access, successful overwrite and DeleteObject were all
  zero/false;
- `unified-attempt.json` was published last and fresh-read exact: 33,951 bytes, SHA-256
  `5dad6220ae74a86a052a45386fd0540995f3e8e5abd7fea0274eb69441164ba1`.

The [completion record](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11#issuecomment-5357846394),
[independent post-run review](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11#issuecomment-5357910563)
and [closure review](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11#issuecomment-5358532472)
bind the terminal evidence and its limits. The commands above remain reproducibility/traceability
documentation; they do not authorize another execution or access to credentials, COS, held-out truth
or later experimental stages.

## Fail-closed acquisition

For each selected member the runtime:

1. makes one exact Range request for the fixed ZIP local header;
2. validates signature, flags, compression method, frozen sizes/CRC and safe member basename;
3. requests only the variable local-header bytes and compressed payload;
4. requires HTTP 206 plus exact `Content-Range`, `Content-Length`, ETag and Last-Modified;
5. streams raw-DEFLATE extraction while computing CRC-32, SHA-256 and exact size;
6. validates CSV readability and row count;
7. uses no automatic retry and has no full-archive fallback.

The 550,000,000-byte source-network cap applies to the v3 local acquisition only. The consumed direct-
server attempt is recorded separately and is not included in a claim that this v3 cap covers complete
historical traffic. Each new scratch root is capped at 2.5 GiB, and the completed exact COS prefix is
capped at 2.0 GB decimal.

## Artifact separation

Each assay keeps a separate task identity and publishes:

- raw truth CSV and raw zero-shot score CSV as distinct immutable objects;
- Dataset Manifest v1 and DatasetIngestAttempt v1;
- deterministic gzip `candidates.csv.gz` containing only `candidate_id`, `mutant` and
  `mutated_sequence`;
- deterministic gzip `hidden-truth.csv.gz` containing only `candidate_id` and `DMS_score`;
- compact processing audit and per-column zero-shot coverage/missingness audit;
- Processing Manifest v1;
- deterministic gzip membership assigning every candidate to `candidate-pool`;
- Split Manifest v1 with no held-out partition.

The score audit performs no imputation and approves no zero-shot model. Score rows must map to truth
by mutant; if `mutated_sequence` is present it must also match. Raw score values remain in their
separate immutable source artifact and are not copied into candidate features.

Publication is payload-first and manifest/evidence-last. Every object uses COSCLI
`--forbid-overwrite --err-retry-num 0`, receives a fresh exact SHA-256/size readback, and each assay
records a duplicate-write rejection probe. Temporary readback files are removed after verification;
published success objects are never overwritten or deleted.

For every expected non-final object, the continuation chooses exactly one action before its first PUT:

- absent: publish once with forbid-overwrite and fresh-read it;
- present with exact prepared SHA-256 and size: record `reuse` and do not PUT it;
- present with any mismatch, out-of-order partial state, or outside-inventory key: fail closed.

This rule applies to CAPSD, GFP and SPG1 as well as BLAT, so an interrupted continuation cannot lock the
prefix merely because an exact successful payload already exists. `unified-attempt.json` remains the
only final object and is published last after a second exact-prefix inventory check.

## Boundaries

The implementation and CI establish an executable data-readiness route. The authorized formal
continuation recorded above establishes server-execution and four-assay data-readiness evidence.
Neither engineering nor execution evidence establishes a benchmark result or scientific Gate.

The runtime does not:

- approve a zero-shot model or cost ratio;
- freeze an experiment train/validation/held-out split or initial observations;
- access held-out truth, run replay, train a model or compute high-value results;
- publicly redistribute raw/extracted CSV bytes;
- claim ProteinGym relicensed upstream assay data.

Any M5C experiment must separately freeze proxy identity, coverage eligibility, experimental
split/leakage, sampling, metrics, compute caps and held-out access before results are read.
