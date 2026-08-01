# FLIP2 v3 Nuclease B 原始数据入库

## 范围与证据边界

本流程对应 GitHub Issue #10 中的 NucB task，只负责冻结并入库官方原始文件。它不解释四档
activity classification 的语义，不执行 candidate identity、duplicate/near-duplicate 或 split/leakage
审计，不授权 held-out，不运行 replay，也不构成 scientific gate 证据。

Canonical source 是 Zenodo record `18433203` version `v3`，DOI 为
`10.5281/zenodo.18433203`。task README 声明 `CC-BY-4.0`、first-hand lab provenance，并归属
Thomas et al. 的 “Engineering of highly active and diverse nuclease enzymes by combining machine
learning and ultra-high-throughput screening”（bioRxiv `2024.03.21.585615`）。外部发布必须保留
FLIP2 release、task README 与上游论文归属。

## 冻结的 source inventory

| Source key | Bytes | Publisher MD5 |
|---|---:|---|
| `nucb/README.md` | 733 | `6f6cef8efcdf701244c47830722f86b0` |
| `nucb/random.csv.gz` | 629753 | `8b8f333d2c96fe6f92d7ebd692d684d8` |
| `nucb/two_to_many.csv.gz` | 624937 | `cdedaadd57f2b148950b357e9f24c238` |

共 3 个文件、`1255423` bytes。`random.csv.gz` 与 `two_to_many.csv.gz` 均只是忠实保存的
upstream artifacts；raw ingest 不把它们授权为正式 benchmark split。

## 本机 staging 与 COS layout

正式数据位于 Git worktree 外的 staging。下载先进入 `incoming/**/*.part`，publisher MD5 与 byte
size 通过后才排他发布到 `verified/` 并计算 SHA-256。

```text
raw/flip2/zenodo-18433203-v3/nucb/<source-filename>
manifests/flip2/zenodo-18433203-v3/nucb/dataset-manifest.json
manifests/flip2/zenodo-18433203-v3/nucb/ingest-attempts/<attempt-id>.json
```

## 2026-07-21 runtime evidence

- raw-ingest commit：`528f3eca87ae462cc8cba537cadf67a2deb34282`；
- publisher MD5 与 byte size：3/3 通过，总计 `1255423` bytes；
- COS raw readback：3/3 的 SHA-256、size 与本机 verified bytes 全部一致；
- Dataset Manifest：`1615` bytes，SHA-256
  `00baeb776a6640611c4e0b49ee511adb51eb79263f016f7b47303bf4798b6d5e`，COS exact-byte
  readback 与 Draft 2020-12 Schema 均通过；
- DatasetIngestAttempt：`flip2-nucb-20260721T021708Z.json`，`2983` bytes，SHA-256
  `bfef390939fd636a19a53ad306ce8ef7015ccb22cd55620eafb82a0fbd05ae5e`，COS exact-byte
  readback 与 Schema 均通过；
- Dataset Manifest 和 DatasetIngestAttempt 的重复写入均返回 `409 FileAlreadyExists`，再次回读
  后 checksum 未变化；
- COS task prefix：raw objects 为 3，manifest root 为 2（1 manifest + 1 ingest attempt）。

原始 CSV 当前可观察字段为 `sequence,target,set,validation`。这些字段的 label direction、activity
bin、missing-value、candidate identity、duplicate 规则和 split membership 均属于后续 Processing
Manifest 与 Split Manifest，不由本 raw-ingest evidence 确认。后续状态见
[dataset registry](../DATASETS.md) 的 NucB processing 摘要与 [NucB split](FLIP2_NUCB_SPLIT.md)。
