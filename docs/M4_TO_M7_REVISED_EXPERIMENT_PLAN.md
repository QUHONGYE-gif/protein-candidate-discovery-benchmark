# M4 至 M7 修订实验方案

## 文件职责与生效边界

本文是在 M4 已产生正式阴性筛选结果后建立的 M4→M7 新实验规划，也是 M4 之后唯一的
normative plan。它不删除、不覆盖、
不原地修改 [`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md) 或任何已冻结协议、RunSpec、
成功 attempt 与审核记录。原路线继续作为历史规划基线；本文经审核合并后，作为 M4 之后的
执行补充与后续 Issue 设计依据。

[`M5_SAME_STATE_CAUSAL_DIAGNOSTIC_EXPLANATION.md`](M5_SAME_STATE_CAUSAL_DIAGNOSTIC_EXPLANATION.md)
按用户要求逐字保存形成本文之前的讨论，只是不可变、非规范的 historical decision record，
不与本文并行维护。二者出现术语、cohort、证据成熟度或执行边界差异时，以本文为准。

本文不改变 M0–M3 的完成状态，不追溯性改变 M4 Gate 3，也不因提出后续路线而授权新的
RunSpec、hidden truth、held-out、COS、服务器、凭据、数据集或付费计算操作。

## 研究目标

北极星目标保持不变：在公开、可复现、hidden-label-safe 的条件下，说明 surrogate model、
uncertainty method 与 acquisition strategy 在固定标签或成本预算下何时产生真实发现价值，
何时冗余或有害。

M4 之后不再把“找到一个普遍胜过 Greedy 的策略”作为必须达成的结果。新的主线问题是：

1. acquisition 在同一模型和同一 observation state 上是否真正改变决策；
2. 改变后的候选是否包含超出随机偏离的决策信息；
3. 冗余、无信息偏离、机会成本与条件性正收益分别在什么 task/assay/预算条件下出现；
4. 这些机制能否在独立 ProteinGym assay 与最小 SKEMPI 2.0 external validation 中保持；
5. 单保真结论如何延伸到固定总成本下的可控与真实多保真条件。

论文允许得到强阴性或条件化结论，但不允许通过事后扩大 seed、模型、beta、threshold 或
acquisition 网格制造阳性结果。

## 已冻结证据基线

| 证据 | 当前事实 | 在新方案中的用途 |
|---|---|---|
| M4 pilot＋conditional expansion | 344 个成功 attempts；Gate 3 为 `STOP`；6 个 fragile、2 个 eliminated、0 retained | 保留为正式 L1 screening 与多轮阴性证据，不重跑、不改写 |
| #83 H1–H7 failure analysis | merged `main@d8f4915`；canonical report 98,778 bytes / SHA-256 `2b0b23e2b01002fe4fe7fdf19252873b265395935c9680d562ddc8e159ca04ef`；result review [comment 5324953978](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/83#issuecomment-5324953978)；H2/H4/H5/H7 `SUPPORTED`，H1/H3/H6 `INCONCLUSIVE` | accepted exploratory report；只用于提出机制假设，不作为因果确认 |
| #85 28-run seed-source tracer | `executed result candidate / pending merge`；result ledger [comment 5328839257](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/85#issuecomment-5328839257)；aggregate SHA-256 `6eb41d8d56782020cb441da721548cfe2e5651ecf6d01b687b627d938313aa56`；实现仍在 Draft PR #86 head `209e230`，Issue #85 仍 Open | 在 PR #86 审核合并与 #85 闭环前，不作为已进入 `main` 的冻结基线；不继续用 seed 扩量代替 task/assay 扩展 |

344 个 attempts 不能被解释为 344 个独立科研样本。跨任务结论的统计单位仍然是 task、assay、
protein 或 complex；seed 只用于估计算法随机性。

此前 H1 corrected-boundary 与 H3/H6 first-batch 补充检查没有持久化 canonical report；现由
[author-side non-review evidence ledger](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/83#issuecomment-5337682238)
绑定 `main@d8f4915`、M4 terminal、#83 input report、只读实现 identity 与聚合结果。该评论不是独立
审核或 canonical report，因此仍不列为 accepted/frozen evidence，只作为本路线的描述性动机。
若要进入 scientific evidence，必须由 M5A 在 reviewed exact source/environment/input 下重新产生
checksum-bound report，不能用作者评论或未持久化终端输出替代。

## 总体执行顺序

```text
保留 M4 Gate 3 STOP
→ M5A：既有轨迹逐轮机制测量（新的只读 analysis execution；零新 benchmark attempt）
→ M5B：同状态单步分支干预（Alpha/IRED 探索）
→ M5C：ProteinGym 四-assay prospective cohort 前瞻确认
→ M5D：最小 SKEMPI 2.0 external validation
→ M6：E4 可控多保真＋E5 ProteinGym 真实代理
→ M7：证据冻结、统计汇总与论文产物
```

M5A 不是独立的大型科研阶段，不单独增加模型、task、seed 或基础设施。它是 M5B 的新只读
analysis execution 与实现校验，会读取既有 revealed observations 并消耗计算资源，但不创建新的
科学 RunSpec/RunAttempt。真正新增的科研信息来自 M5B 的同状态干预和 M5C/M5D 的独立确认。

## M4：保留阴性 screening terminal

### 目标

把已完成的 M4 作为“哪些组合不能直接进入优越性确认”的前瞻筛选证据，而不是待修复的失败。

### 保留事项

- Gate 3 继续为 `STOP`，冻结 M5 winner matrix 继续为空；
- RF+Uncertainty 与 XGBoost+UCB 仅作为两种诊断性 contrast，不升级为 retained winner；
- Random 与 Greedy 继续作为不可删除的强基线；
- 既有 344 attempts、terminal、checksums、provenance 与失败分析均保持不可变；
- M4 不新增 seed、task、模型、beta 或 threshold。

### 完成条件

M4 已完成。任何后续实验使用新的 M5/M6 protocol identity，不得伪装为 M4 Gate 3 通过或
追溯性修改 M4 terminal。

## M5A：逐轮 prediction→decision 机制测量

### 目的

补齐现有轨迹未持久化的逐轮中间机制，判断首批观察到的脱节或冗余是否持续、累积、收敛或
发生阶段性变化。M5A 不再重复 H1–H7，也不声称单独完成因果识别。

M5A 是一次新的只读 analysis execution，不是“零运行”。它允许读取成功 trajectory 中已经由
历史 benchmark attempt 揭示的 `Observation` 及其 label，并重新拟合/评分历史状态；它不允许
新的 oracle reveal、未揭示 hidden truth 或 held-out access。

### 最小动作

从 344 个成功 M4 attempts 中 exact-select 160 条 M5A 相关轨迹：80 条已选择 exploration
（RF+Uncertainty 与 XGBoost+UCB）加同 task/model/initial/seed 的 80 条 Greedy。其余 120 条
Random、40 条 Ridge Greedy 与 24 条已淘汰 Ridge exploration 只核验 identity/exclusion count，
不读取 trajectory payload，也不重新拟合。对 160 条相关轨迹逐轮复用：

1. 已揭示 observations；
2. exact observable feature、model config 与 acquisition config；
3. 同一 round 的 remaining candidate universe；
4. 冻结 seed、tie-break 与 batch size。

在每个历史 observation state 上重新拟合相同模型，并同时重建：

- predictive mean ranking；
- uncertainty ranking；
- UCB/acquisition ranking；
- Greedy top-k；
- Exploration top-k；
- 记录中的实际 selected batch。

### 输出

只输出聚合、无 candidate ID、无 raw label 的机制证据：

- actual selection 与 frozen acquisition top-k 的 exact-match；
- predictive-mean 与 acquisition ranking 的相关性；
- Exploration–Greedy top-k overlap/Jaccard；
- selected batch 的 predictive-mean rank 与 uncertainty rank 分布摘要；
- divergence 随 round、revealed budget、task、model、initial size 和 seed 的变化；
- 与既有 checkpoint decision-metric difference 的描述性配对关系。

### 完成条件

- 160 条相关轨迹 exact cover，且 184 条非目标成功 attempt exclusion exact cover；不纳入失败、
  无 `_SUCCESS` attempt 或任何非目标 trajectory payload；
- 实际 selected batch 必须可由 frozen config 确定性重建；
- 两次独立聚合结果 byte-identical；
- exact-bind analysis source commit/tree、runtime environment、input inventory/checksums、report
  bytes/checksum 与 resource terminal；
- 正式执行前完成最小 resource probe，并前瞻冻结 wall/RSS/scratch/network/cost caps；
- 允许读取既有 revealed observations；保持 `0 new RunSpec / 0 new benchmark attempt /
  0 new oracle reveal / 0 unrevealed hidden-truth or held-out access`；
- 不产生新的科学 RunAttempt 或 COS 对象。

### 停止条件

完成一份逐轮机制报告后立即停止。若实际选择无法重建，M5B 暂停并先处理该 conclusion-invalidating
问题；不得用更多 seed 绕过。若结果只重复首批结论，仍视为完成，不继续追加统计口径。

## M5B：同状态单步分支干预

### 目的

在模型、已观察数据、候选池和预算完全相同的状态下，只改变 acquisition 决策，区分：

1. Greedy 行为冗余；
2. 无信息偏离；
3. 有信息但机会成本过高；
4. 条件性正收益。

### 分支设计

每个冻结 observation state 只比较下一批，不立即延伸为完整多轮轨迹：

```text
同一个 immutable model＋observation prefix＋remaining pool
├─ 隔离分支 G：Greedy predictive-mean top-k
├─ 隔离分支 E：Exploration Uncertainty 或 UCB top-k
└─ 隔离分支 M：固定保留 E ∩ G，再随机选择其余偏离部分
```

三个分支必须分别从同一个 immutable observation prefix 建立 branch-local evaluator/HiddenOracle
state；不得依次复用同一个有状态 HiddenOracle。任一分支的 reveal 不得改变另外两个分支的
revealed state、remaining pool 或 evaluator 输入。

令 `E` 为 Exploration top-k、`G` 为 Greedy top-k、batch size 为 `k`。Matched Random 必须固定
保留 exact `E ∩ G`，再从 `remaining_pool − G` 中按预冻结 seed 抽取
`k − |E ∩ G|` 个候选；不得重新抽取 Greedy 内成员。matched-random replicate 数、seed 派生与
tie-break 必须在读取任何分支结果前冻结。replicate 先在 observation state 内聚合；replicate、
branch 和 seed 均不作为独立 assay 样本。

### 方法范围

- surrogate 只保留 Random Forest 与 XGBoost；
- acquisition 只保留 Greedy、RF+Uncertainty、XGBoost+UCB 和对应 Matched Random；
- Ridge exploration 已被 M4 淘汰，不恢复；
- 不增加 beta 网格、EI、Thompson Sampling、diversity 或新模型；
- Alpha/IRED 只承担 exploratory mechanism identification。

### evaluator 输出

hidden oracle/evaluator 只揭示分支所选批次并输出聚合指标：

- batch high-value discovery count/recall increment；
- simple-regret reduction；
- best-found improvement；
- `Exploration − Greedy` 配对差；
- `Exploration − Matched Random` 配对差；
- undefined、tie 与 sensitivity-definition 状态。

原始 truth、高价值 membership、candidate ID 与 score distribution 不进入公开聚合报告。

M5B 只识别“冻结 observation state 下下一批决策的 immediate batch effect”。单批结果不能被
表述为完整多轮 active-learning trajectory 的长期因果收益；只有满足前瞻继续条件后，才能另行
进入 bounded multi-round confirmation。

### 机制判定

具体置信区间、等价界值和最小效应在 M5B protocol Issue 中前瞻冻结。方向解释固定如下：

| 结果模式 | 机制解释 | 后续动作 |
|---|---|---|
| Exploration 与 Greedy 高度重合且效果等价 | 行为冗余 | 停止该 acquisition 扩量 |
| Exploration 接近 Matched Random 且弱于 Greedy | 无信息偏离 | 停止该 UQ acquisition 路线 |
| Exploration 优于 Matched Random、但不优于 Greedy | 有信息但探索机会成本过高 | 最多允许一个前瞻冻结的平衡方法小实验 |
| Exploration 同时优于 Matched Random 与 Greedy | 条件性正收益 | 进入独立 assay 和 bounded multi-round confirmation |
| task/initial 间方向翻转 | 条件异质性 | 进入分层 failure map，不合并为全局排名 |

### 阶段与止损

1. synthetic fixture：验证三分支共同状态、matched-overlap 与 evaluator 边界；
2. Alpha/IRED：优先复用既有 80 个 model-matched first-decision states；
3. 只有分支实现和统计口径通过后，才在 ProteinGym 使用相同冻结 protocol；
4. 不因 Alpha/IRED 的某个正向结果自动运行完整多轮 replay；
5. 若结果仍不能区分上述机制，停止同任务归因，转入独立 assay 条件化报告。

## M5C：ProteinGym 独立 assay 前瞻确认

### 目的

增加真正独立的 assay-level 统计单位，检验 M5B 机制是否跨景观保持。#11 在一个统一 data-readiness
切片中闭合 task-level license、raw/manifest、candidate/hidden-truth separation、score coverage 和
单一 `candidate-pool` membership；M5C 实际使用前仍须另行冻结实验 split/leakage、代理身份和
held-out 边界。

### 固定 prospective cohort

M5C 使用 Issue #11 已冻结并在 [comment 5336871661](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11#issuecomment-5336871661)
确认的完整四-assay cohort，不再从中选择 2–3 个：

- `BLAT_ECOLX_Firnberg_2014`；
- `CAPSD_AAV2S_Sinai_2021`；
- `GFP_AEQVI_Sarkisyan_2016`；
- `SPG1_STRSG_Olson_2014`。

每个 assay 保持独立 task identity、truth/score artifact、manifest chain、processing/split eligibility
与结果报告。只有在读取任何模型/acquisition 结果、high-value membership 或 held-out 结果前，
预冻结的 source/provenance/license、candidate/label semantics、truth-score coverage、split/leakage
eligibility 或 immutable identity/readback 检查失败时才允许排除，并必须保留失败记录。不得用模型
表现、high-value hit rate 或结果方向决定纳入。

### 执行顺序

1. 由 #11 的一个统一实现完成 exact 四-assay selective acquisition、四套 per-assay Dataset /
   Processing / candidate-pool Split manifests、immutable publication/readback 与 ingest attempts；
2. BLAT 在同一程序中先行，失败即 fail closed，成功则自动继续其余三个 assay，不增加人工 Gate；
3. 在 M5C Issue 中前瞻冻结实验 split/leakage、zero-shot proxy identity、coverage eligibility 与
   observable feature family，再完成最小 M2 static baseline；
4. 在同一 M5C Issue 中运行 M5B 单步三分支 confirmation；
5. 仅当预先定义的机制类别在独立 assay 中可复现时，选择每类最多一个代表 assay 进入多轮确认；
6. seed 用于配对不确定性，不替代 assay-level 汇总。

### 完成条件

- 四-assay cohort、eligibility rules 与协议先于结果冻结；
- 报告 assay-level effect、置信区间和异质性，不把 seed 当独立 task；
- exploratory Alpha/IRED 与 confirmatory ProteinGym 明确分层；
- 无论正负均按冻结规则保留；任何 eligibility exclusion 保留失败证据，不通过删除或追加 assay
  隐藏方向翻转。

## M5D：最小 SKEMPI 2.0 external validation

### 目的

回答已冻结机制能否跨 protein complex 保持，而不是在 SKEMPI 上重新选择方法。

### 最小范围

- 独立 processing Issue 完成 mutation/chain normalization、label direction、duplicate 与许可边界；
- 独立 split/external-validation protocol 保持 complex/protein-pair leakage-safe；
- 只使用最小表格/突变级 observable feature；
- 不引入 complex-level normalization、深结构特征、FoldX、Rosetta 或结构能量计算；
- 只评估 M5C 前已冻结的方法、指标和机制判定。

### 完成条件

- external task 不参与模型、策略、threshold 或 cost ratio 选择；
- RunSpec、attempt、manifest chain 与结果 exact-bound；
- 结论限定为 external task evidence，不用一次 SKEMPI 结果声称普遍性；
- 正、负或方向翻转均进入最终报告。

## M6：E4/E5 多保真条件分析

M6 不是 M4 的救援性扩量。它回答固定总成本下低保真信息何时产生正收益、无收益或负收益。

### E4：可控多保真

使用公开高保真真值可复现地产生低保真代理。最小设计只保留能区分机制的少量条件轴：

- 高低保真整体相关性；
- 高价值区域局部偏差或排序反转；
- 成本比；
- 必要时加入一个冻结噪声水平。

比较至少包括：只买高保真、固定比例、硬预筛和成本感知联合选择。具体 levels、seed、预算和
停止界值必须在读取结果前冻结，不运行默认完整笛卡尔积。

### E5：ProteinGym 真实代理

在已完成 M5C onboarding 的 assay 上，将公开 zero-shot score 作为真实低保真代理、DMS 标签作为
高保真真值。先冻结代理 identity、覆盖率、方向、missing policy 与 cost model，再运行策略比较。
不得根据 DMS held-out 表现选择代理、阈值或 cost ratio。

### M6 输出

- fixed-cost simple regret 与 high-value recall；
- cost-to-target、节省率和负收益概率；
- 高值区 ranking distortion；
- correlation × bias × cost 的条件相图；
- E4 可控条件与 E5 真实代理的对应与不一致。

### 完成条件

- 单保真 M5 机制和统计口径已经冻结；
- E4/E5 的 cost model、代理与 evaluator 边界可审计；
- 不把低保真平均相关性解释为高值区决策价值；
- 多保真负收益也是完整结果，不追加条件直到出现阳性。

## M7：证据冻结与论文结果

### 进入条件

- M4 immutable terminal 保持完整；
- M5A 逐轮机制重建完成；
- M5B 同状态分支结果完成；
- M5C 对全部四个 prospective assays 完成 confirmation，或在任何结果读取前按冻结 eligibility
  rules 记录不可纳入的失败证据；
- M5D 最小 SKEMPI external validation 完成；
- M6 E4 完成，E5 在其 task-level 数据边界满足后完成或明确记录为未完成范围。

### 主要交付物

- checksum-linked RunSpec/attempt/release manifest；
- M4 多轮 screening 主表与失败概率；
- prediction→decision 逐轮关系图；
- Greedy/Exploration/Matched Random 同状态机制图；
- task/assay-level effect、配对置信区间和异质性图；
- ProteinGym confirmatory 与 SKEMPI external-validation 分层结果；
- E4/E5 多保真条件相图；
- 排除、失败、资源、许可、未完成项与声明边界。

### 允许的论文主张

- prediction quality 不自动推出 decision value；
- exploration 可能表现为 Greedy 冗余、无信息偏离、机会成本或条件性正收益；
- 机制是否跨 task/assay/complex 保持必须由对应独立证据限定；
- 多保真价值依赖高值区排序、偏差与成本条件。

不得在没有 task/assay-level 支持时声称全局最优策略，也不得把 CI、server execution、seed 数量
或单一 external task 当作 scientific gate。

## Issue 与工程复杂度控制

默认只保留下列必要执行单元：

1. 一个 M5 主 Issue：覆盖 M5A 新只读 analysis execution 和 M5B Alpha/IRED 同状态分支；
2. ProteinGym 保持 #11 一个 acquisition＋data-readiness 统一切片（四套 per-assay manifests）＋
   一个 M5C experiment/confirmation Issue；不默认每 assay 新建 Issue，也不为 BLAT 另设人工 Gate；
3. 一个最小 SKEMPI processing/split Issue 和一个 external execution Issue；若合同允许合并则优先合并；
4. 一个 M6 主 Issue，E4/E5 只有在数据授权边界不同且无法同一审批时才拆分；
5. 一个 M7 freeze/release Issue。

不因分析结果自动创建新模型、框架、Schema、服务、VM、Docker、CI Gate 或子 Issue。新依赖或
持久化契约必须由真实失败、结论有效性或明确用户要求直接证明必要。

## 资源与授权边界

- M5A：新的只读 analysis execution；读取既有 revealed observations；`0 new RunSpec /
  0 new benchmark attempt / 0 new oracle reveal / 0 unrevealed hidden-truth or held-out access`；
  exact source/environment/input/report checksum 与 resource probe/caps 在执行前冻结；0 COS write；
- M5B Alpha/IRED：先使用既有 first-decision states；任何新 truth-bearing branch evaluation 在
  protocol review 和明确启动授权前保持 0；
- ProteinGym #11 data-readiness 使用已冻结的 550,000,000 B network、2.5 GiB scratch 和 2.0 GB
  decimal COS caps；M5C、SKEMPI、M6 的实验计算仍先做单任务/单条件 resource probe，不提前承诺
  全矩阵；
- 所有正式运行均保持一次手动启动、无自动 retry/expansion、成功产物不可覆盖；
- 任何 held-out、external validation、COS 写入、云资源或凭据使用仍需对应独立授权；
- 本规划、实现、CI、server execution、benchmark evidence 与 scientific gate 分别报告。

## 全局停止与转向条件

- 若 M5A 无法确定性重建实际选择，暂停科学解释并修复该有效性问题；
- 若 M5B 仍不能区分 Greedy 冗余与随机偏离，停止 Alpha/IRED 原因深挖；
- 若 independent assays 全部显示无信息偏离或 Greedy 优势，冻结强阴性结论，不搜索新 winner；
- 若收益只出现在预先声明的有限条件，进入条件化结论，不扩大为全局声明；
- 若 task/assay 方向相反，按层报告，不用更多 seed 稀释异质性；
- 若许可、split/leakage、truth separation 或 provenance 失效，对应数据和 attempt fail closed；
- 若新增工程消耗超过最小实验本身，暂停 hardening 并回到可观察科研问题。

## 后续仍需前瞻确认的参数

本文冻结方向和阶段边界，不冻结下列实验参数：

- ProteinGym 四 assay 的 data-readiness 基本 candidate/label/score-coverage 规则由 #11 统一实现；
  M5C 的实验 split/leakage、coverage eligibility threshold 与 proxy identity 仍需前瞻冻结；cohort
  名单本文件已冻结，不再作为可选参数；
- M5B 等价界值、置信区间方法、matched-random 重复数和 seed 派生；
- 是否以及何时进入 bounded multi-round confirmation；
- SKEMPI label/candidate/split 的精确定义；
- E4 correlation、bias、noise、cost-ratio levels；
- E5 zero-shot proxy identity 与 cost model；
- 各阶段正式 RunSpec 数、服务器、网络、COS、时间和资金 cap。

这些参数必须在对应 Issue 中基于公开元数据、最小 preflight 和结果读取前证据冻结。本文件本身不
授权选择、执行或发布。
