# 数据集登记

FLIP2 v3 Alpha Amylase、Imine Reductase、Nuclease B 与 Hydrophobic Core 已分别完成官方
raw source 下载、checksum 冻结和 COS 回读验证。Alpha Amylase 与 Imine Reductase 已分别在
reviewed commit 上完成正式 immutable processing publication、权限恢复和 fresh readback；四个 task
均未进入 split 审计或 replay。其他候选数据源包括 ProteinGym 和 SKEMPI 2.0；每个具体 task/assay
必须通过 dataset onboarding Issue 后才能进入开发或主实验。

## 进行中的 onboarding

- FLIP2 v3 Alpha Amylase：GitHub Issue #3，raw ingest runtime 与 DOI manifest revision 已完成，
  machine-readable `DatasetIngestAttempt` 已回读验证；官方 source inventory、下载校验与 COS
  对象布局见
  [FLIP2 Amylase ingest](data/FLIP2_AMYLASE_INGEST.md)。Issue #15 负责 candidate identity、
  hidden-label separation、duplicate/near-duplicate audit、Processing Manifest 与正式运行证据，见
  [FLIP2 Amylase processing](data/FLIP2_AMYLASE_PROCESSING.md)。正式 processing revision
  `flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b`
  已发布四个 processed payload；Processing Manifest SHA-256 为
  `cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec`。首次 publication
  因 payload PUT 权限返回 403 而不完整；恢复操作只补齐四个缺失 payload，禁止覆盖且未改写已发布
  manifest，随后完成 fresh readback。该证据只建立 processing server-execution，不建立 split、
  replay、benchmark 或 scientific gate。
- FLIP2 v3 Imine Reductase：GitHub Issue #8，3/3 source objects、Dataset Manifest 和
  machine-readable `DatasetIngestAttempt` 均已完成 COS exact-byte/readback 验证及不可覆盖
  检查，见 [FLIP2 IRED ingest](data/FLIP2_IRED_INGEST.md)；PR #9 已合并。Issue #16 processing
  implementation 复用共享 processing core，冻结 8,586 个 canonical candidates、hidden-label
  separation、source relationship、WT/mutation 与 duplicate/near-duplicate audit，见
  [FLIP2 IRED processing](data/FLIP2_IRED_PROCESSING.md)。Reviewed commit
  `703247cb001e063c78bd8d34d66b094a9514dc3a` 的正式 processing revision
  `flip2-ired-8f32dfa11a52d3ce96da4eb4ebb305210ccf027cf812d277f2c9c25f254c4f5d`
  已发布四个 processed payload；Processing Manifest SHA-256 为
  `929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba`。4+1 对象均通过
  fresh checksum/size/row-count/Schema/domain/exact-byte readback，重复 manifest PUT 以
  `409 FileAlreadyExists` 失败且对象未变。该证据只建立 processing server-execution，不建立 split、
  replay、benchmark 或 scientific gate。
- FLIP2 v3 Nuclease B：GitHub Issue #10，3/3 source objects、独立 Dataset Manifest 与
  `DatasetIngestAttempt` 已完成 COS exact-byte/readback 和不可覆盖验证，见
  [FLIP2 NucB ingest](data/FLIP2_NUCB_INGEST.md)；代码与证据仍需 PR/CI 和人工审查。
- FLIP2 v3 Hydrophobic Core：GitHub Issue #10，7/7 source objects、独立 Dataset Manifest 与
  `DatasetIngestAttempt` 已完成 COS exact-byte/readback 和不可覆盖验证，见
  [FLIP2 Hydro ingest](data/FLIP2_HYDRO_INGEST.md)；代码与证据仍需 PR/CI 和人工审查。
- Alpha Amylase 与 IRED processing implementation、正式 immutable publication/recovery 与 fresh
  readback 已完成；所有 Split、held-out 授权、replay 和 scientific validation 均未开始。

## Onboarding checklist

- [ ] 来源页面和下载 URL 已确认；
- [ ] task-level license、record-level provenance 与再分发限制已记录；
- [ ] Dataset Manifest 已记录 DOI、下载时间、原始文件 SHA-256 和已知限制；
- [ ] Dataset ingest attempt 已绑定 Git commit、环境、命令、COS 回读与不可覆盖证据；
- [ ] label direction 和 missing-value 规则已确认；
- [ ] candidate ID 唯一；
- [ ] Processing Manifest 已绑定处理代码 commit、配置 hash、字段可见性和处理输出；
- [ ] duplicate/near-duplicate audit 与处理规则已写入 Processing Manifest；
- [ ] development/validation/held-out 边界与 leakage control 已写入 Split Manifest；
- [ ] preprocessing 只在允许的数据范围内拟合；
- [ ] 完整 manifest chain 通过 Schema 与领域语义校验；
- [ ] 数据只写入批准的 COS prefix。

不得仅因为数据公开就假设其可以重新分发。若许可证不允许，应只发布下载与处理脚本、manifest
和合法的索引信息。
