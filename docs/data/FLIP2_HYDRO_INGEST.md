# FLIP2 v3 Hydrophobic Core 原始数据入库

## 范围与证据边界

本流程对应 GitHub Issue #10 中的 Hydro task，只负责冻结并入库官方原始文件。它不把三个
wild-type proteins 解释为 homologs，不确认 fitness direction、wild-type identity 或任何
split/leakage policy，不授权 held-out，不运行 replay，也不构成 scientific gate 证据。

Canonical source 是 Zenodo record `18433203` version `v3`，DOI 为
`10.5281/zenodo.18433203`。task README 指向上游 Zenodo record `11175470`，声明
`CC-BY-4.0`、first-hand lab provenance，并归属 Escobedo et al. 的 “Genetics, energetics and
allostery during a billion years of hydrophobic protein core evolution”。外部发布必须保留 FLIP2
release、task README、上游数据集 DOI 与论文归属。

## 冻结的 source inventory

| Source key | Bytes | Publisher MD5 |
|---|---:|---|
| `hydro/README.md` | 430 | `eb762cd877939c4cee891bdc1c159650` |
| `hydro/random_split.csv.gz` | 376954 | `1007879548f6f414387ef474922308d9` |
| `hydro/three_to_many.csv.gz` | 369389 | `a13b01d71faf2e76dd91dc57b7311972` |
| `hydro/low_to_high.csv.gz` | 372137 | `fb9cfaef56791b90f2ddd154b90da8ed` |
| `hydro/to_P06241.csv.gz` | 357257 | `1b860081da084196b276804b573283cf` |
| `hydro/to_P0A9X9.csv.gz` | 359737 | `64d1690065aeb0ad90cd1e1b5dd7a581` |
| `hydro/to_P01053.csv.gz` | 361259 | `5b7ade3bb837d2d8ade1e75f659d77f7` |

共 7 个文件、`2197163` bytes。这些 random、mutation-count、fitness-range 与 wild-type transfer
文件均只是 upstream split references；raw ingest 不把它们授权为正式 benchmark splits。

## 本机 staging 与 COS layout

正式数据位于 Git worktree 外的 staging。下载先进入 `incoming/**/*.part`，publisher MD5 与 byte
size 通过后才排他发布到 `verified/` 并计算 SHA-256。

```text
raw/flip2/zenodo-18433203-v3/hydro/<source-filename>
manifests/flip2/zenodo-18433203-v3/hydro/dataset-manifest.json
manifests/flip2/zenodo-18433203-v3/hydro/ingest-attempts/<attempt-id>.json
```

## 2026-07-21 runtime evidence

- raw-ingest commit：`528f3eca87ae462cc8cba537cadf67a2deb34282`；
- publisher MD5 与 byte size：7/7 通过，总计 `2197163` bytes；
- COS raw readback：7/7 的 SHA-256、size 与本机 verified bytes 全部一致；
- Dataset Manifest：`2492` bytes，SHA-256
  `5770fd53ff2117888edb5d21bff4f86dd6f9855ef105ce74ddbf2397244b6fcc`，COS exact-byte
  readback 与 Draft 2020-12 Schema 均通过；
- DatasetIngestAttempt：`flip2-hydro-20260721T021708Z.json`，`3801` bytes，SHA-256
  `a6f214a3bf298d75369f8f3d56a37a5be43f233017b76bab6e5a14a74691cebe`，COS exact-byte
  readback 与 Schema 均通过；
- Dataset Manifest 和 DatasetIngestAttempt 的重复写入均返回 `409 FileAlreadyExists`，再次回读
  后 checksum 未变化；
- COS task prefix：raw objects 为 7，manifest root 为 2（1 manifest + 1 ingest attempt）。

原始 CSV 当前可观察字段为 `sequence,target,set,validation`。三个 wild-type 的身份关系、label
direction、missing-value、candidate identity、duplicate 规则和 split membership 均属于后续
Processing Manifest 与 Split Manifest，尚未确认。
