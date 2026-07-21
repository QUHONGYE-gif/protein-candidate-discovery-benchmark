# 数据集登记

FLIP2 v3 Alpha Amylase、Imine Reductase、Nuclease B 与 Hydrophobic Core 已分别完成官方
raw source 下载、checksum 冻结和 COS 回读验证。Alpha Amylase processing implementation 已启动，
但尚无正式 processing run；四个 task 均未进入 split 审计或 replay。其他候选数据源包括
ProteinGym 和 SKEMPI 2.0；每个具体 task/assay 必须通过 dataset onboarding Issue 后才能进入开发
或主实验。

## 进行中的 onboarding

- FLIP2 v3 Alpha Amylase：GitHub Issue #3，raw ingest runtime 与 DOI manifest revision 已完成，
  machine-readable `DatasetIngestAttempt` 已回读验证；官方 source inventory、下载校验与 COS
  对象布局见
  [FLIP2 Amylase ingest](data/FLIP2_AMYLASE_INGEST.md)。Issue #15 已启动 candidate identity、
  hidden-label separation、duplicate/near-duplicate audit 与 Processing Manifest 实现，见
  [FLIP2 Amylase processing](data/FLIP2_AMYLASE_PROCESSING.md)；尚无正式 processed/COS 产物。
- FLIP2 v3 Imine Reductase：GitHub Issue #8，3/3 source objects、Dataset Manifest 和
  machine-readable `DatasetIngestAttempt` 均已完成 COS exact-byte/readback 验证及不可覆盖
  检查，见 [FLIP2 IRED ingest](data/FLIP2_IRED_INGEST.md)；PR #9 已合并。
- FLIP2 v3 Nuclease B：GitHub Issue #10，3/3 source objects、独立 Dataset Manifest 与
  `DatasetIngestAttempt` 已完成 COS exact-byte/readback 和不可覆盖验证，见
  [FLIP2 NucB ingest](data/FLIP2_NUCB_INGEST.md)；代码与证据仍需 PR/CI 和人工审查。
- FLIP2 v3 Hydrophobic Core：GitHub Issue #10，7/7 source objects、独立 Dataset Manifest 与
  `DatasetIngestAttempt` 已完成 COS exact-byte/readback 和不可覆盖验证，见
  [FLIP2 Hydro ingest](data/FLIP2_HYDRO_INGEST.md)；代码与证据仍需 PR/CI 和人工审查。
- Alpha Amylase processing implementation 已启动；正式 processing run、所有 Split、replay 和
  scientific validation 均未开始。

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
