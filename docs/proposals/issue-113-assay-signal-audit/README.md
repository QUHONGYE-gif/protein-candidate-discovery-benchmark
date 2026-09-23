# #113 单场景数据可行性审计：GB1/IgG-Fc 漏筛假设

记录日期：2026-09-23。状态：**执行 NO-GO，技术选择待核实**。本记录是 #112 的后续有界资料审计，不是已冻结实验方案、发明披露书或实际实验授权。[#112 报告草案](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/116)仍待审阅；#113 的负责人选择条件尚未满足。

## 单场景与技术问题

场景限定为 ProteinGym `SPG1_STRSG_Olson_2014` 的蛋白 G B1 结构域与 IgG-Fc 结合测定。问题是：在成本固定的 HF 复测决策前，能否用**当时确实可获得**的测定质量信号识别 LF 预筛会遗漏的有价值候选，同时避免简单取消预筛造成的预算损失。

此处只有待证假设，**没有可实施的区别处理步骤或预期增益数值**。不能把已经观察到的高 DMS 分数、全池 top1% 身份或筛选后测序计数当成选点输入。

## 证据与字段可见性

| 资料 | 已核实 | 对方案的含义/未核实项 |
| --- | --- | --- |
| [项目四 assay 数据准备记录](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/d372f7d54315cdbf028cd8a30affe766e3bcbf7e/docs/data/PROTEINGYM_FOUR_ASSAY_DATA_READINESS.md) | 当前标准化候选对象只有 `candidate_id,mutant,mutated_sequence`；DMS_score 在独立 hidden-truth 对象；zero-shot 分数保留于单独原始对象。 | 当前 replay 没有独立表达量、测序深度/计数、检测限或批次字段；不得绕过 hidden oracle。 |
| [Olson 等 2014 原论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC4254498/) | GB1 mRNA display 在 IgG-Fc 富集前后测序，论文报告三次筛选及无 IgG 对照，并据频率变化计算 binding fitness；PMC 提供 [补充表工作簿](https://pmc.ncbi.nlm.nih.gov/articles/PMC4254498/bin/NIHMS637581-supplement-2.xlsx)。 | 原始发表层面存在成对的前/后测序信息。尚未核对工作簿列名、变体键、逐变体计数粒度、与 ProteinGym 536,962 条候选的映射及再利用条件。论文提到计数，不代表项目现有数据已包含。 |
| [M6 固定结果包](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/36bf63dc8aaeab14c71f20406739323099e75fd8/docs/results/m6-results-20260917/README.md) 与 [跨池进度](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/36bf63dc8aaeab14c71f20406739323099e75fd8/docs/results/m6-progress-20260920/README.md) | SPG1 原开发池取消硬筛选有局部收益；全体八项预列跨池标准未通过。 | 只能说明某一开发设定存在筛选损失，不能把它当成运行前诊断器、独立证实或普适技术效果。 |

**决策时序**：序列和既有 LF 代理分数可作为候选特征；已经购买的 HF Observation 只对已购候选可见。若原始工作簿的“富集前计数”与候选可匹配，它在实验流程中可能早于 IgG-Fc 选择，但其获取需要单独测序/制库成本，不能视为免费或默认全池可见。“富集后计数”、由前后计数得到的 fitness、无 IgG 对照的后验读数及隐藏 DMS truth 都只能在相应实验后用于评价或已购观察。还需证明输入计数与**预筛漏失机制**有关；低计数首先可能意味着测量不确定性，不自动意味着 LF 模型漏判。

## 对现有技术的最小区别检验

[#112 的 claim chart](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/codex/112-patent-feasibility/docs/proposals/issue-112-patent-feasibility/README.md)已显示，蛋白预筛、主动选择、不确定性/成本感知多保真和达标阈值都不是可直接主张的新步骤。若将来提出“测序质量信号→特定漏筛判别→受约束 HF 核查”，仍须逐项说明与最接近流程的输入、判别和实体测定动作区别，并用同预算强简单对照证明净效果。眼下没有这种判别公式、可见性证据或效果证据，不能把原论文的计数规则改名当作发明。

## 本轮决定与关闭条件

1. **#113 不进入实验就绪**：目前缺逐变体字段/键映射、数据权利核查、时序/成本、明确区别步骤、强简单对照、数值预算和负责人技术选择。真实训练、held-out、服务器、COS、湿实验均未启动；本轮计算预算为零。
2. 只允许后续一次**元数据级**核验：读取原论文补充表的表头/说明和项目已有 manifest，确认富集前计数是否逐候选匹配、同一 assay 与方向是否一致、取得该计数是否要求先做会改变 HF 预算的实验，并记载许可与再分发限制。不得为找阳性而换任务、追加种子或读 hidden truth。
3. 若计数不可匹配、在决策时不可取得、与漏筛无关、或核验后仍无法写出区别步骤，就将 A 记录为 STOP。B“通用达标候选数选点”已在 #112 暂停，不能自动接替。只有上述条件通过且负责人记录方法选择，才起草固定开发/确认隔离、对照、统计单位、效应阈值、资源上限和止损的正式协议。
4. #114/#115 继续保持 blocked；不得以本审计或 PR 通过替代科学 Gate 或后续实际运行授权。

本文件没有修改任何冻结数据、协议或公开 contract，也不对可专利性给出结论。
