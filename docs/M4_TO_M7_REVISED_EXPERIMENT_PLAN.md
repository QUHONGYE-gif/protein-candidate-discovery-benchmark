# M4 至 M7 修订实验方案

## 文件职责与生效边界

本文是在 M4 已产生正式阴性筛选结果后建立的 M4→M7 新实验规划。它不删除、不覆盖、
不原地修改 [`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md) 或任何已冻结协议、RunSpec、
成功 attempt 与审核记录。原路线继续作为历史规划基线；本文经审核合并后，作为 M4 之后的
执行补充与后续 Issue 设计依据。

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
| #83 H1–H7 failure analysis | H2/H4/H5/H7 `SUPPORTED`，H1/H3/H6 `INCONCLUSIVE`；多个失败现象共同存在 | 只用于提出机制假设，不作为因果确认 |
| #85 28-run seed-source tracer | `MIXED-INCONCLUSIVE`；只在 Alpha/simple-regret 观察到 initial-dominant | 不继续用 seed 扩量代替 task/assay 扩展 |
| H1 修正只读分析 | 两个 task 均 0/4 支持，8 个 cell 的变异未随标签增加下降 | 标签稀少保留为可能放大因素，不作为首要补救方向 |
| H3/H6 首批只读分析 | 79/80 Exploration–Greedy next-batch 不同；78/80 Exploration 至少与 Greedy 的相似度不低于 Random | 证明首批存在 ranking→decision 脱节和部分冗余，尚未形成完整逐轮机制或因果归因 |

344 个 attempts 不能被解释为 344 个独立科研样本。跨任务结论的统计单位仍然是 task、assay、
protein 或 complex；seed 只用于估计算法随机性。

## 总体执行顺序

```text
保留 M4 Gate 3 STOP
→ M5A：既有轨迹逐轮机制测量（零新运行）
→ M5B：同状态单步分支干预（Alpha/IRED 探索）
→ M5C：ProteinGym 独立 assay 前瞻确认
→ M5D：最小 SKEMPI 2.0 external validation
→ M6：E4 可控多保真＋E5 ProteinGym 真实代理
→ M7：证据冻结、统计汇总与论文产物
```

M5A 不是独立的大型科研阶段，不单独增加模型、task、seed、基础设施或审批链。它是 M5B 的
零运行测量与实现校验。真正新增的科研信息来自 M5B 的同状态干预和 M5C/M5D 的独立确认。

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

### 最小动作

对现有 344 条成功轨迹逐轮复用：

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

- 344 条轨迹 exact cover，不纳入失败或无 `_SUCCESS` attempt；
- 实际 selected batch 必须可由 frozen config 确定性重建；
- 两次独立聚合结果 byte-identical；
- 不读取新的 hidden truth、held-out 或未揭示标签；
- 不产生新 RunSpec、attempt 或 COS 对象。

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
相同 model＋observation prefix＋remaining pool
├─ Greedy：predictive mean top-k
├─ Exploration：Uncertainty 或 UCB top-k
└─ Matched Random：保持与 Exploration 相同的 Greedy-overlap，随机选择偏离部分
```

若 Exploration 与 Greedy 的 top-k overlap 为 `o/k`，Matched Random 必须从 Greedy top-k 中
确定性抽取 `o` 个、从其外部抽取 `k-o` 个。其 seed 派生与 tie-break 在读取任何分支结果前冻结。

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

增加真正独立的 assay-level 统计单位，检验 M5B 机制是否跨景观保持。ProteinGym 数据准备不是
本方案的科学阻塞假设，但任何实际使用仍必须先闭合 task-level license、manifest、processing、
split/leakage 和 held-out 边界。

### assay 选择

- 在读取策略结果前，根据公开元数据前瞻选择 2–3 个 assay；
- 选择规则可使用候选规模、突变深度、标签完整度、蛋白/assay 类型和可形成泄漏安全 split 的能力；
- 不根据模型预测、acquisition 表现、high-value hit rate 或 held-out 结果选择 assay；
- 每个 assay 保持独立 manifest chain 和 task identity。

### 执行顺序

1. 完成 task-level onboarding 与 Gate 1；
2. 在相同 observable feature family 上完成最小 M2 static baseline；
3. 运行 M5B 单步三分支 confirmation；
4. 仅当预先定义的机制类别在独立 assay 中可复现时，选择每类最多一个代表 assay 进入多轮确认；
5. seed 用于配对不确定性，不替代 assay-level 汇总。

### 完成条件

- assay 选择与协议先于结果冻结；
- 报告 assay-level effect、置信区间和异质性，不把 seed 当独立 task；
- exploratory Alpha/IRED 与 confirmatory ProteinGym 明确分层；
- 无论正负均按冻结规则保留，不通过追加 assay 隐藏方向翻转。

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
- M5C 至少完成前瞻独立 assay confirmation；
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

1. 一个 M5 主 Issue：覆盖 M5A 零运行测量和 M5B Alpha/IRED 同状态分支；
2. ProteinGym 每个被选 assay 的必要 onboarding Issue；能安全共享的 M5C 执行保持一个主 Issue；
3. 一个最小 SKEMPI processing/split Issue 和一个 external execution Issue；若合同允许合并则优先合并；
4. 一个 M6 主 Issue，E4/E5 只有在数据授权边界不同且无法同一审批时才拆分；
5. 一个 M7 freeze/release Issue。

不因分析结果自动创建新模型、框架、Schema、服务、VM、Docker、CI Gate 或子 Issue。新依赖或
持久化契约必须由真实失败、结论有效性或明确用户要求直接证明必要。

## 资源与授权边界

- M5A：0 新 RunSpec、0 attempt、0 truth/held-out read、0 COS write；
- M5B Alpha/IRED：先使用既有 first-decision states；任何新 truth-bearing branch evaluation 在
  protocol review 和明确启动授权前保持 0；
- ProteinGym、SKEMPI、M6：先做单任务/单条件 resource probe，再冻结正式 cap，不提前承诺全矩阵；
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

- ProteinGym assay 的精确名单和选择规则；
- M5B 等价界值、置信区间方法、matched-random 重复数和 seed 派生；
- 是否以及何时进入 bounded multi-round confirmation；
- SKEMPI label/candidate/split 的精确定义；
- E4 correlation、bias、noise、cost-ratio levels；
- E5 zero-shot proxy identity 与 cost model；
- 各阶段正式 RunSpec 数、服务器、网络、COS、时间和资金 cap。

这些参数必须在对应 Issue 中基于公开元数据、最小 preflight 和结果读取前证据冻结。本文件本身不
授权选择、执行或发布。
