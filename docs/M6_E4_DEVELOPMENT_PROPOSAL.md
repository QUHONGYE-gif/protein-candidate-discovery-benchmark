# M6 E4 有界开发实验方案 v1

状态：**DRAFT / FOR REVIEW / NO REAL EXECUTION**。提案 ID：`m6-e4-development-v1`。
本文件完成方案规划与发布，不是已授权的运行配置、已冻结实验契约或科学结果。

- authority：`github_issue`
- repository：`QUHONGYE-gif/protein-candidate-discovery-benchmark`
- issue_number：`107`
- url：<https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/107>
- authorization_and_acceptance：负责人同意进行方案规划与发布，并遵守仓库实现流程。
  下列具体模型、成本、矩阵与阈值是本次提交的待审选择，不能追溯视为已逐项获批。
- PR class：`Major / semantic`；发布于现有 Draft PR #108，不新增平行主 Issue。

## 1. 决策问题与证据定位

在同一公开开发候选池、同一初始 HF 和相同总购买成本下，高值区 LF 排序反转能否使
成本感知选择相对于 HF-only 的发现收益由正转负？同时报告整体相关性下降和 LF 成本上升的
单轴效应。结果可为负或不确定，不以得到优越策略为完成条件。

本次建议先进行 **E4 / development / directional**，不进入 Formal/Confirmatory，不运行 E5。
Alpha/IRED 已用于既有开发，不能宣称独立确认；M4 `Gate 3 = STOP` 保持不变。
M5D 开发结果不用于选择本次模型、参数或候选。只有两个 task、每 task 五个 seed，
不形成普遍性或显著性结论。

当前实现基线为 `012e954672fcf7fbf14d3704bc8631cca1f48b19`：S1 和四策略工程组件、
48 条 synthetic 演示、973 项测试与 CI 已完成。现有 toy scorer 不用于本实验。
具体实现差距和启动条件见第 8–10 节；本方案未读取任何新的真实标签或 LF 结果。

## 2. 输入、候选池与可见性

### 已从版本化配置核验的预期父身份

以下是 M4 配置记录的父输入身份，**不是本轮服务器 fresh readback**，也不是复用 M4 配置
启动 M6 的许可。M6 必须新建自己的配置/运行身份。

| task | Dataset Manifest SHA-256 | Processing Manifest SHA-256 | Split SHA-256 | 父 candidate-pool 数 |
|---|---|---|---|---:|
| alpha-amylase | c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac | cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec | c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a | 2262 |
| ired | 37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643 | 929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba | 9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e | 2070 |

来源：`configs/experiments/m4-single-fidelity/{alpha-amylase,ired}.json`。实际文件位置、size/hash、
feature identity、源环境和执行源码仍须在启动前绑定，当前为 UNKNOWN；不能从测试 fixture 补值。

### 本次固定的开发子集

- 每 task 仅在其父 `candidate-pool` 内，按 `SHA256("m6-e4-pool-v1:" + task + ":" + candidate_id)`
  升序取 256 个；哈希并列用 candidate ID 升序。先确定 ID，后读取标签。
- 子集跨条件和 seed 不变；不按 label、hit rate、模型表现、长度或位点覆盖筛选。
- 这是新的 M6 开发子集，不修改父 Split，不称为原 M4 全池或原冻结高值集合。
  所有 top-fraction 指标在这 256 个候选内重新定义并使用 M6 identity。
- 只使用既有 `sequence-composition-420-v1` 可观察特征；新子集视图绑定父 feature identity
  和子集 ID hash，不改变既有特征定义，不引入结构/embedding 或新依赖。
- 未获授权的 validation/held-out 不读取。候选 features、完整 HF truth、完整 LF table、
  revealed observations 和评价输出分开持有。
- 生成器可以在授权执行时读取本子集完整 HF 以构造模拟 LF；这是明确的 simulation owner
  权限，不允许把生成器统计、rho、tail membership 或未购买 LF/HF 传给 surrogate/scorer。

## 3. 成本与最小矩阵

科学成本是无量纲购买单位，不冒充实测费用或墙钟加速比。建议采用逐候选付费：
HF 单次 100，LF 为 1 或 4；同候选 LF→HF 两次费用相加；初始 HF 全部计费。

| 参数 | 待审固定值 |
|---|---|
| task / pool | Alpha、IRED；各 256 个固定开发候选 |
| replay seeds | 1729、1730、1731、1732、1733 |
| 初始 HF | 每 task/seed 16 个；四策略与四条件共用 ID 和次序 |
| 初始 ID 规则 | 按 `SHA256("m6-e4-initial-v1:" + task + ":" + seed + ":" + candidate_id)` 升序取 16 |
| LF 配对 warmup | 三个多保真策略再购买上述 16 个候选的 LF；HF-only 不买 LF；费用照计 |
| 总购买预算 | 6400；含初始 HF、LF warmup、预筛和后续所有购买 |
| 决策批量 | 每次 1 个 candidate/fidelity 动作 |
| 成本检查点 | 3200、4800、6400；主终点为 6400 |
| fixed_ratio | warmup 后按 2 次新 LF → 1 次 HF；比例是次数，不是成本 |
| hard_prescreen | 全池 LF 付清后固定保留最高 25%，再在其内按 HF 预测均值购买 |
| 高值定义 / 目标 | 子集 top 1%，含边界并列；target recall = 0.5 |
| 敏感性 | top 0.5% 与 top 2%，只评价，不改变选择或停止 |

| 条件 | 偏差前 rho | LF 高值区镜像 | LF 成本 | HF 成本 |
|---|---:|---|---:|---:|
| C0 reference | 0.8 | 否 | 1 | 100 |
| C1 lower-correlation | 0.2 | 否 | 1 | 100 |
| C2 high-tail-reversal | 0.8 | HF top 10% 区域 | 1 | 100 |
| C3 higher-lf-cost | 0.8 | 否 | 4 | 100 |

这是四个预列条件，不是完整笛卡尔积。2 tasks × 4 conditions × 4 strategies × 5 seeds =
**160 个逻辑比较单元**。HF-only 与 LF 条件无关，每 task/seed 只执行一次，共 10 条；
三个多保真策略执行 120 条，合计 **130 条唯一轨迹，30 个 HF-only 引用复用**。
复用必须绑定相同子集、初始、HF、模型配置、预算和 scorer 的无 LF 行为，不伪造新 attempt。

最贵条件全池 LF 为 256×4=1024，初始 HF 为 1600，合计 2624；6400 预算足以付清预筛，
并保留追加 HF。LF warmup 是这 256 次 LF 的一部分，不能在预筛阶段重复收费。
HF-only 最多获得 64 个 HF。候选/保留集耗尽允许提前停止，并在固定成本曲线上保持最后状态。

## 4. 可控代理与随机性

沿用现有生成公式：HF 中心化后除 L2 norm 得 z；PCG64 正态噪声去均值、去 z 投影后
归一化得 e；`q = rho*z + sqrt(1-rho**2)*e`。C2 对 HF top 10%（含并列）的 q 围绕
该区 q 均值镜像，其他值不变。供 LF oracle 的数值为 `sqrt(256)*q`，避免单位方差随池规模
缩小；该缩放必须作为新代理输入版本显式绑定，不能改写旧 synthetic 输出身份。

- 常数 HF 或退化噪声 fail closed，无自动重抽、替换 task 或 seed。
- 代理 seed 为 SHA-256 域分离字符串 `m6-e4-proxy-v1:task:replay_seed` 的前 8 bytes，按
  big-endian unsigned 转整数。C0–C3 共用同一基噪声；C3 直接复用 C0 LF bytes。
- 模型 bootstrap 使用不同域 `m6-e4-bootstrap-v1:task:replay_seed:fidelity:observation_ids_hash:member`；
  使用同样前 8 bytes 规则和 PCG64。ID 按 candidate ID 排序，JSON 紧凑 UTF-8 array 取 hash。
  seed 不包含 strategy/condition；同观测身份可复用共同随机数，但标签变化必须触发重新拟合。
- 记录偏差前/后实际 Pearson、高值区 LF/HF Spearman、并列数及未定义情况；不事后调回 rho。
- 所有生成统计仅进入评价报告。RF 或 scorer 不接收真实 rho、HF 全池均值/标准差或高值阈值。

## 5. surrogate 与同单位信息价值：待审实现定义

### 固定 RF 族，不继续使用 toy scorer

选择现有 RandomForestRegressor 为基础，不搜索模型。复用现有 RF 参数：64 trees、
max_depth=8、max_features=1.0、min_samples_leaf=1、bootstrap=True、n_jobs=1、
random_state=1729；其余参数逐项复制基线 `src/pcdb/m2/models.py` 的 RF inventory。

多保真组合、bootstrap、输入特征扩展是 **新的 M6 model/scorer identity**，不是 M4 原模型配置
字节或 M4 结论的复用；不修改原配置。新 identity 及 artifact 形状必须在实现 PR 中列明供审查。

每个状态建立 5 个 ensemble members，分别对已购买 LF 和已购买 HF observations 做有放回
bootstrap（样本量等于各自已观察数）。每 member：

1. 有 LF 时用 420 维 X 拟合 RF_L；无 LF 时 LF prediction 固定为 0。
2. HF 特征为 X 加两列：已购买 LF 的真实观测值（无 LF 则该 member 的 LF prediction），
   以及 paid-LF indicator。用该 member bootstrap 的已揭示 HF 拟合 RF_H。
3. 对全池输出 HF/LF predictions，已购买对应 fidelity 的预测样本替换为实际观测，方差为零。
   不允许 HF 标签进入 LF 拟合；HF 特征中的真实 LF 必须在当前状态已购买。
4. HF 均值取 5 members 均值。无 LF 的 HF-only 同样使用这一 RF_H 族和 422 列，后两列全零。
   不能声称不同策略沿途具有同一训练集，公平性是共同起点、预算和固定建模规则。

warmup 后 fixed_ratio 的节奏计数只包含 warmup 以后的 LF/HF 动作。现有仅依赖 State 总计数的
实现需要显式区分初始与 warmup，不能把初始配对 HF 误算为后续筛选确认。

### 信息价值的明确近似

不做每候选幻想重拟合。对每个可购买候选 c 和 fidelity f，从上述 5 members 计算
`v_f(c)` 及它与每个未揭示 HF 候选 i 的 `cov(H_i, F_c)`，均用 population denominator 5。

- 假设观测噪声尺度 `epsilon_H = 0.1*max(sd(initial_HF, ddof=0), 1e-8)`；
  `epsilon_L = 0.1*max(sd(warmup_LF, ddof=0), 1e-8)`，每条轨迹固定；HF-only 不估计 LF。
- 对 `z = {-sqrt(2), 0, sqrt(2)}`，权重 `{1/4, 1/2, 1/4}`，定义
  `m_i(z) = m_i + cov(H_i,F_c)/sqrt(v_f(c)+epsilon_f**2)*z`。
- `R(z) = max(best_observed_HF, max_i m_i(z))`；
  `V_f(c) = max(0, sum_z weight(z)*R(z) - R(0))`。
- HF 和 LF 的 V 都是 HF 标签单位下的“最优预测均值提升”近似；统一输出给选择器。
  cost_aware 选择 `V_f(c)/cost_f` 最大项。并列先 HF，再 HF 预测均值降序，再 seed/ID hash；
  全部 V=0 时退化为可负担的 HF Greedy，不再随机消耗 LF。

这是 bootstrap ensemble 的线性高斯 lookahead **启发式**，不是已校准贝叶斯后验或真实信息收益。
当前 toy Gaussian prior 必须移除。零协方差导致 LF 无价值可以是有效负结果，不通过增加 members、
调噪声或 beta 搜索补救。当前数学定义足以实现和手算验证，但其科学有效性仍需审查。

三种多保真策略都获得同样的计费配对 warmup。fixed_ratio 的新 LF 按 V_L 选择，HF 按均值；
hard_prescreen 的全池 LF 次序按固定 seed/ID hash，其保留集及初始已计入的 hits 不随购买变化。
LF 后无法再负担一次 HF 时停止新增 LF；不得用 LF 直接确认 hit。未授权免费全池 LF 场景。

## 6. 评价、判定与停止

主策略 contrast 是 cost_aware − HF-only 的 top-1% recall 差，终点成本 6400，单位为 recall
比例差（0.05 = 5 个百分点）。主机制 contrast 是 C2 的该差减去 C0 的该差。
其他两策略、C1、C3 和其他检查点全部报告，不能按结果挑选主对照。

每 task 先对五个共同 seeds 等权汇总，保留逐 seed 配对值。只有两个 task，因此不做总体显著性
检验、不用 seed bootstrap 冒充跨 task 置信区间。unitful simple regret/best-found 只报告 task-local。

每个 task/condition 相对 HF-only 的方向标签：

- positive：mean delta recall >= +0.05，且至少 4/5 seeds > 0；
- negative：mean delta recall <= -0.05，且至少 4/5 seeds < 0；
- unstable：不满足前两项，且同时存在正/负 seed；
- inconclusive：其余情况；零差不解释为统计等价。

原问题“由正转负”仅当 **两个 task 都满足 C0 positive、C2 negative**，且各 task 的 C2−C0
配对差至少 4/5 为负，才标为 `SUPPORTS_BOUNDED_HYPOTHESIS`，建议以后另行确认。
只有一个 task 支持或方向冲突 → `UNSTABLE / REVISE`；C0 本身无正收益、差异太小或其他
不满足条件 → `INCONCLUSIVE / STOP_CURRENT_MATRIX`。不得从无阳性证据推断 LF 普遍无用。
实现、输入或资源失败为 execution failure/invalid，不填成科学 negative。

必报：各策略 fixed-cost recall/regret、完整成本轨迹、LF/HF 数量、cost-to-target、达到目标比例、
双方删失数、双达标条件节省率、within-task seed 负向比例、高值区排序诊断和全部四条件表。
256 池 top 1% 名义仅 3 个候选，recall 离散度很高，实际并列数必须报告；不由敏感性结果改主指标。
达到 target 只记录首次成本，不提前终止轨迹；继续到预算或可选候选耗尽。
固定矩阵结束立即停止，不追加 task/model/seed/rho/cost 条件，不自动运行 E5/M7 或证据晋级。

## 7. 执行与损失上限（建议值，尚未部署）

- 唯一指定计算环境；单 worker、单原生线程，不增加服务器、服务、依赖或权限。
- 全 study active wall 6 小时；进程树 RSS 1.5 GiB；新 study 产物 2 GiB；普通 run 零 COS 写入。
- 运行阶段不下载新源数据；源码与紧凑材料传输上限 100 MiB。资源可用性尚未在本轮 live 核验。
- 先做无真实标签的最坏维度 synthetic resource probe，最多 5 分钟，计入 6 小时。
  基于最大状态 LF=256、HF=64 测试 scorer，外推总量超过剩余 wall 的 80% 时停止待修订，
  不能用真实结果选择缩矩阵。外推不保证运行完成，仍持续执行硬上限检查。
- 最多 130 条唯一科学轨迹；无额外 truth-bearing pilot、warmup 试运行或自动扩量。
  ordinary attempt 自动重试 0；资源/代码失败保留失败终态，修复后需新的 source/attempt 和明确续跑范围。
- 每次实际尝试使用新目录；已成功轨迹只按完整身份复用，不覆盖、重跑或当成新重复。
  本次不授权读写旧成功 run，也不生成 COS archive。

## 8. 仓库实现顺序与验收

所有切片继续由 #107 跟踪，在 #108 或后续 Issue-linked reviewed PR 中实施，不按模块新增 Issue。

| 顺序 | 完整切片 | 最小验证与验收 |
|---|---|---|
| A | observed-only RF ensemble→同单位 V→四策略选择 | 手算协方差/三点权重；已购买值覆盖；未揭示标签扰动不改决策；全零 V 的 HF Greedy；state 重建确定性；无真实数据 |
| B | 父 manifest→固定子集→独立 oracle→计费 warmup/四策略→报告 | 拒绝 input/hash/split 漂移；两种成本硬筛可行；warmup 恰好收费一次；初始/节奏分开；160 逻辑单元 exact cover、130 唯一轨迹、30 显式复用 |
| C | source/environment/input 绑定→append-only 运行→新进程回读 | 新持久化边界单列供审查；禁止 mock 代替真实输入；失败/超限 terminal；_SUCCESS 最后；新进程从保存产物重算选择、成本、指标、汇总和 checksums |
| D | 全部方向→task-first 报告→科学审查 | 删失、离散性、两任务限制、失败/排除全保留；执行完整与科学方向分别记录 |

每个代码切片先 targeted tests，再 `make check`，检查 diff、provenance 和 hidden-label 边界。
本次只是 docs-only 方案发布：执行 docs/governance/link/diff 检查及 required pre-PR checks，
不将通过检查记为上述 A–D 已实现。禁止直接写 main、force push、自动 merge 或关闭 #107。
现有 synthetic 输出与冻结 M0–M5 不改写；新 artifact 格式须在实现 PR 明确提出，不偷偷扩 RunSpec。

## 9. 启动前真正缺失的信息与决策

| 项目 | 当前状态 | 完成方式 |
|---|---|---|
| 本文的成本、子集、模型/scorer、矩阵和判定规则 | DRAFT | 审查当前文档 exact revision，接受或指出修订；“同意规划”不替代逐项语义决定 |
| 父数据实际服务器定位、size/hash、feature 身份、访问方式 | UNKNOWN | 只读 metadata/inventory 核验后写入单一执行记录；不在公开正文发布机器 IP/内部路径 |
| 当次源码/tree、环境 digest、资源实测、产物格式与验证命令 | NOT_READY | A–C 实现后绑定并跑机器检查；不使用旧 commit 或 placeholder 充数 |
| 真实开发数据读取及一次 bounded execution | NOT_STARTED | 绑定上述具体方案和实际操作范围；本轮不启动 |
| Formal/Confirmatory | NOT_REQUESTED | 若未来晋级，按 ADR-0005 / Governance v2 Gate A 处理，成本等语义须两位科学负责人明确批准 |

本开发方案不通过改名绕过正式 Gate，也不为普通代码 review、每个 seed/phase 制造新的科研 Gate。
规划/代码审查批准、真实执行授权、Formal Gate A/B 和科学结论分别记录。当前不产生
Gate A 转换、Gate B 或 milestone closeout，因此不更新 docs/STATUS.md。

## 10. 已知限制与 E5 后续

三个可能使结论失效的风险：

1. 代理生成/高值 membership 泄漏给 scorer，或使用未购买 LF 构造 HF 特征；必须用泄漏测试阻断。
2. ensemble covariance 与高斯幻想未校准；V 不等于真实发现收益。只能评价这一固定启发式，
   不宣称最优信息价值或代表所有多保真方法。
3. 固定小子集、两 task、五 seeds 与稀疏高值使结论不稳定；不能用更多 seed 自动补成外部证据。

E5 不包含在这 130 条轨迹中。#11 四-assay DMS/zero-shot 数据准备已完成；下一方案仍须绑定
具体 proxy column/model、方向、coverage/missing、M5C experiment split/leakage 和真实成本解释。
不因 E4 结果从四 assay 中挑选最有利成员，不重复下载已准备数据，不恢复未授权 held-out。
