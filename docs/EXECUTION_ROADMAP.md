# 项目执行路线与初始时间线

## 规划基线

- 基线版本：2026-07-20；
- 初始主实验窗口：2026-07-20 至 2026-08-31；
- 依据：项目整体规划、现有仓库 contracts 与 accepted ADR；
- 管理方式：GitHub Issue 是具体工作的权威记录，本文件只维护阶段顺序、依赖和 Gate。

日期是排期目标，不是数据访问授权、资源采购承诺或 scientific gate 结论。任何阶段未通过退出
Gate 时，后续日期顺延或缩减范围，不以跳过审计补进度。

## 工作状态

每项工作分别记录：

| 状态 | 完成含义 |
|---|---|
| Implementation complete | 当前 Issue 范围内的实现和文档完成 |
| Tests and CI green | 约定的本地检查及 GitHub Actions 通过 |
| Server execution complete | 指定 commit/environment 的正式运行与产物发布完成 |
| Benchmark evidence complete | 冻结的任务、seed、预算和统计证据完整 |
| Scientific gate passed | 预先定义的跨任务证据门槛满足 |

这些状态必须分别更新，不得相互代替。

## 关键路径

```text
治理与基础契约
→ task-level dataset onboarding
→ processing / duplicate audit
→ split / leakage audit
→ 静态基线与 feature contract
→ deterministic replay smoke
→ 单保真 screening
→ 敏感性与预测—决策分析
→ 多保真实验
→ 最小 SKEMPI 2.0 external validation
→ 证据冻结与发布
```

最小、可审计的 SKEMPI 2.0 external validation 属于 initial scope，并在主结果冻结前完成。
SKEMPI complex-level normalization、深结构特征、更多 assay、知识引导搜索和大型模型重算是
增强线，不与该最小验证混为同一范围。

## 阶段与 Gate

### M0：治理、契约与仓库基础

**目标**：建立 package、Schema、manifest、hidden oracle、storage abstraction、CI 和协作规则。

**退出条件**：

- 最小契约和 fail-closed 测试进入 reviewed PR；
- GitHub、COS、compute 和本地 cache 的职责分离；
- 工程状态与科学状态的报告口径建立。

截至规划基线，M0 implementation 与 CI 已通过 PR #2 进入 `main`。这不包含正式 server run 或
scientific evidence。

### M1：数据入库、处理与任务冻结

**计划窗口**：7 月 20–28 日。

**执行单位**：每个 dataset task/assay 一个 onboarding Issue。优先用一个 FLIP2 task 完成
raw-ingest tracer bullet，再依据审计结果决定后续 FLIP2 和 ProteinGym 范围；最小 SKEMPI 2.0
external validation 仍需独立 onboarding Issue，不因属于 initial scope 而跳过许可或字段审计。

**交付物**：

- Dataset Manifest：来源、版本、许可、下载时间、raw checksum 和限制；
- Processing Manifest：字段可见性、label direction、缺失、candidate ID、duplicate 处理；
- Split Manifest：membership、grouping、development/validation/held-out 与 leakage control；
- 数据质量报告和主任务候选清单。

**Gate 1：数据可用性**

- manifest chain 通过 Schema 与领域验证；
- candidate ID 唯一，duplicate/near-duplicate 规则明确；
- hidden label 与 observable feature 分离；
- task/assay 许可、标签方向和高值定义可审计；
- 未经授权的 held-out 数据保持关闭。

Gate 1 通过前，不运行正式模型比较。

### M2：轻量特征与静态基线

**计划窗口**：7 月 29 日–8 月 2 日。

**首选范围**：简单序列/突变特征与 Ridge、Random Forest、XGBoost 等强表格基线。新增生产依赖
或 TabPFN 等模型必须由独立 Issue 批准。

**交付物**：静态 prediction metrics、资源测量、feature hash、训练/校准边界验证。

**退出条件**：相同 split/config/seed 可复现；预处理只在允许的观测范围拟合；结果绑定完整
provenance。

### M3：deterministic replay 与单保真 smoke

**计划窗口**：8 月 3–7 日。

**最小矩阵**：少量已通过 Gate 1 的任务、3 个左右基线模型、Random/Greedy/Uncertainty、少量
seed 和小预算。

**Gate 2：回放可信性**

- 同一 RunSpec 产生相同 initial selection、tie breaking 和 trajectory；
- candidate 不被重复购买，budget/cost 单调且正确；
- 只有 replay engine 能调用 hidden oracle；
- 中断恢复不覆盖成功 attempt，聚合忽略无 `_SUCCESS` 产物；
- decision metrics 通过小型确定性 fixture 验证。

### M4：E1 单保真筛选与确认

**计划窗口**：8 月 8–17 日。

先用代表任务、32/64 等少量初始规模和约 10 个 seed 做 screening。模型从线性、树模型、GP、
TabPFN 或 Ensemble 中按 Issue 逐步引入；策略至少包含 Random、Greedy、UCB/EI 等探索基线。

**Gate 3：主矩阵冻结**

- 根据预先声明的 screening 规则选择最终模型、策略和初始规模；
- 记录被淘汰组合及理由；
- 冻结关键组合的 task、seed、budget、threshold 和统计计划；
- 不读取 held-out 结果帮助筛选。

### M5：E2/E3 条件与一致性分析

**计划窗口**：8 月 18–22 日。

研究初始样本量、噪声、高值稀疏度、初始化偏置、activity cliff 和候选池规模，并比较
prediction ranking 与 decision ranking。输出应优先解释策略何时失效，而不是只给平均名次。

在独立 onboarding Issue 与预先声明的 protocol 通过审核后，完成最小、可审计的 SKEMPI 2.0
external validation。该切片只回答跨蛋白复合物的外部有效性，不自动扩展到 complex-level
normalization、深结构特征或结构能量计算。

**退出条件**：跨 task 的统计单位正确；关键差异提供置信区间和配对分析；探索性与确认性分析
明确区分；最小 SKEMPI external-validation 结果绑定 manifest、split、RunSpec 和 attempt。

### M6：E4/E5 多保真

**计划窗口**：8 月 23–29 日。

- E4：用公开真值可复现生成相关性、偏差、噪声和成本条件；
- E5：将 ProteinGym 公开 zero-shot 分数作为真实低保真候选，DMS 标签作为高保真真值；
- 比较只买高保真、固定比例、预筛和成本感知联合选择。

**进入条件**：单保真框架通过 Gate 2，cost model 和 fidelity contract 在看结果前冻结。

**退出条件**：报告固定总成本下的 regret、节省率和负收益概率，并单独分析高值区排序失真。

### M7：证据冻结与主结果

**计划窗口**：8 月 30–31 日。

**交付物**：

- 冻结的关键 RunSpec/attempt 集与 release manifest；
- 主表、regret/Top-k 曲线、预测—决策关系图和多保真相图；
- 失败 attempt、排除规则、资源用量和已知限制；
- benchmark evidence 与 scientific gate 的独立状态说明；
- 9 月增强项 backlog。

结果冻结只接受 checksum-linked、provenance 完整且有成功标记的 attempt。缺失任务或 seed 不用
模拟数据补成“完成”。

## 9 月以后增强线

按主线证据决定是否启动：

1. 扩展 ProteinGym assay 与关键组合 seed；
2. SKEMPI complex-level normalization、扩展 split 和轻量结构基线；
3. conformal calibration、activity-cliff-aware acquisition；
4. FoldX/Rosetta 或更深结构代理；
5. 知识引导粗到细搜索与解释模型。

每项增强都需要说明新增的可证伪假设，不因“已有代码可用”自动进入主 Benchmark。

## Issue 到合并与运行的标准流程

```text
同步最新 main
→ 建立有验收标准的 Issue
→ 创建 Issue 关联短期分支
→ 实现最小可测试切片
→ 检查 privacy / protocol / diff scope
→ 本地 commit
→ 明确授权后 push
→ Draft PR
→ CI 与 review
→ 有权限的另一位协作者批准
→ 明确授权后 squash merge
→ 在精确 commit/environment 上执行正式任务
→ 上传、回读、校验并发布完成证据
```

PR 合并只代表该 Issue 的 engineering change 进入 `main`。server execution、benchmark evidence
和 scientific gate 需要后续独立证据。

## 路线维护

- 具体 Issue 的验收标准优先于本路线的概括描述；
- 阶段范围或 Gate 改变时更新本文件并说明原因；
- frozen protocol 变化使用新版本或 superseding ADR；
- 每周按 Gate、阻塞和证据缺口更新，而不是只按日期报告百分比；
- 资源和价格只在 pilot 后的运行计划中记录，不固化在长期方向文档中。
