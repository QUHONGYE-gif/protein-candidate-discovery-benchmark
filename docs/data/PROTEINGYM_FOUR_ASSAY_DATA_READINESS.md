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

## One bounded runtime

After the implementation commit is merged and one separate execution authorization is recorded, the
operator runs:

```text
pcdb proteingym-four-assay \
  --config configs/datasets/proteingym/v1.3/four-assay-unified-v1.json \
  --reviewed-git-commit <exact-merged-commit> \
  --approval "APPROVED FOR ISSUE #11 UNIFIED PROTEINGYM DATA READINESS EXECUTION" \
  --approval-comment-id <positive-comment-id> \
  --cos-destination cos://<approved-bucket>/<approved-prefix> \
  --repository-root <clean-exact-worktree> \
  --scratch-root <new-empty-local-directory>
```

This command is documentation, not current execution authorization. Credentials and the exact COS
destination remain outside Git.

The runtime performs BLAT first. HTTP/source identity, CRC, 4,783-row truth, processing, COS cap,
immutable publication or fresh-readback failure stops the whole run. A passing BLAT checkpoint
continues automatically to CAPSD, GFP and SPG1 without another human Gate.

## Fail-closed acquisition

For each selected member the runtime:

1. makes one exact Range request for the fixed ZIP local header;
2. validates signature, flags, compression method, frozen sizes/CRC and safe member basename;
3. requests only the variable local-header bytes and compressed payload;
4. requires HTTP 206 plus exact `Content-Range`, `Content-Length`, ETag and Last-Modified;
5. streams raw-DEFLATE extraction while computing CRC-32, SHA-256 and exact size;
6. validates CSV readability and row count;
7. uses no automatic retry and has no full-archive fallback.

Hard caps are 550,000,000 source network bytes, 2.5 GiB local scratch and 2.0 GB decimal COS growth.
The BLAT internal checkpoint additionally uses 5,000,000 B network, 64 MiB scratch and 15,000,000 B
COS growth caps.

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

## Boundaries

This implementation and its CI establish only an executable data-readiness route. A later formal run
establishes server-execution evidence. Neither establishes a benchmark result or scientific Gate.

The runtime does not:

- approve a zero-shot model or cost ratio;
- freeze an experiment train/validation/held-out split or initial observations;
- access held-out truth, run replay, train a model or compute high-value results;
- publicly redistribute raw/extracted CSV bytes;
- claim ProteinGym relicensed upstream assay data.

Any M5C experiment must separately freeze proxy identity, coverage eligibility, experimental
split/leakage, sampling, metrics, compute caps and held-out access before results are read.
