# 项目背景与研究定位

## 文件职责

本文说明项目为什么存在、研究什么，以及哪些结论属于本项目能够支持的范围。领域术语与不变量
以 [`CONTEXT.md`](../CONTEXT.md) 为准；数据、回放、指标和 held-out 规则以
[`BENCHMARK_CONTRACT.md`](BENCHMARK_CONTRACT.md) 为准。本文不冻结具体数据集、模型、阈值、
成本或实验网格。

## 背景

蛋白候选发现通常面对一个有限候选池：可以计算的候选很多，获得可靠实验标签的成本却很高。
实际决策问题因此不是单纯预测所有候选，而是在很少的初始观测和固定预算下，决定下一批应测量
哪些候选，并在必要时决定购买哪一级保真度的标签。

一个平均预测误差更低的模型不一定能更快找到高价值候选。排序尾部的局部错误、不可靠的不确定
性、高值样本稀疏、初始样本偏置和分布偏移，都可能使预测排行榜与发现排行榜发生逆转。反过来，
简单的 Greedy 策略在平滑且高值区域较密集的景观上可能已经足够强。Benchmark 必须把这些情况
作为待检验问题，而不是预先假定复杂模型或探索策略一定优胜。

## 决策问题

项目研究以下闭环：

```text
公开候选池
→ 少量初始观测
→ 拟合 surrogate 并估计 uncertainty
→ acquisition 选择下一批 candidate ID
→ hidden oracle 只揭示被选候选的公开真值
→ 追加不可变 trajectory 记录
→ 在标签或成本预算耗尽前重复
```

多保真设置还需要联合决定候选与标签级别。总成本按冻结的 cost model 累计，低保真信息只有在
固定总成本下改善高保真发现结果，才被视为有决策价值。

## 核心研究问题

1. Prediction metrics 与 decision metrics 在何种条件下一致或背离？
2. 初始样本量、高值稀疏度、噪声、景观平滑性和分布偏移如何改变策略排序？
3. Greedy、Random、探索型和多样性约束策略分别适用于哪些可识别场景？
4. 不确定性校准是否能预测 UCB、EI、Thompson Sampling 等策略的实际收益？
5. 低保真相关性、局部偏差、覆盖率和成本比在什么区域产生正收益或负收益？
6. 结论能否跨 assay、protein 或 complex 保持，而不是只在随机 seed 层面成立？

## 数据集角色

数据源按角色而不是按规模进入项目。每个 task 或 assay 都必须通过独立的 dataset onboarding
Issue、许可核验和 manifest chain，不能因为数据源公开而批量授权使用。

| 数据源 | 预期角色 | 进入主实验前的必要条件 |
|---|---|---|
| FLIP2 | 小样本景观与分布偏移任务 | task-level 来源、许可、字段、duplicate 和 split 审计 |
| ProteinGym | DMS 高保真标签与公开 zero-shot 低保真代理 | assay 选择规则、方向、覆盖率和 held-out 边界冻结 |
| SKEMPI 2.0 | 最小、可审计的蛋白复合物 external validation | task-level 许可与字段审计、候选定义和 external-validation protocol 冻结 |
| 可控模拟标签 | 多保真相关性、偏差、噪声和成本敏感性 | 由公开真值可复现生成，参数在结果读取前冻结 |

第一阶段目标是形成少量、互补、审计完整的代表任务，其中包括最小 SKEMPI 2.0 external
validation；不追求一次覆盖数据源的全部 assay。SKEMPI complex-level normalization、深结构特征、
FoldX/Rosetta 等属于后续增强，不与最小 external validation 混为同一范围。

## 预期贡献

- 在固定标签预算和固定总成本下，比较模型—获取策略组合的发现效率；
- 定量分析预测质量、不确定性校准与决策价值之间的关系；
- 给出低保真信息有效、无效和产生负收益的经验边界；
- 形成按景观和预算条件组织的选择指南，而不是单一总排行榜；
- 提供 manifest-driven、hidden-label-safe、可中断恢复且可追溯的公开回放框架。

## 非目标

当前主线不包括：

- 从零训练大型蛋白基础模型；
- 生成或筛选任何私有蛋白序列；
- 使用内部靶点、结构、模型输出、MD 或湿实验结果；
- 用 held-out 结果反向选择 feature、模型、策略、阈值或 cost ratio；
- 以覆盖所有公开 assay、最大模型规模或完整笛卡尔积作为贡献；
- 把 CI、一次服务器运行或单一任务胜出解释为 scientific gate 通过。

## 证据解释

Implementation、CI、server execution、benchmark evidence 和 scientific gate 是不同状态，
前一状态不自动推出后一状态。各状态的完成定义和阶段 Gate 见
[`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md)，完整 provenance 与冻结规则见
[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md)，公开与私有信息边界见
[`PRIVACY_BOUNDARY.md`](PRIVACY_BOUNDARY.md)。
