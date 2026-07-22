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

## 输出布局

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

## 2026-07-22 正式 publication 与恢复证据

正式运行绑定 reviewed commit `57026fa5bc46240d311f6378f9ef87dcbe0d665c`，生成的 immutable
processing revision 为
`flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b`。
首次 publication attempt 中，当前执行身份对四个 processed payload 的 PUT 均返回
`403 AccessDenied`，精确 prefix 复查确认四个 payload 均未写入；Processing Manifest 单独写入成功并
通过回读。该状态被明确报告为不完整，不得作为成功运行或可消费 revision。

恢复时从同一 commit、同一 Dataset Manifest 和 processing config 重新生成 exact-byte-identical
staging，只以禁止覆盖模式补齐四个缺失 payload；没有删除或覆盖已存在的 Processing Manifest。
随后在新的临时 readback 目录验证全部五个对象，结果如下：

| Artifact | `row_count` | Bytes | SHA-256 |
|---|---:|---:|---|
| `candidates.csv` | 3,706 | 1,819,668 | `50c13a23f9fbfe3892b074a1ce7b31eb5537dfe21ceb682baccbf412c0d42aab` |
| `hidden-truth.csv` | 3,706 | 301,480 | `1d7b4deb06af08649867cbdeec6d0ef13dd70e2997687bbc290493a63fb7b8f8` |
| `processing-audit.json` | 1 | 3,149 | `0f6940999bb42e6946349b001e10ae203403d83079d3f5ba6a5c9bccee064e94` |
| `upstream-split-metadata.csv` | 18,530 | 1,930,061 | `be2a3c7f2f4502fc1178391816b56b60b59c3b88ac352c71df4a93be4f44af2a` |
| Processing Manifest | 1 document | 2,429 | `cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec` |

四个 payload 合计 4,054,358 bytes。Fresh readback 对五个对象通过 staged/readback exact-byte、
SHA-256、byte size、row count、Processing Manifest Schema、field visibility、candidate-ID 唯一性、
candidate universe 一致性和每个 candidate 恰有五条 upstream membership 的验证；精确 processed 与
manifest prefixes 没有额外对象。失败尝试与恢复完成分别记录在 Issue #15 的
[permission-blocked report](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/15#issuecomment-5041312979)
和
[recovery evidence](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/15#issuecomment-5041703460)。

最终 reviewer 身份独立确认了远端精确对象数量与大小，并下载、校验了 Processing Manifest；该身份
对四个 payload 的 HEAD/Get 仍返回 403，所以 payload exact-byte fresh readback 证据来自 recovery
operator，而不是 reviewer 身份下的第二次独立下载。此角色分离不改变成功的 processing
server-execution 结论，但必须保留这一证据来源边界。

以上只完成 Alpha Amylase processing implementation 与 server-execution evidence。Split membership、
near-duplicate grouping/leakage control、held-out 授权、feature、model、replay、benchmark result 和
scientific gate 均未完成，也不得从本 revision 推断。
