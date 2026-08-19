# M5 同状态因果诊断：简要理由与证据记录

## 文件职责

本文件仅解释为什么 M5 从 M4 事后归因转向“既有轨迹只读测量＋同状态分支干预＋独立
assay confirmation”。唯一规范性总方案是
[`M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`](M4_TO_M7_REVISED_EXPERIMENT_PLAN.md)；若两者出现差异，
以后者为准。本文件不复制其完整 protocol、验收条件或 Issue 拆分。

## 为什么不继续追加事后归因

M4 的 344 个成功 attempts 已经足以确立 screening terminal，但它们不是 344 个独立科研样本。
现有事后证据能定位若干现象，却不能仅靠追加 seed 或诊断切片，把相关性升级为跨 task/assay 的
因果结论。继续围绕同一批 M4 结果扩展解释，信息增益低且容易产生事后选择。

因此 M5 保留三步最小因果链：

1. M5A 从既有已揭示 observations 重建逐轮排名和选择机制；
2. M5B 在同一个 immutable observation prefix 上做 branch-isolated 单步干预；
3. M5C 在预先冻结的四个 ProteinGym assays 上做前瞻确认。

动作与原目标的关系是：机制测量或分支干预 → 观察 acquisition 是否改变下一批真实发现价值 →
判断 M4 阴性结果更可能来自策略无效、实现脱节还是任务异质性。M5 不以增加基础设施、Issue 数量
或长矩阵为目标。

## 证据锚点与成熟度

- M4 terminal：344 个成功 attempts，terminal `236250`，terminal SHA-256
  `06688b89650af8aba1e3f34728ebf4c6d2bfdb1e9d7a574995575556533c783d`。
- [#83 accepted H1–H7 report](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/83#issuecomment-5324953978)：
  report SHA-256 `2b0b23e2b01002fe4fe7fdf19252873b265395935c9680d562ddc8e159ca04ef`；
  H2/H4/H5/H7 `SUPPORTED`，H1/H3/H6 `INCONCLUSIVE`。
- [#85 seed-source tracer candidate](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/85#issuecomment-5328839257)：
  aggregate SHA-256 `6eb41d8d56782020cb441da721548cfe2e5651ecf6d01b687b627d938313aa56`；
  结论为 `MIXED-INCONCLUSIVE`。其实现仍在 Draft PR #86，因此只标记为
  `executed result candidate / pending merge`，不是已进入 `main` 的冻结基线。
- [H1/H3/H6 read-only follow-up](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/83#issuecomment-5337682238)：
  两 task 的 H1 校正视图均为 0/4 support，79/80 个 first next-batches 中 Exploration 与 Greedy
  不同，78/80 个 state 中 Exploration 至少与 Random 同样 Greedy-like。该记录只约束 M5 设计动机，
  不改变 H1/H3/H6 的正式 `INCONCLUSIVE` verdict。

## 三项关键设计澄清

### M5A：新的只读 analysis execution

M5A 可读取历史 attempt 已揭示的 observations（包括已揭示 labels），但必须绑定 exact source
commit/tree、environment identity、input inventory/checksum、report bytes/SHA-256 和最小 resource
probe。它的边界是：

`0 new RunSpec / 0 new benchmark attempt / 0 new oracle reveal / 0 unrevealed hidden-truth or held-out access`

因此 M5A 不再称为“零运行”；它是新的分析执行，但不是新的 benchmark attempt，也不创建新 COS
对象。

### M5B：同状态且分支隔离

Greedy、Exploration 和 Matched Random 必须从同一个 immutable observation prefix 构造三个
branch-isolated evaluator/oracle state；任何分支的 reveal 都不得传播到其他分支。令 `E` 为
Exploration top-k、`G` 为 Greedy top-k，Matched Random 固定保留 exact `E ∩ G`，并从
`remaining_pool − G` 按预冻结 seed 抽取 `k - |E ∩ G|` 个候选。

replicate 数、seed 派生和 tie-break 在结果读取前冻结；replicate 先在 observation state 内聚合，
不作为独立 task/assay 样本。该设计只估计冻结状态下下一批选择的 immediate batch effect，不能
直接声称完整多轮 active-learning trajectory 的长期因果收益。

### M5C：固定四-assay prospective cohort

M5C 与 #11 inventory 一致，使用全部四个 assays：

- `BLAT_ECOLX_Firnberg_2014`；
- `CAPSD_AAV2S_Sinai_2021`；
- `GFP_AEQVI_Sarkisyan_2016`；
- `SPG1_STRSG_Olson_2014`。

不得根据模型、acquisition、hit rate 或 held-out 结果再挑选 2–3 个。只有在读取任何策略结果前冻结
的 provenance、processing、candidate/label semantics、zero-shot coverage 或 split/leakage
eligibility 检查失败时才可排除，并必须保留失败证据。执行结构保持为 #11 acquisition bundle、一个
共享 processing/split protocol（带 per-assay manifests/identity/status）和一个 M5C confirmation
Issue。

## Decision Guard 结论

方向结论仍是 `PROCEED WITH CAP`：先完成 M5A；M5B 仅做冻结状态的最小单步分支；M5C 对全部四个
assays 前瞻确认。任何 bounded multi-round confirmation、正式数据获取、COS 写入、云端执行或 M6
扩量都需要其各自 protocol review、资源探测和明确授权。

## 未授权边界

本说明及其合并均不授权：

- 执行 #11 Gate A、下载或上传 ProteinGym 数据；
- 创建 truth-bearing branch evaluation 或访问未揭示 hidden truth/held-out；
- 将 Draft PR #86 的候选证据称为 accepted main baseline；
- 将 M5B 单批效应外推为多轮长期收益；
- 将 M5C 四-assay cohort 改为结果驱动的子集。
