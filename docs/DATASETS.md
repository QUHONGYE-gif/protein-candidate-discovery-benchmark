# 数据集登记

FLIP2 v3 Alpha Amylase 已完成官方 raw source 下载、checksum 冻结和 COS 回读验证，
但尚未进入 processing、split 审计或 replay。其他候选数据源包括 FLIP2 其他 task、
ProteinGym 和 SKEMPI 2.0；每个具体 task/assay 必须通过 dataset onboarding Issue 后才能进入
开发或主实验。

## 进行中的 onboarding

- FLIP2 v3 Alpha Amylase：GitHub Issue #3，raw ingest runtime 已完成；官方 source inventory、下载校验与
  COS 对象布局见 [FLIP2 Amylase ingest](data/FLIP2_AMYLASE_INGEST.md)。
- 当前分支仍需 PR/CI 评审；Processing、Split、replay 和 scientific validation 均未开始。

## Onboarding checklist

- [ ] 来源页面和下载 URL 已确认；
- [ ] license 与再分发限制已记录；
- [ ] Dataset Manifest 已记录下载时间、原始文件 SHA-256 和已知限制；
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
