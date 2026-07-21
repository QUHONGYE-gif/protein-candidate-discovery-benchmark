# SKEMPI 2.0 raw ingest

## 当前结论

Issue #12 已完成 SKEMPI 2.0 官方最小 source bundle 的下载、local verification、COS
immutable upload 与独立 readback。当前只证明公开原始包已被可复现地保存；在本 PR 完成人工
review 与 merge 前，仍不标记为 ready for processing。

| 状态 | 当前值 | 证据 |
|---|---|---|
| downloaded | true | 2 个官方对象，共 32,084,298 bytes |
| verified | true | expected size、SHA-256、CSV parse、tar member inventory |
| uploaded | true | cos://data/raw/skempi/v2.0/2018-06/source-bundle/ |
| ready_for_processing | false | 等待 Draft PR 的 CI 与 human review/merge |

这四个状态彼此独立。uploaded=true 不代表 processing、split、external validation 或
scientific gate 已获得授权。

## 冻结来源与许可

- Official download page：<https://life.bsc.es/pid/skempi2/database/index>
- Terms of use：<https://life.bsc.es/pid/skempi2/info/terms>
- DOI：10.1093/bioinformatics/bty635
- License：CC-BY-4.0
- Attribution：Jankauskaite J, Jimenez-Garcia B, Dapkunas J,
  Fernandez-Recio J, Moal IH. *SKEMPI 2.0*. Bioinformatics 35, 462-469；
  SKEMPI2 Copyright 2018 BSC。
- HTTP metadata observed at：2026-07-21T02:39:21Z

两个官方 response 均为 HTTP 200，Content-Length 与下表一致；当时未提供 ETag 或
Last-Modified。publisher 也未提供 cryptographic checksum，因此没有把本地 digest
表述成 publisher checksum。

| Source object | 页面日期 | Bytes | 本地计算 SHA-256 |
|---|---:|---:|---|
| skempi_v2.csv | 2018-06-06 | 1,602,208 | 76f8e60d09eaa4f05fb265063886859a86895c0b2fb225e31fe82928a883972d |
| SKEMPI2_PDBs.tgz | 2018-06-08 | 30,482,090 | 82418131a3864aa8484ea6ec300e58f9bc0349e21530f83f7ce336b41589a95a |

Dataset Manifest 中 downloaded_at 为 2026-07-21T02:51:43.796908Z。source inventory
在下载前冻结了 canonical URL、页面日期、HTTP metadata、expected bytes、license 与
已知限制。

## Source inspection

skempi_v2.csv 使用分号 delimiter，可解析出 29 列 header 与 7,085 条 data rows。本
Issue 没有解释或规范化任何 mutation、affinity、temperature、unit 或 label 字段。

SKEMPI2_PDBs.tgz 可完整读取，包含 1,381 个 regular members：

- 345 个 PDB payload members；
- 345 个 mapping payload members；
- 691 个 AppleDouble metadata members（._*）。

AppleDouble members 是 publisher archive 内原有内容。raw object 保持 exact bytes，不在
ingest 阶段删除；source inspection 将它们保留在 deterministic inventory 中，但不计入
PDB 或 mapping payload count。结构缺失、文件名/映射歧义以及 table-to-structure mapping
完整性留给后续 processing Issue，不能从 345/345 的计数推断已经完成记录级匹配。

## COS publication 与回读

对象布局：

- raw/skempi/v2.0/2018-06/source-bundle/skempi_v2.csv
- raw/skempi/v2.0/2018-06/source-bundle/SKEMPI2_PDBs.tgz
- manifests/skempi/v2.0/2018-06/source-bundle/dataset-manifest.json
- manifests/skempi/v2.0/2018-06/source-bundle/source-inspection.json
- manifests/skempi/v2.0/2018-06/source-bundle/ingest-attempts/skempi-v2-20260721T025812Z.json

首次上传前，raw 与 manifest target prefixes 均为 0 objects。全部上传使用
--forbid-overwrite --err-retry-num 0。随后从 COS 下载到新的 temporary directory，
5 个对象均通过 exact-byte、size 与 SHA-256 验证。

对已存在的 Dataset Manifest 重复执行禁止覆盖上传时，coscli exit code 为 2，COS 返回
409 FileAlreadyExists。再次回读后 Manifest 仍为 1,663 bytes，SHA-256 仍为
773d714ad10bbce46d3791da2bbd5feb75377239fb39a7785421bb1a014536ee。

## Provenance

- Raw ingest 与 Manifest revision Git commit：
  022efc65b4a4d08f4656c3bd950a3695f300c1a1
- Source inventory：
  3511ba8c00e8c31736b17a59fad946a10ecd1f7d779117e9f924ffa390b18f76
- Dataset Manifest：
  773d714ad10bbce46d3791da2bbd5feb75377239fb39a7785421bb1a014536ee
- Source inspection：
  1700f3add0fb06d2868fa8609ffed35ea374420c9b25b22e9cf714f048162ada
- DatasetIngestAttempt：
  aa793022ef1d36aa9603645bb77f5cde56bfd907beae7a606d765f504cf2d838
- Runtime：Python 3.14.4，coscli v1.0.8，macOS arm64，
  COS region ap-beijing，bucket alias data

DatasetIngestAttempt.verification.publisher_md5_and_size_verified=false 是有意的：publisher
没有提供 MD5 或其他 cryptographic checksum。expected size 已验证，本地 SHA-256 和 COS
readback 也已验证；这不能被改写为 publisher checksum evidence。

## 科学边界

本 ingest 没有完成或授权：

- mutation normalization 或 mutation-to-chain mapping；
- affinity/unit 清洗或 delta-delta-G direction；
- missing structure repair、record filtering 或 deduplication；
- complex/protein-pair leakage-safe split；
- held-out external-validation use；
- feature、model、replay、benchmark evidence 或 scientific gate。

任何 processing 与实验使用必须由后续独立 Issue、Processing Manifest、Split Manifest
及对应 gate 明确授权。
