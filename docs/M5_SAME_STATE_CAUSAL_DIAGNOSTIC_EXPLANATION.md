对，之前已经做了充分的“事后归因分析”。#83、#85 和刚才的 H1/H3/H6 验证回答的是：

> 哪些现象与 M4 失败同时出现？

因为这些因素在同一批轨迹里耦合，最终只能得到“随机性、任务异质性、pilot optimism、ranking 脱节和行为冗余共同存在”。继续用同样数据换统计口径，基本不会把它们分开。

所以我修正上一版建议：**不应把 M5A、M5B 做成两个大型阶段。M5A 只保留为 M5B 内部的零运行测量步骤；真正有科研增量的是 M5B 的同状态干预实验和独立 assay 确认。**

## M5A：补齐逐轮机制测量

它不是再次分析 H1–H7，而是测量当前轨迹里缺失的中间过程。

### 主要目的

现在只知道：

- 首个决策批次有 79/80 不同；
- 最终 RF+Uncertainty 在 Alpha 上有害；
- XGBoost+UCB 经常接近 Greedy。

但不知道这种差异是：

- 第一轮出现后逐渐消失；
- 随预算不断累积；
- 某一轮突然发生；
- 还是 acquisition 排名一直不同，但最终选择因候选池收缩而趋同。

### 怎么做

复用现有 344 条轨迹中的已揭示 observations。在每个历史决策状态：

1. 用同一模型、同一已揭示标签重新拟合；
2. 重建全候选 predictive mean、uncertainty 和 acquisition score；
3. 在完全相同状态下同时计算：
   - Greedy top-k；
   - Uncertainty/UCB top-k；
   - 实际记录的选择；
4. 输出聚合指标：
   - mean ranking 与 acquisition ranking 的相关性；
   - Exploration–Greedy top-k overlap；
   - 被选对象的 prediction rank、uncertainty rank；
   - divergence 随预算的变化。

不新增 RunSpec，不重新调用 oracle，也不读取新的 hidden truth。

### 预期结果

它只能给出机制轨迹，不能单独形成因果结论：

| 结果 | 解释 |
|---|---|
| 全程高 overlap | acquisition 功能上冗余 |
| 前期偏离、后期收敛 | exploration 只影响早期路径 |
| 全程强偏离且最终更差 | 持续错误方向探索 |
| 偏离程度随 task/initial 翻转 | 条件异质性是主线 |

由于第一批和最终结果已经比较明确，M5A 的增量价值有限。**它最多是一张论文机制图和 M5B 的实现校验，不应单独建大型 Gate、扩 seed 或形成新的原因分析 Issue。**

## M5B：同状态分支干预实验

这是补救论文证据的核心。它回答：

> 在模型、已观察数据和候选池完全相同的情况下，仅改变 acquisition 的决策，结果究竟会怎样？

### 怎么做

对每个冻结的共同状态，只执行下一批选择，不先跑完整轨迹：

```text
完全相同的模型与 observation prefix
├─ Greedy：predictive mean top-k
├─ Exploration：Uncertainty 或 UCB top-k
└─ Matched Random：与 Exploration 保持相同的 Greedy-overlap，但随机选择偏离部分
```

“Matched Random”非常关键。例如 Exploration 与 Greedy 只重合 4/16，那么随机对照也固定选取：

- 4 个 Greedy top-k 内候选；
- 12 个 Greedy top-k 外候选。

这样能区分：

- acquisition 确实利用了有价值的信息；
- 还是只因为“选择得与 Greedy 不同”。

hidden oracle 只在 evaluator 内揭示三个分支所选批次，外部只输出：

- 本批新增 high-value 数量/recall；
- simple-regret reduction；
- best-found improvement；
- Exploration 相对 Greedy 的增量；
- Exploration 相对 matched-random 的增量。

第一阶段只比较一个 batch，避免后续状态差异继续累积并重新混杂原因。只有单步实验出现稳定信息，才进入完整多轮 replay。

### 如何使用 ProteinGym

既然 ProteinGym 的阻塞可以很快解决，就不必把 Nuclease B 作为唯一新增主任务。建议：

- Alpha/IRED：只作为发现假设的 exploratory tasks；
- 前瞻选择 2–3 个 ProteinGym assay：作为独立 confirmation tasks；
- assay 选择只能依据结果读取前的公开元数据，例如候选规模、突变深度、标签完整度和景观覆盖，不能依据策略表现；
- SKEMPI 仍保留为最终跨 complex external validation。

这样才能把统计单位从 2 个 task 提升到多个独立 assay，而不是把 344 个 seed-run 错当成 344 个独立样本。

### M5B 的结果如何解释

| 观察结果 | 可以支持的机制结论 | 下一步 |
|---|---|---|
| Exploration ≈ Greedy | acquisition 行为冗余 | 不再调该策略 |
| Exploration ≈ matched Random，且弱于 Greedy | uncertainty 偏离没有决策信息 | 停止该 UQ 路线 |
| Exploration 优于 matched Random、仍弱于 Greedy | UQ 有信息，但探索机会成本过高 | 只允许一个前瞻冻结的平衡方法 |
| Exploration 同时优于 matched Random 和 Greedy | 找到条件性正收益区域 | 才进入多轮 confirmation |
| 不同 assay 方向翻转 | task/景观条件决定策略有效性 | 形成条件化 failure map |
| 所有独立 assay 都弱于 Greedy | 强阴性但可发表：复杂探索缺乏普遍价值 | 冻结负结果，不再搜索赢家 |

这比再做一轮相关性分析强得多，因为它真正固定了共同状态，并通过 matched-random 分支隔离“有信息的探索”与“单纯不同”。

## Decision Guard

### 1. 决策层级与成功标准

- 决策层级：研究方法。
- 真正要决定的问题：如何从多因素相关推进到可区分的 acquisition 因果机制。
- 成功标准：同状态分支可以明确区分冗余、无信息偏离、机会成本和条件性正收益。
- 已确认约束：不改写 M4 STOP，不用 held-out 选 assay/阈值/策略。

### 2. 事实、推断与未知

| 项目 | 状态 | 证据或缺口 |
|---|---|---|
| 既有多原因分析已基本穷尽 | 事实 | H1–H7、28-run tracer、首批 ranking audit 已完成 |
| 逐轮 ranking 尚未测量 | 事实 | 轨迹未持久化完整分数 |
| 不同 acquisition 的因果价值 | 未知 | 后续轨迹状态不同，现有比较仍有路径混杂 |
| 跨 assay 稳定性 | 未知 | 当前只有两个 task |

### 3. 当前选项与隐藏选项

| 选项 | 来源逻辑 | 优点 | 主要风险 | 可逆性 |
|---|---|---|---|---|
| 继续原因分析 | 延续现有路线 | 便宜 | 大概率仍是多因素结论 | 高 |
| 直接完整扩 task/seed | 增加样本 | 结果更多 | 不隔离原因、成本高 | 中 |
| 同状态单步分支＋独立 assay | 因果隔离 | 信息量最高 | 需新协议 | 高 |
| 大范围方法搜索 | 寻找阳性 | 可能找到赢家 | 严重事后选择 | 低 |

### 4. 关键假设

| 假设 | 证据强度 | 若为假会造成什么 | 最早预警 | 最小验证 |
|---|---|---|---|---|
| 分支选择能在同状态重建 | 强 | 实验无法公平比较 | 实际选择与重建 top-k 不一致 | M5A 重建 |
| matched-random 能隔离偏离价值 | 中 | 仍有混杂 | 对照 overlap 不一致 | 合成 fixture |
| ProteinGym 模式能确认跨 assay 结论 | 未知 | 仍只能作 task-level 声明 | assay 间完全异质 | 2–3 assay pilot |

### 5. 最小信息实验

- 要区分的假设：有信息探索、无信息偏离、Greedy 冗余。
- 最小动作：M5A 重建校验＋相同初始状态的一批三分支比较。
- 最大成本：先只做 Alpha/IRED 既有状态；不运行完整轨迹。
- 预期证据：Exploration 相对 Greedy 和 matched-random 的配对批次增量。
- 成功意味着：至少能把失败归入上述三类之一。
- 失败意味着：停止同任务机制深挖，直接依赖独立 assay 的条件化结果。
- 结果不明确时：不扩大 seed，增加独立 assay。

### 6. 损失上限与不可触碰边界

- 一个 M5 主 Issue；M5A 不单独建 Issue/Gate。
- 首轮只做一个 batch，不做完整多轮放大。
- ProteinGym 在合法 onboarding 后使用，不绕过许可、manifest、split/leakage 门禁。
- 不新增模型网格、beta 网格或基础设施。

### 7. 继续、暂停、停止与转向条件

- 继续：同状态重建准确，分支结果能区分机制。
- 暂停：实际选择无法复现或共同状态不一致。
- 停止：新分析仍只得到不可区分的“多因素共同作用”。
- 转向：在 ProteinGym 上做前瞻条件确认，而不是继续 Alpha/IRED 归因。

### 8. 反证、建议与待确认决定

- 会推翻当前建议的新证据：现有轨迹已经包含完整逐轮分支或等价的同状态 counterfactual。
- 建议：`PROCEED WITH CAP`
- 建议置信度：高。
- 未解决问题：ProteinGym assay 的前瞻选择规则。
- 需要用户确认的决定：将 M5A 压缩为 M5B 的零运行校验，真正的新实验采用“同状态 Greedy/Exploration/matched-random 单步分支＋ProteinGym 独立确认”。

换句话说：**之前的工作是在列出可能原因；新的 M5 应该通过干预把这些原因拆开。** 如果不做这种因果分支，而只是继续分析现有结果，就没有必要再设 M5A/M5B。
