# #113 单场景数据可行性审计：GB1/IgG-Fc 漏筛假设

初稿日期：2026-09-23；审核证据同步：2026-09-25。状态：**执行 NO-GO，技术选择待核实**。本记录是 #112 的后续有界资料审计，不是已冻结实验方案、发明披露书或实际实验授权。[#112 报告草案](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/116)仍待审阅；#113 的负责人选择条件尚未满足。

## 单场景与技术问题

场景限定为 ProteinGym `SPG1_STRSG_Olson_2014` 的蛋白 G B1 结构域与 IgG-Fc 结合测定。问题是：在成本固定的 HF 复测决策前，能否用**当时确实可获得**的测定质量信号识别 LF 预筛会遗漏的有价值候选，同时避免简单取消预筛造成的预算损失。

此处只有待证假设，**没有可实施的区别处理步骤或预期增益数值**。不能把已经观察到的高 DMS 分数、全池 top1% 身份或筛选后测序计数当成选点输入。

## 证据与字段可见性

| 资料 | 已核实 | 对方案的含义/未核实项 |
| --- | --- | --- |
| [项目四 assay 数据准备记录](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/d372f7d54315cdbf028cd8a30affe766e3bcbf7e/docs/data/PROTEINGYM_FOUR_ASSAY_DATA_READINESS.md) | 当前标准化候选对象只有 `candidate_id,mutant,mutated_sequence`；DMS_score 在独立 hidden-truth 对象；zero-shot 分数保留于单独原始对象。 | 当前 replay 没有独立表达量、测序深度/计数、检测限或批次字段；不得绕过 hidden oracle。 |
| [Olson 等 2014 原论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC4254498/)与[出版商原始补充表](https://ars.els-cdn.com/content/image/1-s2.0-S0960982214012688-mmc2.xlsx) | GB1 mRNA display 在 IgG-Fc 富集前后测序；原表有逐变体 `Input Count`/`Selection Count` 列。 [四缺口审计](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/235134cb2bafb2f6cc54f71adf1b644023ab77da/docs/results/research-progress-20260923/GB1_AUDIT.md)和[元数据核查](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/235134cb2bafb2f6cc54f71adf1b644023ab77da/docs/results/research-progress-20260923/METADATA_CHECKS.json)将 535,917 个双突变、1,045 个单突变与项目 536,962 个唯一候选键全部匹配，P1/P2/P3 各 256 个身份匹配；重复和两侧未匹配均为 0。 | **身份映射 PASS**，不等于已校验计数数值、标签变换、字段使用许可或决策时可见性。原表未加入当前 replay 的候选对象。 |
| [M6 固定结果包](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/36bf63dc8aaeab14c71f20406739323099e75fd8/docs/results/m6-results-20260917/README.md) 与 [跨池进度](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/36bf63dc8aaeab14c71f20406739323099e75fd8/docs/results/m6-progress-20260920/README.md) | SPG1 原开发池取消硬筛选有局部收益；全体八项预列跨池标准未通过。 | 只能说明某一开发设定存在筛选损失，不能把它当成运行前诊断器、独立证实或普适技术效果。 |

**决策时序**：序列和既有 LF 代理分数可作为候选特征；已经购买的 HF Observation 只对已购候选可见。原工作簿的富集前计数已在**身份层面**与候选匹配，但“输入样本取自选择前”不等于“输入计数结果在候选决策前已返回”。原方法未证明先获得全池输入计数，再依此决定本轮 IgG-Fc 测定；若作为首轮测定后的复测分配，首轮状态及费用都须计入。`Selection Count`、由前后计数得到的 fitness、同轮无 IgG 对照读数及隐藏 DMS truth 不得提前作为选点输入。输入测序、制库和共享文库测定有实际成本，不能把合成的 1/4/100 价格解释成现实节约。

**信号语义**：[原论文补充方法](https://ars.els-cdn.com/content/image/1-s2.0-S0960982214012688-mmc1.pdf)说明纯化融合物 cDNA 输入用于归一化表达差异；`Input Count` 不能直接视为独立表达量，更不能据此称“低表达掩盖高结合”已被证明。输入计数也是 fitness 构造中的分母；任何未来相关性都要排除共享分母、噪声和覆盖效应，才可能说明对 ESM 漏筛有独立预测价值。当前没有可执行判别规则或净增量效果。

## 对现有技术的最小区别检验

[#112 的 claim chart](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/codex/112-patent-feasibility/docs/proposals/issue-112-patent-feasibility/README.md)已显示，蛋白预筛、主动选择、不确定性/成本感知多保真和达标阈值都不是可直接主张的新步骤。若将来提出“测序质量信号→特定漏筛判别→受约束 HF 核查”，仍须逐项说明与最接近流程的输入、判别和实体测定动作区别，并用同预算强简单对照证明净效果。眼下没有这种判别公式、可见性证据或效果证据，不能把原论文的计数规则改名当作发明。

## 本轮决定与关闭条件

1. **资料级身份审计已完成，#113 仍不进入实验就绪**：逐变体键和三个现有开发池身份 PASS；数值计数语义、数据再利用许可、决策前结果可见性、共享/边际真实成本及新增信号独立价值仍未闭合。没有具体区别步骤、强简单对照、数值预算或负责人技术选择。此结论与[正式审核评论](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/117#issuecomment-5794609176)一致。
2. 后续若有新的具体研究决定，先固定**一个真实决策时点**与字段到达顺序：本轮初次选择或首轮已购后的复测分配不能混同；再列制库、输入测序、富集、追加复测的固定/批量/边际成本和可避免环节，核实原始数据使用许可。只把在决策前确已返回并付费的值作为允许输入。
3. 仅在独立信号价值和可实施区别步骤有证据后，才起草强简单对照、开发/确认隔离、统计单位、效应阈值、资源上限与止损的正式协议。若这些条件不成立，A 记录为 STOP；B“通用达标候选数选点”不能自动接替。不得换任务、追加 seed 或读取 hidden truth 搜索阳性。
4. #114/#115 继续保持 blocked；本轮未新增科学拟合、回放或正式 Gate，也未授权真实实验。资料审计通过不等于科学效果或专利判断。

本文件没有修改任何冻结数据、协议或公开 contract，也不对可专利性给出结论。
