# #112 专利方向可行性初筛：技术证据、现有技术与披露边界

记录日期：2026-09-23。对象：当前公开数据蛋白候选发现 benchmark；拟申请地区暂按中国发明专利。**结论：优先路线为“特定测定场景中的漏筛修正”，但状态仅为 REVISE／有条件继续；通用“达标候选数驱动选点”在当前定义下暂缓。现在没有可据以主张稳定增量效果的新增技术步骤。** 这是一轮有限初筛，不是新颖性、创造性、权属或自由实施法律意见。

## 1. 证据层级与原始入口

| 单元 | 已实现/执行 | 主结果及反例 | 当前可支持的陈述 |
| --- | --- | --- | --- |
| M4 | Gate 3 已完成，终态 STOP；见 [项目状态](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/19d929c7cf42679a8f3b95104c363e6b6f4bd45e/docs/STATUS.md) | 不能把工程通过升级为科学 PASS | 单保真普遍探索优势未建立。 |
| M5 | A/B/C/D、原因分析及六任务 R/S 已整理于 [PR #110 的固定阅读包](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/03aad1694f9d070c1d9da95cb3b0fe78470d2326/docs/results/m5-closeout-20260913/README.md)；PR #110 未合并 | 最新六任务仅 Alpha 满足两主比较×两批完整标准；旧阳性、失败复现及 HXK4 S 批不明确均保留 | 预测改善不能替代发现评价；存在有限训练信息条件效应，不证明普适探索收益或统一机制。 |
| M6 E4/E5 | 执行源码 `fd68935bf97cd7811c66f733066aa8e3677cb1dc`；[综合结果](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/36bf63dc8aaeab14c71f20406739323099e75fd8/docs/results/m6-results-20260917/README.md) 随 PR #111 审阅 | E4 的 48 主比较：5 positive、1 negative、27 unstable、15 inconclusive；E5 的 24 主比较：0 positive、2 negative、4 unstable、18 inconclusive。SPG1 去硬筛选在原开发池主终点 +66.7pp、五 seed 同向，其他任务和终点反例保留 | 局部筛选损失可观察；通用多保真或取消筛选优势未建立。购买单位不是现实货币。 |
| 独立池与新增规则 | [2026-09-20 开发进度](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/36bf63dc8aaeab14c71f20406739323099e75fd8/docs/results/m6-progress-20260920/README.md) 及 [九单元来源身份](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/36bf63dc8aaeab14c71f20406739323099e75fd8/docs/results/m6-progress-20260920/EVIDENCE_MANIFEST.json) | 180 条独立池轨迹的八项预列跨池标准均未通过；CAPSD 有筛选阻断迹象，GFP/P2 去筛选五次均变差。动态放行四轮、简单组合对照及购买前可靠性规则都已执行；新控制器没有增量主发现收益，可靠性规则与普通估值 24 次主差全为 0 | 可定位风险，不具备运行前稳定预报器；已停止版本不得作为“待实现新方法”。 |

PR #111 及 #107 的正式科学验收仍待指定协作者；各单元为 ITERATION/L1。上述数字来自绑定的紧凑摘要，本次未打开原始 truth、重跑模型或独立复算全部 ensemble。[来源与复验边界](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/36bf63dc8aaeab14c71f20406739323099e75fd8/docs/results/m6-results-20260917/PROVENANCE.md)已经区分 260 条唯一 E4 轨迹、140 条唯一 E5 轨迹和被复用的引用。种子/检查断言不是独立蛋白样本。

## 2. 检索记录与范围

检索日期 2026-09-23。入口：Google Patents 的公开申请/授权文本与家族表、原始论文页（arXiv、期刊作者版本）、国家知识产权局法条/审查指南。所用查询按主题如下，先看标题摘要，再核权利要求或论文方法；未发现有用命中的泛化噪声结果不纳入 chart。

1. `protein engineering dynamic screening active learning candidate rescue low fidelity high fidelity`；`protein directed evolution prescreening Bayesian optimization mutant patent`。
2. `active learning threshold hit discovery protein experiment selection`；`probability threshold protein variant experiment`。
3. `multi fidelity cost aware active learning protein sequence`；`protein false negative screening active learning`。
4. 由最接近的 [US20240339175A1](https://patents.google.com/patent/US20240339175A1/en) 做一轮家族/引证追溯：同组 CN115116539A/B、WO2023216747A1、JP7746656B2 视为同一技术族，不按四个独立发现计数；[CN111598616B](https://patents.google.com/patent/CN111598616B/zh) 涉及通用对象集合筛选、以广告为实施例，因缺蛋白测定关联仅列广义背景。检索到 [CN116913393B](https://patents.google.com/patent/CN116913393B/zh) 的强化学习蛋白进化，未开展全文对照，列为后续正式检索项目。

本轮未覆盖所有分类号、未公开申请、全部中文同义词、所有法域及完整审查档案；Google Patents 的法律状态仅作线索，最终状态由代理师按官方登记核实。“没有检出”不等于具有新颖性。

## 3. 初步 claim chart

以下“拟区别”是**待发明的假设**，不是已有实现。专利权利要求与论文方法分开对比；未做侵权/自由实施判断。

| 来源与状态线索 | 精确可比内容 | 与当前设想重合 | 目前尚缺的区别及效果 |
| --- | --- | --- | --- |
| [US20240339175A1](https://patents.google.com/patent/US20240339175A1/en) 权利要求 1–2；同族 CN115116539A/B（Google 显示 CN 授权、US 申请仍待审，须官方核状态） | 根据预测指标预筛对象，再据实验值与特征确定目标；从属项明确突变蛋白。说明书讨论 ODBO 和 top1/2/5% 候选比例 | “先筛蛋白候选再主动选择”高度重合 | 若想保护漏筛修正，须有具体且运行前可观测的漏筛诊断和由此产生的不同操作；现有开发研究没有证明这一点。 |
| [WO2024000579A1](https://patents.google.com/patent/WO2024000579A1/zh) 权利要求 1；PCT 公开，Google 标记 ceased，国家阶段另核 | 蛋白/生物序列适应度模型、不确定度、UCB、突变子空间及迭代种子 | 普通 UCB/探索/突变库已是公开手段 | 仅把其采样替换为本项目 LF/HF 或阈值，不足以陈述实质区别；需单独证明技术问题和效果。 |
| [WO2026046536A1](https://patents.google.com/patent/WO2026046536A1/en) 权利要求 1、15–18；PCT 公开 | 结构模型质量分数筛蛋白 binder，阈值可由实验分数确定 | “按可观测分数阈值筛候选/决定实体实验”有重合；其具体对象和模型与本项目不同 | 若是测定混杂/漏筛校正，应说明新信号与阈值/购买动作的技术关联；当前没有独立表达、检测限或批次效应证据。 |
| [US20100023465A1](https://patents.google.com/patent/US20100023465A1/en) 权利要求 1/4 与说明书；美国公开申请，Google 标记 abandoned | 主权利要求选取已知/未知标签的相似性或规则学习；说明书将达标阈值定义为 desired data，并讨论蛋白相关化合物筛选 | “达标命中”是已有主动学习目标概念，虽非同一蛋白序列方案 | 泛化“最大化达标数量”缺具体新流程。不能把说明书例子误称为其独立权利要求。 |
| [US20260105987A1](https://patents.google.com/patent/US20260105987A1/en) 权利要求 1 与说明书；美国公开申请、状态待审线索 | 蛋白序列变异、实验测定、LLM embedding、上层预测器及反复选点；说明书以超过阈值的活性百分比评价 | 蛋白小样本迭代筛选与阈值评价已有公开 | 本项目若仅改评价成“达标数量”，难证明新增技术效果；该申请的模型架构不同，不能断言完全覆盖。 |
| [ODBO 原论文](https://arxiv.org/abs/2205.09548)；[多保真成本 BO 原论文](https://arxiv.org/abs/2211.02732) | 前者公开蛋白库预筛与 BO；后者公开成本感知多保真及严重偏置来源识别/排除 | “蛋白预筛＋BO”和“识别有害 LF”作为宽泛命题已有技术启示 | 需要与当前失败模式对应的具体输入、判别、干预及可重复效果；论文是非专利文献，不能当作具体权利要求。 |

## 4. 两条路线的决策

### A. 特定测定场景中的漏筛修正：REVISE／唯一优先研究方向

**技术问题**：硬筛选可能把真正有价值的蛋白候选排除在 HF 测定之外；直接取消筛选又可能浪费 HF 预算。当前结果支持这两种损失都存在。

**拟有用输入**：候选序列/公开代理分数、已购买 Observation，以及某一测定流程中客观取得、与漏筛原因有关的额外信号。后者尚未选定或验证，不能以隐藏 DMS truth、事后 top1% membership、同任务回放命中或人为造出的 oracle 风险分数替代。

**可能的处理/动作**：由额外信号诊断一种具体失配，并在同一 HF 预算下决定候选资格或核查；与当前简单“全池均值”“硬筛选＋固定核查”和现有专利流程对照。此处只是可检验的研究框架，未形成具体算法，也没有可主张的技术效果。

**进入 #113 的必要条件**：找到一种明确的蛋白测定、公开可用且许可清楚的配对数据、运行前确实可观测的额外信号、和对现有专利有实质区别的处理。当前已登记的 [ProteinGym 四 assay 标准化对象](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/main/docs/data/PROTEINGYM_FOUR_ASSAY_DATA_READINESS.md)包含 DMS truth、zero-shot 分数与候选序列，但其标准化输出没有独立表达量、检测限或批次层信号；不能据此推断原始来源永远没有其他元数据。若不能满足条件，A 应停止，不再叠加第五版释放控制器。

### B. 预定义达标候选数驱动 HF 选点：当前 STOP／仅保留备选问题

当前没有经实验证实优于“固定 LF 后按均值选择 HF”的区别步骤。已有 M5 的二值上尾代理及排序目标也未建立稳定增益，见 [M5 收尾](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/03aad1694f9d070c1d9da95cb3b0fe78470d2326/docs/results/m5-closeout-20260913/README.md) 中 E19/E20；现有专利/论文已经使用达标定义或阈值评价。全池 top1% 的定义属于 evaluator，不是运行时可输入的“技术阈值”。若未来独立出现非事后、与测定技术需求相连的阈值及具体新步骤，须重新对比现有技术，另作明确决定；本次不启动 B 的拟合或选点实验。

## 5. 披露与权属台账

| 时间/渠道 | 已知事实 | 仍待核查 |
| --- | --- | --- |
| 2026-09-13 至 09-20 的 #109/PR #110、#107/PR #111 | 结果包进入 GitHub 仓库；2026-09-23 仓库查询为 private；PR #110/#111 仍开放，#111 为 Draft。 | GitHub 历史可见性、历史克隆/分享、各时点访问者与保密关系；private 不能证明“从未披露”。 |
| 2026-09-23 负责人回复 | 负责人确认相关方法或结果“没有任何发表披露”。记录为当事人陈述，涵盖其知情范围；本轮没有外部披露证据。 | 各贡献者、共同权利人及历史接收方是否一致确认；具体首次公开时间若未来发生须留证。 |
| 本地软著准备材料 | 本地 `output/software-copyright/v0.1.0/` 有 V0.1.0 冻结清单及草稿，基准 commit 为 `d372f7d54315cdbf028cd8a30affe766e3bcbf7e`；清单写“待共同著作权人确认”，未见提交回执。 | 软件著作权人的法定身份、合作开发/职务归属及实际提交情况，须由当事人确认；软件权属不自动决定具体专利发明人。 |
| 专利申请 | 目前未见本项目专利申请号或提交回执，现有进度包明确“软著/专利均未提交”。 | 拟申请人、具体技术贡献者与发明人、职务/委托关系、共有约定、申请地区、保密审查适用性。 |

[中国专利法第 6、8、22、24–26 条](https://www.cnipa.gov.cn/art/2020/11/23/art_97_155167.html)分别涉及权属、授权条件、有限公开宽限和充分公开；普通主动公开不可默认获得六个月宽限。[2025 年审查指南修改解读](https://www.cnipa.gov.cn/art/2025/12/4/art_66_202935.html)要求 AI 应用说明输入、输出与技术场景的内在关联，单纯换场景或改名称通常不足。正式申请前由具备资质的专业人士核对这些条件。

## 6. 交给 #113 的边界

建议 **只做一次有界的数据/场景可行性审计**：先不读 held-out、不拟合、不运行服务器；检查 A 的额外信号是否存在、可合法使用、与漏筛机制相关、且可在决策时取得。若无，#113 给出 NO-GO 记录，#114/#115 不启动。若有，再提出一个方法和强简单对照，前瞻冻结预算、效果及止损。B 当前停止，不因 A 失败自动转入 B。

本报告完成的是资料初筛与有限建议。正式检索、申请范围、发明人/申请人及提交时机仍须实际负责人和专利代理师核实；没有任何方法被认定可授权。
