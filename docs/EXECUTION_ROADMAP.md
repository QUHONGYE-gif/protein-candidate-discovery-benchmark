# 项目执行路线与初始时间线

> 状态：历史规划基线。当前事实见 [`STATUS.md`](STATUS.md)；M4 之后的规范路线见
> [`M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`](M4_TO_M7_REVISED_EXPERIMENT_PLAN.md)。本文不再作为
> M4 之后阶段状态或下一动作的实时来源。

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

截至 2026-08-01，Alpha Amylase 与 IRED 已分别完成 accepted raw ingest、processing、deterministic
split implementation，以及历史单独授权的 immutable COS publication/readback。IRED Issue #22
还完成了 exact-commit 真实 observable-input preflight、正式 `2 + 1` publication 和独立 fresh
readback；duplicate request 只观察到 forbid-overwrite 下非零失败及后续 inventory/bytes 未变，
COSCLI 没有暴露可解析的 HTTP 409。两项任务的 exact manifest chain、candidate counts、限制与证据
链接见 [Alpha＋IRED M1 readiness ledger](data/ALPHA_IRED_M1_READINESS_LEDGER.md)。

这些事实只使 Alpha＋IRED 具备 Gate 1 review inputs。Gate 1 尚未由 Issue #42 判定，项目整体继续
处于 M1 portfolio onboarding；历史 COS execution 也不证明当前 credentials 已配置、仍有效或对
新操作获得授权。

补充（2026-07-30）：Hydrophobic Core Issue #26 已在 reviewed/merged implementation commit
`ba85ebec4bdbd21c555720762e5681f5e17dd376` 上完成正式 immutable Processing Manifest
publication、`4 + 1` 远端 inventory、fresh exact-byte readback 与禁止覆盖验证。该结果仅关闭
Hydrophobic Core processing gate；Split Manifest、held-out、replay、benchmark evidence 与
scientific gate 继续由 Issue #27 及后续独立授权负责。

补充（2026-07-31）：Nuclease B Issue #25 已在 reviewed generation commit
`de2280adfd522c6917329070565f742c8e6553cc` 上完成正式 immutable Split Manifest
publication、`2 + 1` 远端 inventory、fresh exact-byte readback 和独立确定性重建验证。冻结
membership 为 development/candidate-pool/validation/held-out =
32,776/11,493/5,745/5,745，cross-role Hamming-1 edge 为 0。该结果只关闭 NucB split
implementation/publication gate；held-out authorization、hidden-truth consumption、replay、
benchmark evidence、M2 completion 与 scientific gate 仍需后续独立授权。

补充（2026-07-31）：Hydrophobic Core Issue #27 已在 merged commit
`cda72d5ac96268cbb9a41432d73adafe628e7761` 上完成具名
`flip2-hydro-within-wt-topology-stress-tracer-v1` 的正式 immutable Split Manifest
publication。Protocol hash 为
`6ef77e40e56d079959430d086eddde4b74360ca7c4cddd6af33bbc0cf52bacbe`；
远端 inventory 为 `2 + 1`，membership 的
development/candidate-pool/validation/held-out 为 23,775/580/290/290，1,346 个 within-WT
components 与 88,664 条 Hamming-1 edges 均为 0 cross-role crossing。非发布方已 fresh
读取 accepted observable inputs，在 exact merged commit 上重建，并确认 COS bundle 3/3
exact-byte identical、Schema/domain PASS。该 split 被前瞻性限定为非 primary、非 M2 必需的
topology stress tracer；它不授权 hidden truth、held-out/WT-holdout、feature/model/replay、
benchmark conclusions、M2 entry 或 scientific gate。

### M2：轻量特征与静态基线

**计划窗口**：7 月 29 日–8 月 2 日。

**首选范围**：简单序列/突变特征与 Ridge、Random Forest、XGBoost 等强表格基线。新增生产依赖
或 TabPFN 等模型必须由独立 Issue 批准。

**交付物**：静态 prediction metrics、资源测量、feature hash、训练/校准边界验证。

**退出条件**：相同 split/config/seed 可复现；预处理只在允许的观测范围拟合；结果绑定完整
provenance。

补充（2026-08-01）：PR #39 已以 merge commit
`8db51b8bc5d7c97a7753bcc462790ac947c8611f` 将 production Experiment Config v2、metric/result
aggregate contracts 与 synthetic evaluator conformance 进入 `main`。这建立了 M2 的契约前置条件，
没有完成 feature artifact Schema/canonical feature hash、prediction artifact contract、feature
builder、surrogate baseline 或正式 M2 execution，也不替代 Gate 1 verdict。

收尾（2026-08-10）：Issue #42 已将 Alpha Amylase＋IRED 冻结为首批 Gate 1 PASS cohort；Issues
#44/#47/#46 已完成 feature artifact、canonical feature identity 与 static prediction contract，
Issue #54 完成 Linux CPU 最小依赖可运行性验证，Issues #55/#56 完成 Ridge、Random Forest 与
XGBoost 的 observable-only producers。Issue #58 随后在固定 `2 tasks × 3 models × seed 1729`
矩阵上完成单次正式执行：六份 RunAttempt 均 succeeded，26 shared＋42 per-attempt 共 68 个对象
完成 forbid-overwrite publication 与独立 fresh readback，final evidence review 判定
`APPROVED / ORIGINAL PURPOSE ACHIEVED` 并以 completed 关闭。

因此首批 M2 的 implementation、CI、server execution 与 benchmark evidence 已完成。该状态只限
Alpha＋IRED task-level static-baseline evidence；整个 M1 portfolio、held-out、external validation、
M3 execution、Gate 2 与最终 scientific validation 不由此完成或授权。静态结果显示 prediction
ranking 与 high-value recall 会随 task/threshold 改变，不支持跨 task 普遍最优模型声明。

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

收尾（2026-08-13）：Issue #66 的
[M3 single-fidelity replay protocol v1](M3_SINGLE_FIDELITY_REPLAY_PROTOCOL_V1.md) 已完成 exact-bytes
独立审核，Issue #67 的 synthetic ReplayEngine implementation 已通过审核并合并。Issue #68 随后在
exact merged source `e2f15d3e8dc85abc0a9e0a76345c18eb0a353566` 上完成 Gate A real-input
preflight 与一次正式 Gate B sequence：冻结矩阵为 Alpha＋IRED、三模型、Random/Greedy/Uncertainty、
seed `1729`，共 18 个 RunSpecs；9-object sentinel 经独立 readback 后才发布余下 153 个对象，最终
162-object exact-cover、bytes/checksums/cross-links 与 resource completion 均通过。执行过程中
retry、replacement、overwrite、DeleteObject 和 cap expansion 均为 0。

最终独立 evidence review 以 `15b7dd2339e2eea7f71d15d444a1845e2568d766b634749d0fd6f4e5d7a73db9`
的 `final-readback.json` 和
`fcdfff8b11c1ccb69c312d97f47d5db219decb5b9f42ac669c5a670633ad2a52` 的 final resource
checkpoint 为锚点，判定五项 Gate 2 engineering conditions `5/5 PASS`；Issue #68 已以 completed
关闭。该结论只证明冻结范围内 replay/publication/readback 的工程可信性，不证明 surrogate 或
acquisition 的科学优越性，不代表 held-out、external validation、M4 或主结果已经完成。

### M4：E1 单保真筛选与确认

**计划窗口**：8 月 8–17 日。

先用代表任务、32/64 等少量初始规模和约 10 个 seed 做 screening。模型从线性、树模型、GP、
TabPFN 或 Ensemble 中按 Issue 逐步引入；策略至少包含 Random、Greedy、UCB/EI 等探索基线。

**Gate 3：主矩阵冻结**

- 根据预先声明的 screening 规则选择最终模型、策略和初始规模；
- 记录被淘汰组合及理由；
- 冻结关键组合的 task、seed、budget、threshold 和统计计划；
- 不读取 held-out 结果帮助筛选。

当前进入状态（2026-08-13）：M3 Gate 2 engineering prerequisite 已满足。M4 尚未授权执行；下一步
应在新的 Issue 中 prospectively 冻结 screening matrix、seed/initial-size 预算、模型与 acquisition
候选、淘汰/保留规则、统计单位、资源上限和 held-out 禁止边界。Issue #68 的 authorization 不得复用。

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
