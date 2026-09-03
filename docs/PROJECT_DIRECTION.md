# 项目方向与范围治理

## 文件职责

本文提供跨 Issue 的方向控制：当多个实现、数据或实验选项竞争资源时，用一致的原则决定先做
什么、何时停止，以及哪些变化必须重新审批。具体时间线见
[`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md)，持久化协议由 accepted ADR 和 Schema 决定。
跨项目当前状态摘要只在 [`STATUS.md`](STATUS.md) 维护；达到独立 Issue 门槛的仓库级任务决策与
lifecycle 以 GitHub Issue 为准，执行归属与阶段可由多个 Multica issues 多对一映射到该 Issue。

### 状态与证据职责

| 文件或系统 | 唯一职责 |
|---|---|
| `README.md` | 稳定项目入口并指向 `STATUS.md` |
| `docs/STATUS.md` | 跨项目 current-state snapshot |
| ADR | 长期架构与治理决策 |
| Experiment contract | 单个研究问题、identity、预算、指标、停止条件与 promotion rule |
| `research_log.jsonl` | runner 产生的 append-only server attempt/event 历史；不是 Issue 评论流 |
| Candidate summary | 等待 accepted/frozen 的紧凑聚合结果候选 |
| COS/frozen manifest | 正式不可变证据与 checksum |
| GitHub Issue | 达到独立门槛的 task-specific 决策、lifecycle 和证据链接；不保存 PID、STS 调试、packet dump 或完整服务器日志 |
| GitHub PR | 代码/文档变更及其验证 |

`STATUS.md` 是带 `Last verified` 的低频科学状态快照，只在 Gate A 触发 scientific state
transition、Gate B 产生 terminal，或 milestone closeout 时更新。普通 PR/CI、单个 run/attempt、
retry、日志、plot、maintenance batch、Issue 评论和 code-review 事件不触发同步。历史计划、Issue
评论和运行记录继续保留各自证据职责，但不维护并行的“当前状态”。

## 北极星目标

在公开、可复现、hidden-label-safe 的条件下，识别哪种 surrogate model、uncertainty method
与 acquisition strategy 组合能在固定标签或成本预算下最有效地发现高价值蛋白候选，并说明
这种结论成立和失效的条件。

## 方向原则

### 1. 决策价值优先

MAE、RMSE、Spearman 和校准指标是诊断信息，不是最终目标。主结果必须包含 best-found value、
simple regret、Top-k recall、success probability、labels-to-target 或 cost-to-target 等决策指标。

### 2. 强基线优先

Random 和 Greedy 是必须保留的基线。复杂策略只有在相同候选池、初始观测和预算下稳定超过强
基线，才能获得更多实验预算。

### 3. 先单保真，再多保真

先验证单保真 replay、UQ 和决策指标，再引入 fidelity 选择。真实或模拟低保真不能绕过高保真
hidden-label 边界，成本比和相关性也不能根据 held-out 结果调节。

### 4. 小而完整优先

优先完成少量 task/assay 的完整 manifest chain、泄漏测试和可复现 pilot，再扩大数据或模型
覆盖。新增 task、模型或策略必须回答它将区分哪一个已有实验无法区分的研究假设。

### 5. 条件化结论优先

项目不寻找不受条件限制的“全局最佳模型”。结论按初始样本量、高值稀疏度、噪声、景观结构、
分布偏移、低保真质量和成本比组织，并报告失败概率与跨任务不确定性。

### 6. 不可变证据优先

正式数据、split、RunSpec、attempt 和 release 使用 checksum-linked manifest。成功运行和冻结结果
不得覆盖；不完整 attempt 不得进入聚合。对象存储规则见 accepted ADR，不以 POSIX 共享盘假设
替代显式上传、回读和完成标记。

### 7. 可证伪优先

每个阶段先写明预期行为、失败标准和停止条件。负结果、Greedy 胜出、多保真负收益或模型排名
不稳定都是有效证据，不通过临时扩大网格来掩盖。

## 优先级

| 优先级 | 内容 | 进入条件 |
|---|---|---|
| P0 | 数据契约、hidden-label 边界、可复现 replay、核心 decision metrics | 无；属于主线基础 |
| P1 | 代表性 FLIP2/ProteinGym 任务、强静态基线、单保真 E1、E2/E3 | 相应数据与 replay Gate 通过 |
| P1 | 可控多保真 E4、ProteinGym 真实代理 E5 | 单保真结果和 cost model 冻结 |
| P1 | 最小、可审计的 SKEMPI 2.0 external validation | task-level onboarding 与 external-validation protocol 冻结 |
| P2 | SKEMPI complex-level normalization、结构特征扩展、更多 assay、conformal、解释模型 | 不阻塞主结果且有独立 Issue |
| P2 | 知识引导粗到细搜索、大型 PLM 重算、FoldX/Rosetta | 主线证据完成后重新评估 |

优先级不是数据访问授权。每个数据集和实验仍需独立 Issue、reviewed PR 与相应 manifest。

## 实验规模控制

实验采用漏斗式扩展：

1. smoke：小型公开 fixture，验证契约、确定性和泄漏边界；
2. pilot：少量代表任务、模型、策略和 seed，测量耗时、失败率与磁盘增长；
3. screening：淘汰不能回答研究问题或明显弱于强基线的组合；
4. confirmation：只对预先选择的关键组合增加 task 和 seed；
5. freeze：按冻结协议生成表、图、统计和 release manifest。

不得默认运行模型 × 策略 × task × 初始规模 × seed 的完整笛卡尔积。Pilot 证据完成前，不冻结
付费 compute、GPU 类型、长期存储配额或总预算。

## 范围变化规则

以下变化需要新的或更新后的 GitHub Issue，并在实现前明确影响：

- 新增 dataset/task/assay、外部服务或生产依赖；
- 改变可观察字段、hidden label、candidate unit 或 duplicate 规则；
- 改变 frozen split、指标、threshold、cost model、seed policy 或 held-out 授权；
- 改变持久化 Schema、对象路径或完成语义；
- 将 P2 增强项提升为主线阻塞项。

若变化修改 accepted ADR 的决策，应新增 superseding ADR，不在原 ADR 上原地重写。

## 风险与响应

| 风险信号 | 默认响应 |
|---|---|
| 复杂策略只超过 Random、未超过 Greedy | 保留结果，停止扩大该组合，分析景观条件 |
| 预测指标改善但决策指标无改善 | 将其作为 E3 主要结果，不包装为发现提升 |
| 低保真在高值区排序反转 | 禁止硬预筛，转入偏差敏感性或停用该代理 |
| 任务间结论方向相反 | 分层报告，不用 seed 数量掩盖 task-level 异质性 |
| duplicate 或 split 泄漏未解决 | fail closed，停止模型和回放实验 |
| provenance、checksum 或 `_SUCCESS` 缺失 | 该 attempt 不进入证据汇总 |
| pilot 资源增长超出预算 | 缩减模型、task 或保存粒度，先保留强基线和关键假设 |
| 许可或再分发边界不明确 | 停止发布原始内容，只保留合法脚本、manifest 和索引 |

## 声明边界

- 单任务结果只能支持 task-level 观察；
- 多 seed 只能估计算法随机性，不能替代独立 assay、protein 或 complex；
- 工程完成、CI green 和 server execution complete 不等于 benchmark evidence complete；
- 主结果冻结前的探索只生成开发证据，不用于事后改变 held-out 协议；
- 任何对外结论必须能回溯到精确 commit、环境、manifest chain、RunSpec 和完整 attempt。
