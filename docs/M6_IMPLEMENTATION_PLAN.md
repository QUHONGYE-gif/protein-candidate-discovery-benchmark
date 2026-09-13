# M6 拆分与首个实现切片

状态：S1 与 S2 的四策略 synthetic 工程切片已实现；真实 S2/E4、S3/E5 与 S4 科学终态尚未完成。
Authority：[GitHub Issue #107](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/107)。
本文为实现范围与待审方案，不是冻结科学协议或 Gate A。

## 依据与证据

[M4→M7 规范规划](M4_TO_M7_REVISED_EXPERIMENT_PLAN.md) 要求固定总成本下比较 HF-only、
固定比例、硬预筛和成本感知联合选择，并默认保持一个 M6 主 Issue。
因此采用一个 Issue 内的四个垂直切片，不按 schema、runner 或 assay 分散建单。

2026-09-12 已回读 GitHub main 规划、STATUS、Issue #104 正文/评论和 PR #105。
M5D 正式执行与四阶段开发完成，独立审核 PENDING，第四阶段 `inconclusive / revise`。
PR #105 仍 Open/Draft；后续开发队列不是新的独立外部确认集。
main STATUS 是较早快照，不能用其旧时态否定 #104 后续执行；当前未找到 M5C 实验
onboarding 的完成证据。M4 STOP、既有成功产物与冻结协议保持不变。

## 拆分与验收

| 切片 | 完整交付路径 | 类型与依赖 | 验收重点 |
|---|---|---|---|
| S1 | synthetic 输入→LF/HF 购买→成本轨迹→HF-only 评价 | AFK；无依赖 | 原子校验、付费可见性、分支隔离、手算指标、确定性 smoke |
| S2 | E4 授权开发输入→可控代理→四策略→同成本曲线→服务器回读 | 科学定义 HITL 后 AFK；依赖 S1 | 稀疏条件矩阵、生成器隔离、来源绑定、完整正负方向 |
| S3 | E5 四-assay 输入→固定 proxy→同一四策略→assay 结果 | 数据/成本 HITL 后 AFK；依赖 S2 和 M5C onboarding | proxy/split/coverage/missing 身份；禁止按 DMS 表现选代理 |
| S4 | E4/E5 结果→task-first 条件汇总→终态审核 | Gate B HITL；S3 完成或明确未完成 | 删失、节省率、负收益概率、高值区扭曲和条件图；seed 不算独立 task |

## S1 行为与限制

内部模块 `pcdb.replay._m6_engine` 复用既有 `HiddenOracle` / `Observation`，每次 replay 新建
隔离的 LF/HF oracle。策略只接收不可变候选 ID、已购买 LF/HF observations、成本和预算，
不接收隐藏表、oracle、全池排序、evaluator 或指标。模型特征接口不在本切片中。

调用方显式给出正整数成本和预算。原型中 LF/HF 分别收费，升级总成本为两次购买之和；
同 candidate/fidelity 只能购买一次。初始 HF 也计入预算，没有免费标签或全池 LF。
这只是原型行为，不能据此认定正式 M6 科学成本模型已冻结。

整批 actions 在任何 reveal 前检查未知候选、重复购买和总成本，然后按提交顺序记账；
策略仅在整批完成后取得新状态。跨 LF/HF 的同 candidate 购买合法。空元组表示停止；
没有可负担的新动作时也停止。不自动替换非法动作或缩小批次。

评价在回放结束后读取同一 HF truth，检查 identity、候选全集和购买轨迹。只支持已转为
larger-is-better 的标签；top-fraction 包含边界并列。只有 HF 确认计入发现；无 HF 时
best/regret 为 None、recall 为 0，LF 再高也不算 hit。

fixed-cost 查询取成本阈值前最后状态；cost-to-target 为达到指定 recall 的首次累计成本，
未达到保留 None 和删失标志，不填成预算或零。
LF identity/完整持久化验证、失败恢复、节省率和跨任务统计不在 S1 中。内部 Trace
不是正式 RunSpec/RunAttempt、公共 schema 或可归档科学 artifact。

## 运行与验证

在安装了开发依赖的 checkout 执行：

```sh
PYTHONPATH=src python -m pytest tests/m6/test_cost_replay.py tests/replay/test_hidden_oracle.py
PYTHONPATH=src python -m pcdb.m6.synthetic_smoke
PYTHONPATH=src make check
```

smoke 没有真实数据/文件读取参数，使用 8 个虚构候选，独立运行 HF-only toy 与
screen-then-confirm toy。它不是正式 E4 的完整四策略比较，输出标记
`synthetic-engineering-only`，两个干净进程 stdout 应 byte-identical。

## S2 新增工程切片（2026-09-13）

本轮继续实现可控代理生成、四策略选择器、单任务配对汇总和完整 synthetic 演示。
它们是实验内部 Python 接口，不注册 schema、RunSpec、正式模型身份或真实数据入口。
原 S1 两分支 smoke 保留，新增四策略入口：

```sh
PYTHONPATH=src python -m pcdb.m6.e4_smoke
PYTHONPATH=src python -m pytest tests/m6 tests/replay/test_hidden_oracle.py tests/replay/test_m3_replay.py
```

### 可控代理

生成器由 input/evaluator owner 调用。HF truth 按候选 ID 排序，中心化并除以 L2 norm 得到
z；PCG64(seed) 高斯噪声先去均值，再去除 z 方向并归一化，得到正交向量 e。
代理为 `rho*z + sqrt(1-rho**2)*e`，偏差前有限样本 Pearson 为配置的 rho。
常数 truth、退化噪声和非法数值直接拒绝；不会通过重抽噪声寻找所需结果。

高值区按 HF top-fraction 含并列定义，偏差开关将该区 LF 围绕区内 LF 均值做镜像。
它反转区内 LF 原有排序，其余 LF 保持不变；并不保证偏差后 LF 与 HF 的局部相关性为 -1。
返回实际全局 Pearson 和高值区 Spearman（并列使用平均秩，未定义返回 None）。
偏差后不再强行调回 rho。truth、代理完整表和诊断都不能传给 scorer。

### 四策略与评分接口

所有策略仅把 S1 State 传给显式 scorer，后者返回全候选 predictive mean 和两种 fidelity
的非负 information value。三张表必须 exact-cover 且有限。两种 value 必须在相同效用单位；
数值校验不能证明这一科学语义，正式 scorer 仍需审查。没有默认真实 surrogate。

- `hf_only`：只买 HF，按预测均值选择。
- `fixed_ratio`：每 low_per_high 次 LF 后确认一次 HF；这是购买次数节奏，不是成本比例。
  LF 选择依据信息价值，HF 优先在已筛候选中按均值选择。初始 HF 不计为筛选确认；预算末端
  保留一次 HF 费用，不能凑足完整节奏时可提前 HF。状态重新计算，不使用共享可变计数器。
- `hard_prescreen`：支付完整候选池 LF（包括初始 HF 候选），按 LF 排序固定保留比例，再在
  该固定子集中按预测均值购买 HF。必须预先负担全池 LF 和至少一次追加 HF，否则 fail closed。
  保留集不随 HF 购买向外扩张，子集耗尽允许提前停止。
- `cost_aware`：在可购买 candidate/fidelity 中最大化 `information_value / explicit_cost`。
  LF 之后必须仍能支付 HF。它依赖 scorer 给出的同单位期望信息收益，绝非 LF 原始分数除成本。
  并列优先 HF，再按 seed 与 ID 的 SHA-256 确定顺序。

所有策略共用初始 HF、总预算、候选池和 scorer；每条轨迹建立独立 oracle。硬筛和固定比例
本身的信息可见性、节奏不同，因此不能声称它们沿途 observation states 相同。

### 配对汇总

Pair 显式携带 task、condition 和 seed；汇总拒绝跨 task/condition 混合、重复 seed 或预算
不匹配。相同检查点比较 recall、已定义 regret 和 seed 内负向比例，保留 regret 有效对数。
达到目标的时间晚于检查点仍为删失。节省率 `1 - treatment_cost / baseline_cost` 仅在双方
截至该检查点均已达到目标时计算，同时报告双达标数和双方删失数。该条件子集均值不能
外推成总体节省率，seed 负向比例也不是跨 assay 的负收益概率。

### synthetic 演示的具体范围

12 个虚构候选、3 个共同 seed、4 个稀疏条件（rho 0.8 基准、rho 0.2、高值区镜像、LF 成本
从 1 增至 2），每条件四策略，共 48 条轨迹。HF 成本 4、总预算 40、初始 HF 2、top fraction
0.25、目标 recall 0.5、LF/HF 次数节奏 2:1、硬筛保留 0.5。它们只定义此内置测试例子。

scorer 使用明确标记的独立候选二元高斯 toy 信念：先验 LF/HF 方差 1、协方差 0.5；
可观察 prior 单独给定，读取已购买 LF 后更新该候选 HF 均值和方差。以固定三点幻想估算最优
后验均值提升，作为两个 fidelity 的同单位 value。该信念在四条件间完全一致，不读取代理
生成器的实际 rho、未购买 LF/HF 或高值 membership。它没有跨候选拟合，不是已校准或正式
选定的多保真 surrogate；48 条结果只验证工程链路，不是 Alpha/IRED 或 ProteinGym 证据。

尚需完成正式 surrogate/scorer 对接、真实输入身份、运行/产物回读、统计规则和科学批准。
不把本次新增选择器标为完整 S2 科学验收，也不由 toy 参数反推正式 M6 已冻结。

## S2 待审设计方向

推荐先做 E4：相同 HF 发现任务和总成本下，高值区排序损坏能否把有用的廉价代理变成
净负收益？整体相关性单独不能回答该问题。

建议以既有 Alpha/IRED 开发 candidate pool 为输入，在正式方案中绑定 manifest/split/feature
和一个现有固定 surrogate，不按 SKEMPI 开发表现选择 M6 模型。以共同基准加单轴对照构成
四个候选条件：基准、整体相关性下降、高值区反转、LF 价格升高，不做完整笛卡尔积。
代理生成属于 evaluator/input owner；生成统计和未购买 LF 不进入策略。

真实结果读取前须明确以下字段，当前方向不能直接启动实验：

1. **成本与四策略**：逐次付费或一次全池预计算；初始/升级收费、总预算、成本比、
   初始样本量、固定比例、硬筛比例、成本感知 acquisition 的效用公式。
   不把任意 `predicted score / cost` 直接命名为已验证的联合选择。
2. **代理与判定**：生成公式、相关性/偏差 levels、seed、最大矩阵行数、目标和主指标、
   效应阈值、task-first 不确定性规则。由 HF 构造的变换保持在 oracle owner 内。
3. **输入与执行**：development-only 身份、模型/特征身份、资源 cap、Governance v2
   人类决策和精确运行身份；E5 另需 M5C onboarding、唯一 proxy/coverage/missing identity。
   #11 data readiness 不能替代 M5C 实验边界。

正收益、负收益、任务方向翻转和证据不足均可作为终态；具体判定先于结果。固定矩阵完成
即停止，不因阴性追加 seed/model/beta/条件，不自动进入 M7。三个主要风险：未购买信息
泄漏、LF 成本漏记、按 HF 结果选择代理/条件。

## 权限与未完成工作

用户已授权拆分、发布和实现。S1 不增加依赖、服务、schema，不改变持久化/冻结协议。
S2 涉及 cost semantics，正式 M6 按 ADR-0005/Governance v2 完成人类批准。
真实 E4/E5、服务器 provenance、科学证据和 Gate 均未完成。普通 run 使用既定计算环境和
append-only attempts、零 COS 写入；未授权 held-out、合并、关闭上游或证据晋级。
