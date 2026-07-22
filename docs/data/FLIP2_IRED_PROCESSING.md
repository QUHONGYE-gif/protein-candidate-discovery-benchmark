# FLIP2 v3 Imine Reductase 数据处理

## 范围与证据边界

本流程对应 GitHub Issue #16，把已冻结的 IRED raw bundle 转换为确定性的规范候选表、hidden-truth
表、上游 split 元数据审计表和 Processing Manifest。它不冻结 benchmark split，不授权 held-out，
不生成特征，不运行模型或 replay，也不构成 benchmark 或 scientific-gate 证据。

输入必须绑定已接受的 Dataset Manifest，SHA-256 为
`37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643`。raw 文件继续保持不可覆盖，
正式 processing 只能在 reviewed commit 上生成新的 content-addressed revision。

## 第一方来源、标签与候选含义

FLIP2 v3 release DOI 为 [`10.5281/zenodo.18433203`](https://doi.org/10.5281/zenodo.18433203)，
IRED task README 指向 Gantz、Mathis 与 Hollfelder 发布的 SrIRED 数据
[`Zenodo 11357236`](https://zenodo.org/records/11357236) 及论文
[`10.1101/2024.04.08.588565`](https://doi.org/10.1101/2024.04.08.588565)。上游把 active candidate
定义为具有可测 fitness 的 SrIRED variant，fitness 是相对 wild type 的 log-enrichment factor；
inactive 表表示在 input count 足够时仍没有可辨识 output count 的 variants。

因此本 revision 把 `target` 冻结为无量纲、相对 WT 的 log-enrichment fitness，并使用
`higher_is_better`：更高 enrichment 表示更高的测量活性。第一方 task metadata 未声明 logarithm base，
本流程不补造该信息，也不设置数值 high-value threshold。后续 threshold、membership 与 held-out
含义必须由独立 Split/experiment contract 冻结。

## raw、上游与 FLIP2 candidate count 对账

accepted raw 中的两个 gzip CSV 字段顺序均为 `sequence,set,validation,target`。只读审计结果：

| 检查 | 结果 |
|---|---:|
| `random.csv.gz` rows / unique sequences | 8,586 / 8,586 |
| `two_to_many.csv.gz` rows / unique sequences | 8,586 / 8,586 |
| union / intersection | 8,586 / 8,586 |
| 跨文件 target 冲突 | 0 |
| 缺失值、非有限 target、非法序列 | 0 |
| sequence length / alphabet | 290 / 20 个标准氨基酸 |
| exact duplicate | 0 |
| full-length Hamming distance = 1 pairs | 9,279 |

两个文件的 sequence、row order 和 target 一致；只有 `set` 与 `validation` 分配不同。它们是同一
candidate universe 的 alternate upstream split references，不能拼接成 17,172 个候选，也不能直接
成为本项目 benchmark split。

上游发布文件经 publisher MD5 验证后观察到 active 10,905 rows、inactive 5,373 rows，合计 16,278，
与论文层面的 17,143-variant 描述相差 865；第一方 metadata 未解释该差值，因此保留为明确 limitation，
不推断缺失记录。FLIP2 的 8,586 candidates 可精确对账为：

1. 从 active 表保留 `indel=False`、无内部 STOP、无 STOP readthrough 的 rows；
2. 排除 `Nham_aa` 为 22、31、33 的三个高突变数离群 rows，保留 mutation count 0–15；
3. 移除所有保留序列统一的末端 stop marker `*`，得到长度 290 的 canonical sequence；
4. inactive 表与 FLIP2 candidate universe 无 overlap，不进入连续 hidden truth；
5. FLIP2 target 与上游 fitness 数值一致；74 个文本值只存在约一个 ULP 的十进制重写差异，所以
   processing 以 accepted FLIP2 raw target bytes 为准，不从上游重新计算或替换。

## reference、mutation 与 near duplicate

上游唯一 WT 去掉统一末端 `*` 后，与 FLIP2 8,586 candidates 的唯一 per-position modal consensus
完全相同。reference 长度为 290，SHA-256 为
`27fbaa950b2c68b73f7595a2a53e86a8ab627118640b8e181a08b57a324e2fdb`；完整公开 sequence 冻结在
processing config 中。全部 candidate 对该 reference 的 full-length Hamming distance 与上游
`Nham_aa` 一致，范围为 0–15。

近重复方法在查看任何 downstream model result 前冻结为 full-length Hamming distance，当前只审计
threshold 1：共 9,279 pairs。Processing 保留这些 candidates；grouping、exclusion、leakage control
与跨 partition 约束留给 Split Manifest。

## 冻结的处理规则

- `sequence` 必须已经是长度 290、只含标准氨基酸的 uppercase canonical text，不静默修复；
- `candidate_id` 是 dataset/task namespace 与完整 sequence 的 SHA-256，独立于 row order、source
  filename 和 source enumeration；
- `target` 只写入 hidden-truth artifact，不进入 observable candidate table；
- `set`、`validation`、`random` 与 `two_to_many` 只作为上游审计 metadata；
- 任一文件内部 duplicate、缺失值、非法字段、非有限 target、reference/mutation/near-duplicate
  audit drift 均 fail closed；
- 两文件重复出现的 sequence 只有在 target 一致时聚合，candidate universe 或 target 冲突时 fail
  closed；
- 输出 identity 复用共享 processing core，绑定 Dataset Manifest、processing config 和完整 Git commit。

规则保存在
[`ired-processing-v1.json`](../../configs/datasets/flip2/zenodo-18433203-v3/ired-processing-v1.json)。

## 输出布局

处理器通过 `scripts/process_flip2_ired.py` 在 Git worktree 外排他创建：

```text
processed/flip2/zenodo-18433203-v3/ired/<processing-id>/
├── candidates.csv
├── hidden-truth.csv
├── upstream-split-metadata.csv
└── processing-audit.json

manifests/flip2/zenodo-18433203-v3/ired/processing/<processing-id>/
└── processing-manifest.json
```

Processing Manifest 为每个 output 记录 object key、SHA-256、byte size 与 row count。正式运行必须
在 implementation、CI 和 human review 完成后的 exact commit 上执行；COS publication 必须使用禁止
覆盖的新 prefix，并从 COS fresh readback 验证 checksum、size、row count、Schema 和 exact bytes。

## 当前实现验证

accepted COS raw bytes 的本地只读预检已完成两次 exact-byte-identical build，得到 8,586 candidate
rows、8,586 hidden-truth rows、17,172 upstream metadata rows 和一份 audit document；reference、
mutation range、9,279 near-duplicate pairs、field visibility 与 Processing Manifest Schema 均通过。

该预检没有写入 COS，临时 processing identity 不得发布或引用为正式 revision。只有 reviewed commit
上的 immutable publication 与 fresh COS readback 才能建立 server-execution evidence。Split、feature、
model、replay、benchmark result 和 scientific gate 均未开始。
