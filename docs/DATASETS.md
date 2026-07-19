# 数据集登记

当前尚未下载或冻结正式数据。候选数据源包括 FLIP2、ProteinGym 和 SKEMPI 2.0，但每个具体
task/assay 必须通过 dataset onboarding Issue 后才能进入开发或主实验。

## Onboarding checklist

- [ ] 来源页面和下载 URL 已确认；
- [ ] license 与再分发限制已记录；
- [ ] 原始文件 SHA-256 已记录；
- [ ] label direction 和 missing-value 规则已确认；
- [ ] candidate ID 唯一；
- [ ] duplicate/near-duplicate audit 已完成；
- [ ] development/validation/held-out 边界已冻结；
- [ ] preprocessing 只在允许的数据范围内拟合；
- [ ] manifest 通过 schema validation；
- [ ] 数据只写入批准的 COS prefix。

不得仅因为数据公开就假设其可以重新分发。若许可证不允许，应只发布下载与处理脚本、manifest
和合法的索引信息。
