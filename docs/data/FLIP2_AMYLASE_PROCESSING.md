# FLIP2 v3 Alpha Amylase 数据处理

## 范围与证据边界

本流程对应 GitHub Issue #15，负责把已冻结的 Alpha Amylase raw bundle 转换为确定性的规范候选表、
hidden-truth 表、上游 split 元数据审计表和 Processing Manifest。它不冻结 benchmark split，不授权
held-out，不生成特征，不运行模型或 replay，也不构成 benchmark/scientific evidence。

输入必须绑定已接受的 Dataset Manifest revision，SHA-256 为
`c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac`。旧的 legacy manifest 已被
revision 2 supersede，不得作为 processing 输入。

## 第一方标签语义

原始研究为 van der Flier et al., “Enzyme structure correlates with variant effect predictability”，
DOI [`10.1016/j.csbj.2024.09.007`](https://doi.org/10.1016/j.csbj.2024.09.007)。论文把候选定义为
`AMY_BACSU` 的高阶组合突变体；`target` 是三次去污实验的平均 released-soil absorbance 减去空白
对照后的非归一化 stain-removal activity。更高吸光度表示更高去污活性，因此本 processing revision
冻结 `higher_is_better`。论文明确说明浓度归一化数据因技术问题未用于该分析，本流程不得把当前
`target` 重新解释为浓度归一化酶活。

## 2026-07-21 raw 只读审计

五个 gzip CSV 均包含 `sequence,target,set,validation`，每个文件均为 3,706 行：

| 检查 | 结果 |
|---|---:|
| 每个文件的唯一 sequence | 3,706 |
| 五文件 union / intersection | 3,706 / 3,706 |
| 跨文件 target 冲突 | 0 |
| 缺失值、非有限 target、非法序列 | 0 |
| 序列长度 | 425 |
| 字母表 | 20 个标准氨基酸 |
| 相对 modal reference 的突变数 | 0–8 |
| full-length Hamming distance = 1 的候选对 | 4,311 |

五个文件描述同一候选集合和同一 hidden truth；区别只在 `set` 与 `validation`。因此不得把五个文件
拼接成 18,530 个独立候选，也不得把任一上游 split 自动冻结为 benchmark split。

## 冻结的 processing 规则

- `sequence` 必须已经是长度 425、只含标准氨基酸的 uppercase canonical text；不静默修复；
- `candidate_id` 是 namespace 与完整 sequence 的 SHA-256，独立于行顺序和 source filename；
- `target` 单独写入 hidden-truth artifact，不出现在 observable candidate table；
- `set` 与 `validation` 只写入 upstream metadata audit artifact；
- 任一文件内部 sequence 重复、缺失值、非法字段或非有限 target 均 fail closed；
- 五文件之间相同 sequence 只有在 target 一致时聚合；冲突时 fail closed；
- 近重复使用 full-length Hamming distance 审计，在 Processing 阶段保留；grouping、exclusion 与
  leakage control 留给独立 Split Manifest Issue。

规则保存在
[`amylase-processing-v1.json`](../../configs/datasets/flip2/zenodo-18433203-v3/amylase-processing-v1.json)。

## 输出与正式运行边界

处理器在 Git worktree 外排他创建：

```text
processed/flip2/zenodo-18433203-v3/amylase/<processing-id>/
├── candidates.csv
├── hidden-truth.csv
├── upstream-split-metadata.csv
└── processing-audit.json

manifests/flip2/zenodo-18433203-v3/amylase/processing/<processing-id>/
└── processing-manifest.json
```

`processing-id` 绑定 Dataset Manifest SHA-256、processing config SHA-256 和完整 Git commit。
Processing Manifest 为每个输出记录 object key、SHA-256、byte size 与 `row_count`；CSV 的
`row_count` 不包含 header，`processing-audit.json` 作为一个 JSON document 记录为 1。
正式运行必须在 reviewed commit 上执行，并将本地 staging 与 COS publication/readback 分开报告。
当前文档中的 2026-07-21 结果是 raw 只读审计，不是成功的正式 processing run，也没有创建或上传
上述对象。
