# Benchmark Contract

## 目的

本文件定义主动学习离线回放（offline active-learning replay）的最小科学契约。它约束数据、
候选池、hidden oracle、split、metric 和运行产物，但不预先指定某个模型或策略一定优胜。

## 数据角色

每个任务必须明确区分：

- **development data**：允许用于实现、调参和策略选择；
- **candidate pool**：每轮允许 acquisition strategy 选择的有限候选集合；
- **hidden truth**：候选池中的真实标签，只能在候选被选择后由 oracle 揭示；
- **validation data**：允许用于冻结超参数和校准规则的数据；
- **held-out data**：在 protocol freeze 前保持关闭的最终评价数据。

不得根据 held-out 结果重新选择 feature、model、acquisition、threshold 或 cost ratio。

## 任务 Manifest 集

每个 dataset/task version 由职责分离、checksum 绑定的 manifest 集共同描述：

- **Dataset Manifest**：dataset/task ID、source version、source URL、license、下载时间、
  原始文件 SHA-256 和数据源限制；
- **Processing Manifest**：输入 Dataset Manifest hash、处理配置 hash、处理代码 full commit、
  candidate ID 唯一性、duplicate/near-duplicate 规则、可观察 feature、hidden label、label
  direction、处理输出 checksum 和处理限制；
- **Split Manifest**：输入 Processing Manifest hash、membership artifact、partition、grouping
  unit、leakage control 和 held-out partition；
- **Experiment Config**：high-value threshold、decision metrics、budget、model、acquisition 和
  held-out authorization；
- **RunSpec**：所有影响 run identity 的 canonical 输入；
- **RunAttempt**：实际执行 commit/environment、attempt 状态、compute metadata、输出 checksum、
  failure 和 `_SUCCESS` 记录。

JSON Schema 负责结构、类型与局部约束。需要比较多个动态字段的规则，例如 hidden label 不得
同时出现在 observable feature 列表中，由领域校验器执行，并必须 fail closed。

## 主动学习回放

标准轨迹为：

```text
initial observations
→ fit surrogate model
→ predict candidate mean and uncertainty
→ select candidate IDs
→ oracle reveals selected labels
→ append immutable round record
→ repeat until budget is exhausted
```

相同 `RunSpec` 必须产生相同 initial selection、tie breaking 和随机序列。随机性必须由显式
seed 控制，不得依赖进程启动顺序。

只有 canonical `RunSpec` 参与 `run_id`。`attempt_id`、时间、compute metadata、运行状态、
failure 和输出 artifact 不得改变 `run_id`。

## 指标

Prediction metrics 与 decision metrics 分开报告。

Prediction metrics 可以包括 MAE、RMSE、Spearman、NLL 和 calibration error。Decision
metrics 可以包括 best-found value、simple regret、Top-k recall、success probability、
labels-to-target 和 cost-to-target。

Top-k、Top 1% 和 near-optimal threshold 必须在读取实验结果之前冻结。不同候选池规模下的
归一化规则必须写入 experiment config。

## 统计单位

随机 seed 用于估计算法随机性，但不能替代独立 assay、protein 或 complex。主结论应在任务
层级进行分层汇总，并明确区分 within-task 与 across-task uncertainty。

## Gate 边界

- CI green：只表示静态检查和小型测试通过；
- replay smoke pass：只表示最小离线循环可复现；
- pilot complete：只表示实际资源和失败率已测量；
- scientific gate pass：必须满足预先冻结的跨任务 evidence contract。

任何缺失的 provenance、split audit 或 held-out authorization 都应 fail closed。
