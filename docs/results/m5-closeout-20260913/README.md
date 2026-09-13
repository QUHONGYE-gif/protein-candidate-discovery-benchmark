# M5阶段收尾：核心问题、A/B/C/D与原因分析总览

2026-09-13。第4阶段：M5已完成实验的结果汇总与远端协作收尾；指定协作者审批待完成。

**本轮研究工作到此收束，保留条件性结论及未解决问题。**用户已决定不继续扩大泛化；本次只整理已有证据，没有新实验、新统计检验、参数搜索或证据晋升。这里的“收尾完成”指实验材料和结论边界完成对账，不表示所有科学问题已有答案、原正式确认协议全部完成或论文证据已经冻结。

本文是M5全阶段阅读入口。现有证据和统计口径保持原样；历史报告的继续执行建议不再是当前待办。远端协作状态见[项目状态](../../STATUS.md)。

## 1. M5究竟要回答什么

M4没有筛出可以直接进入优越性确认的稳定探索策略，Gate 3的STOP保持不变。M5因此转向条件分析：预测、信息和选点变化在什么条件下产生实际发现价值，而非必须找出一个普遍胜过Greedy的策略。[P]

| 核心问题 | 截至收尾的回答 | 仍不能回答的部分 |
|---|---|---|
| 固定选点策略，预测改善何时增加高价值发现？ | 全池预测改善不能替代直接发现评价。已有固定样本的标签细节干预，在限定任务/学习器/预算下可重复增加G16命中。 | 尚无根据实验前可观察状态就能判定收益的通用规则；没有统一上游根因。 |
| 固定预测与观测状态，探索改变是否含额外决策价值？ | 已把E−G净收益与E−MR相对于随机偏离的收益分开比较。改变很多选点不自动带来收益；尚未建立跨任务稳定的额外净价值。 | 不能宣布所有探索无效、所有方法等价；即时效应不能回答后续再训练的长期价值。 |
| 上述关系怎样随预算、训练信息与任务变化？ | 信息细节与学习器存在有限条件证据；预算、训练量和任务差已直接检验，结果包含局部支持及大量不明确。 | 尚未得到普适最优训练组成、训练数量、预算分配或探索权重；未见任务与真实测量推广仍缺证据。 |

最稳妥的总判断是：**M5建立了可复现的预测—发现差异、有限的训练信息干预证据，以及检验探索额外价值的同状态比较框架；没有建立跨任务统一的收益机制。**

## 2. 阅读结果时先统一术语和数据范围

- **G**：按预测均值选点的Greedy；**E**：当前实验预定的探索规则，早期RF使用Uncertainty，XGBoost使用UCB；后续ridge实验另有固定UCB配置。
- **MR**：保留E与G交集，从G之外随机补足同样数量。早期为64次随机分支的平均，后续部分单元使用精确条件期望；两种统计实现不能混称字节复现。
- **G16**：当前批次按Greedy选择16个候选；“额外命中数”是每批平均高值个数差。早期表中的recall百分点另以任务高值总数作分母，不能误作precision。
- **训练上组**：按已经观测到的训练标签上四分位分组，不是使用剩余池隐藏真值top1%挑样本。
- **M/U/L/F标签臂**：M只保留上下组均值；U恢复上组内部连续细节；L恢复下组细节；F保留全部连续标签。F−L测量下组细节保留时恢复上组细节的作用；U−L直接比较两种保留方式。

同一命中口径下，E−G = (E−MR) + (MR−G)是记账恒等式，不是独立因果贡献分解。E−MR为正也可能不足以抵消放弃Greedy选点的损失；UCB仍使用预测均值，不能仅凭E−MR把收益归给不确定性自身。

| 来源 | 本阶段实际涉及的数据 | 解释边界 |
|---|---|---|
| A/B | Alpha-amylase、IRED；后续候选池分别2262、2070项 | A和B共用两个任务，不能计作四个独立任务。 |
| C | GFP 51714；PABP/PAB1 37708；HSP82/Hsp90 13294；HXK4/GCK 8570 | 后三项是用户批准的替代开发cohort；PABP与PAB1等名称是同一assay的不同简称。 |
| D | SKEMPI 2.0的9个complex、1774候选，按4个保守组汇总 | 原生41维突变特征、目标为负ΔΔG；各complex内分别学习，不是跨complex预测。不能与C合并成同质总体样本。 |

原C正式四项为BLAT、CAPSD、GFP、SPG1；实际GFP先完成，其他三项没有按原正式四项协议执行，随后按用户决定改用PABP/HSP82/HXK4开展L1研究。此范围变化永久保留，不能用替代cohort回填原确认成功。[C0,C1]

## 3. A/B/C/D各阶段的目的、尝试和结论

A/B之前还完成两项诊断：[#83 / PR84](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/84)只读核对320条M4证据，预列谓词支持seed变化、pilot选择乐观、任务/初始化异质性及Gate阻断路径的描述；有效标签不足、预测—决策错位、探索冗余仍不明确。支持描述谓词不是因果解释。随后[#85 / PR86](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/86)执行28 RunSpec随机源示踪，终态MIXED-INCONCLUSIVE；仅Alpha simple-regret达到预定initial主导判据（1/4），internal主导0/4，不能认定某随机源普遍主导或两者等价。M4 STOP保持不变。


| 阶段 | 目的与实际尝试 | 结果 | 这一阶段提供了什么 |
|---|---|---|---|
| **M5A：历史轨迹重建** | 从M4的344条成功轨迹中精确选择160条相关轨迹，排除184条非目标轨迹；逐轮重建预测、探索排序和实际选择。只读既有已揭示观测并重新拟合历史状态，没有新oracle揭示。 | 800/800历史选择ID及顺序匹配；同实现同环境两次聚合字节一致。A已接受并合并。 | 验证历史选点可重建，提供预测排序与探索偏离的逐轮描述，为同状态分支比较提供基础；不单独证明收益因果机制。 |
| **M5B：同状态单步干预** | Alpha/IRED共80个共同状态；固定模型、观测、剩余池与批量16，比较G/E/64个MR，共5280分支。 | 四项主比较按原98.75%区间及等价界值为EQUIVALENT，但两任务及总体机制分类均为INCONCLUSIVE。B已接受为L1探索终态并合并。 | 完成公平的即时策略比较；效果等价不自动意味着行为冗余、无信息偏离或机会成本机制被识别。 |
| **M5C：ProteinGym跨assay检验** | GFP先完成40状态/2640分支；修订三任务各40状态，共120状态/7920分支，另有静态先验与阈值敏感性。 | GFP与PABP为FRAGILE_INCONCLUSIVE，HSP82/HXK4为INCONCLUSIVE；修订三项六个主区间均跨零，没有稳定探索优势或共同机制复制。 | 同状态方法可用于不同景观，选点变化与收益成立应分开评价；原四项正式确认未原样完成。 |
| **M5D：最小外部场景检验** | SKEMPI的9个complex、4组、360状态/23760分支；保留固定RF/Uncertainty、XGB/UCB、G/MR。后续另做特征、beta、OOF和覆盖开发。 | 原两种方法均未同时建立E−G和E−MR正向主结果；后续开发也没有跨组稳定赢家。原执行已完成，科学Gate未据此通过。 | 为条件限制提供外部场景证据；后续同队列开发不能继续冒称独立外部确认。 |

A/B依据既有接受记录及[A/B接受记录](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/91)；C、D对应原结果见[C0,C1,D0]。A/B各自已披露并获接受的执行授权绑定偏差永久保留；收尾不改写历史来源，也不提升证据等级。

### C的原结果值得保留的具体事实

修订三任务E/G每批平均不同选点数为13.125、13.55、9.3，已经明显改变决策。E−G平均命中差分别为−0.100、−0.575、−0.125；对应主区间仍跨零。E−MR点估计为正也未建立稳定优势。因此“探索没有真正改变选择”不能作为这三项结果的共同解释。[C1]

GFP的E与G平均都只有0.2个命中/16，主比较按原等价带等价，但敏感性触发不明确分类。等价带大约允许每批差1个命中，相对这些低命中均值偏宽，不能直接声称实际可互换。[C0]

### D后续四项开发分别检验了什么

| 尝试 | 操纵或诊断 | 结果与保留结论 |
|---|---|---|
| D特征消融F0→F1/F2/F3 [D1] | 从41维计数，依次加入替换对、位点/氨基酸类别、局部残基窗口。 | F2消除全部候选表示碰撞；RF/XGB的MAE约下降9.3%/11.0%，排序也改善；发现收益仍跨组不稳定。F1+XGB负向，其余新增组合不稳定。候选可区分不等于能够泛化。 |
| 固定F2+XGB的beta [D2] | 同一保存预测比较beta=0、0.25、0.5、1、2，不重训。 | 相对G的平均recall差分别为0、+0.8333、+0.8681、−2.6042、−4.2708百分点；弱探索未达到预设跨组改善标准，强探索较差。支持限定队列中的权重权衡，未确定最优beta。 |
| 初始观测OOF [D3] | 四折交叉拟合，只用允许的训练观测诊断bootstrap分歧与误差。 | sigma–误差rho约0.140，最高sigma四分位误差富集约1.258；未达到预设方向标准，结果unstable。有弱误差信息不等于高值发现信息，也不构成概率校准结论。 |
| 位点覆盖分层 [D4] | 对OOF记录比较已见/未见突变位点，并检查共同支持。 | 两个弱组只有0/20与2/20状态具备所需共同支持，不足以直接比较；其他组和单突变子集不能替代它们识别因果。支持覆盖结构限制存在，未证明它造成预测弱。 |

D的后续独立复审接受收窄的开发陈述；原报告中的“审核待完成”不是最新审查状态。最新审查仍不等于当前PR已获GitHub批准或外部正机制确认。

## 4. 原因分析的完整推进：做过什么、得到什么

以下按研究问题分组，保留主要时间顺序。相同保存预测的再次分析不算新的独立实验；新初始化与新assay也分别说明。

### 4.1 先确认：预测能力和探索分歧是否真的有用

| 尝试 | 目的与控制 | 结果 |
|---|---|---|
| 原C汇总与模型诊断 [E0] | 查120状态中的稀疏命中、影响状态、均值与sigma的排序能力。 | Hsp90/GCK均值的高值AUC约0.532/0.488，PAB1约0.653；sigma高值AUC约0.48–0.50。提示弱预测与UQ问题，但为描述诊断，不能判根因或校准失败。 |
| C位置表示 [E1] | 在420组成特征上加可变位置one-hot，同观测/模型/策略。 | 碰撞消失，但高值AUC未明确改善，六项探索增量变化区间均含零。没有形成更强预测对照，不能靠此排除表示或少标签泛化限制。 |
| 固定策略加入ESM1v输入 [E2] | 三任务、RF/XGB、原32/64观测：420→421输入；G/E分别固定。 | 六个模型×任务单元rho/AUC改善、MAE下降；固定G16与E16发现增量均不明确。首次直接建立预测改善而发现收益仍待确定的受控案例。 |
| GFP相同输入扩展 [E3] | 相同420→421干预，40状态并精确重建原基线。 | 没有建立预测改善，G/E发现增量不明确；不能把GFP列作“预测改善却未获益”的案例，也不能据此说先验普遍无效。 |
| 均值/sigma混合分支 [E4] | 复用预测，XGB比较旧/新mu和sigma的四种组合。 | 条件均值效应、sigma效应、交互和总效应的主命中区间均含零，不能将变化单独归因某一项。 |

### 4.2 改善是否出现在正确位置，早期阳性能否复现

| 尝试 | 目的与控制 | 结果 |
|---|---|---|
| 高值邻域、前端及K8/16/32/64诊断 [E4,E5] | 区分全池排序、真值近邻区分和前端命中；保存预测下比较截断。 | PABP近邻AUC下降、其他任务可改善；原PABP/XGB在K32/64有辅助阳性。直接precision64−precision16变化不明确，不能据显著性差异证明“只是预算16太小”。 |
| 输入干预新20初始化复现 [E6] | 三任务/32与64，120新状态；旧新批分开。 | 全局预测改善重现；原PABP K32/64发现增益没有达到复现标准，其他任务也未建立共同正向收益。旧阳性保留，不能选择只报原批。 |
| 保存状态诊断与子集精度 [E7,E8] | 分析收益正/零/负状态、初始高值覆盖、全部指定大小的状态子集。 | 正负响应与零状态并存；部分旧新聚合差明确，部分不明确。子集目录不是总体置信区间，粗覆盖没有解释全部变化。 |
| 同观测ridge对XGB [E8] | 同421输入与允许标签，固定ridge正则强度。 | PABP部分近邻区分改善但主发现不明确；HXK4/64原批G16有+0.85命中信号。属于学习器包对照，不能纯归因正则化；旧ridge另有后述数值限制。 |
| HXK4新40状态与前64交叉 [E9] | 检查原模型收益，并把候选入池和池内排序交叉。 | 主差+0.475[−0.025,1.00]，未达明确复测；固定ridge池重排有条件性响应，不能推广为完整池收益或唯一环节解释。 |
| 固定池ridge强度 [E10] | 原候选池固定，只比较lambda=0.1/1/10。 | lambda10改善整体rho/MAE，主固定池命中变化仍不明确；未找到最优正则或解释原ridge/XGB差异。旧实现限制保留。 |
| 进入与排序的进一步交叉 [E11] | 五个已有信息臂，保存预测下互换前64池与排序。 | 四项主分解未明确区分入池与池内排序贡献；零命中既有池中没有高值，也有池中有高值而未选中，不能据计数定位上游根因。 |
| 改善位置直接比较 [E12] | 原120与复测120状态，比较高值对旧/新前16并集内外负例的AUC变化。 | 九个主汇总的区域改善直接差均含零；复测XGB三个任务对前端竞争者的区分也改善。“只改善外部、前端完全未改善”的简单说法不成立，位置错位未整体排除。 |
| 新60初始化/任务精度检验 [E13] | 三任务、32/64、360状态720拟合，主估计只用新批。 | G16差：PABP+0.133、HSP82−0.050、HXK4−0.025，区间均含零；预测改善继续。PABP指定方向异质性统计有重复支持，但不是因果贡献率。 |

### 4.3 训练信息的数量、组成和覆盖

| 尝试 | 目的与控制 | 结果 |
|---|---|---|
| 同64已获标签库的32→64使用量 [E14] | 同剩余池、相同已付成本80，在原/加先验输入下比较学习32或64条。 | 多个单元全局预测改善，没有固定G16主区间严格排除零；不是多购买标签的成本效益试验。 |
| 固定32训练数的几何覆盖 [E15] | 只按可见组成特征选覆盖样本，控制训练数量。 | 没有建立稳定主发现收益；标签组成与覆盖仍同时改变，不能单独识别覆盖效应。 |
| 高标签占比/标签跨度配方 [E16] | 同64允许库各选32条，比较random、upper、spread。 | 九项G16主召回对比不明确；upper提高训练高标签占比但全池rho下降。保留PABP K8、HXK4 K32及HXK4最好值的辅助阳性，尚未独立重复。 |
| HXK4同位点支持 [E17] | 固定训练数，提高参考前64的位点支持。 | 支持率约14.1%→26.7%，主G16收益不明确；标签均值、范围也变化，不能纯归因支持。固定池内有辅助排序响应。 |
| 标签平衡支持/固定支持改变填充 [E18] | 尽量匹配标签比较支持，或固定支持anchors改变其余标签组成。 | 支持对照和组成对照完整池主收益均不明确；XGB固定参考池有辅助+0.175命中。匹配不完全，不能建立纯覆盖、纯组成或中介机制。 |
| 组成×420/421输入交叉 [E19] | 用户猜想的直接版本：看组成自身作用及其对新增输入收益的交互。 | XGB组成主效应+0.25[−0.05,0.633]、交互+0.117[−0.133,0.433]，均不明确。仍未得到通用有效训练组成。 |

### 4.4 学习目标与标签细节：形成并检验有限解释

| 尝试 | 目的与控制 | 结果 |
|---|---|---|
| 固定样本XGB成对排序目标 [E20] | 用发现相关的排序学习替换原回归目标。 | 部分全池排序改变，但未稳定改善G16；不能据一个排序目标失败排除全部topK目标。 |
| 二值上尾目标、ridge数值修正 [E19] | 以训练上四分位作二值代理，并检查标签变换的预测一致性。 | 二值代理在稳定HXK4配置下损失发现收益；检查同时发现常数列标准化数值问题并修正。修正改变11/60状态命中，不能说无影响；最新稳定实现的信息效应仍存在。 |
| 原M/U/L/F细节干预 [E19] | 同32训练样本/64观测、421输入、稳定ridge，保持分组和组均值只改变内部连续细节。 | HXK4 F−L+0.683[0.183,1.400]，U−L+0.767[0.217,1.450]；U−L全池rho下降。首次给出明确的训练信息条件干预线索。 |
| 独立数值实现/新初始化/C三任务复验 [E21] | primal/dual桥接、新60状态/任务，保留原细节定义。 | HXK4两项主量重复；PABP不明确，HSP82仅U−L支持。完整跨任务标准未达到，不是独立实验室或新生物测量。 |
| 信息×G/UCB/MR [E22] | 两批C三任务固定同状态，在各M/U/L/F预测条件内比较策略。 | 稳定、广泛的E−G/E−MR及信息×探索交互标准均未达到；部分单批阳性未重复。信息对G有用不能直接推导探索受益。 |
| 完整标签使用量×策略 [E23] | 同64允许库比较F32/F64，以及探索净收益/定向偏离价值的变化。 | 两批主发现增量与策略交互不明确；PABP全池rho重复改善。未证明标签量无作用，也不证明64最优。 |
| 真实总预算分配 [E24] | T80比较32初始+48新选与64初始+16新选；T128为辅助，计入初始命中。 | 主净收益未稳定明确；HXK4的T128−T80响应有重复辅助支持，但不跨任务，也不证明N64绝对更好或多轮反馈价值。 |
| 二值代理的目标等价核对 [E25] | 核对M标签与二元目标的正仿射关系，重用已完成结果。 | 解释二值代理会丢失连续细节，HXK4历史F−M支持；属于回溯等价分析，没有新独立实验，不推广到全部发现导向目标。 |
| 420输入、N32五任务两批 [E26] | 去掉额外ESM列并加入Alpha/IRED，检验信息效应跨任务。 | HXK4两主量重复，Alpha部分、HSP82部分、PABP/IRED不明确；广泛标准未通过。信息效应不以额外ESM列为必要条件，未证明两种表示等效。 |
| N32/N64×细节五任务两批 [E27] | 直接检验训练使用量是否增强细节效应。 | 数量×细节交互未稳定重复；N64下HXK4/Alpha两主量当时均双批支持。不能从N64通过、N32不通过推断数量交互。 |
| 学习器×细节五任务两批 [E28] | 同N32/420信息，固定XGB对稳定ridge。 | HXK4的ridge(U−L)−XGB(U−L)两批为+0.483、+0.767，区间均正；其余广泛标准未达到。作用属于学习器整体配置，不归因单一算法因素。 |
| N64有限条件新C批确认 [E29] | 由前两批提出HXK4/Alpha条件，新60初始化和primal拟合，同时保留全部五任务。 | HXK4/Alpha两主量重复，形成当时有限确认；后来的六任务S批进一步收窄HXK4稳定性，不删除旧结果。 |
| 六任务R/S完整固定矩阵 [E30] | Alpha/IRED/PABP/HSP82/HXK4/GFP，N64/420/ridge/G16，各任务两批各60；全部新拟合并独立核验。 | 仅Alpha满足两项主比较×两批完整标准；HXK4的S不明确，HSP82部分支持，其余未通过。详见下一节。 |

这条链保留了用户原排查顺序：位置→稀疏/状态→训练信息→表示/学习→预算→目标；出现受控线索后进行了复现和推广，而非只停在首次阳性。

## 5. 收尾时必须采用的最新细节干预结论

条件固定：同64训练/观测、420组成特征、stable ridge lambda1、greedy16、池内top1%高值定义。R/S各60个新初始化；区间为99.375%初始化描述bootstrap区间，不代表独立生物重复或全部历史探索的家族错误率控制。[E30]

| 任务 | F−L：R额外命中 [区间] | F−L：S额外命中 [区间] | 两主比较的重复情况 |
|---|---|---|---|
| Alpha-amylase | +0.583 [0.267,0.967] | +0.667 [0.350,1.033] | F−L与U−L均两批通过。 |
| HXK4 | +0.967 [0.267,1.817] | +0.283 [−0.050,0.733] | R两项通过；S两项均不明确。 |
| HSP82 | +0.200 [−0.017,0.717] | +0.450 [0.017,1.142] | U−L两批通过，F−L仅S通过。 |
| PABP | +0.283 [−0.017,0.583] | +0.233 [−0.067,0.550] | 未通过完整标准。 |
| GFP | 0.000 [0.000,0.000] | −0.017 [−0.083,0.000] | 未通过；退化区间不证明总体效应精确为零。 |
| IRED | +0.050 [−0.050,0.167] | 0.000 [−0.100,0.100] | 未通过完整标准。 |

Alpha的U−L为R+0.733、S+0.800，区间均正；同一对比的全池rho分别下降约0.018、0.029。该实例支持训练信息的全局排序价值与当前发现价值可以不同向。

预列任务间直接比较中，IRED−HXK4的U−L效应差在两批均为负，提供有限任务条件差异证据；其余预列直接差没有完整重复。不能从“只有Alpha通过”推断Alpha显著优于其他所有任务。

用户“特定组成的训练信息可能更有助于发现高值”的猜想因此得到**一种操作化版本的支持**：保留哪些连续标签细节会影响发现。实际选了哪些训练样本并未改变，上下组信息容量也不同，所以尚未证明优先采集哪类样本更好、上组每单位信息更有价值或下组信息应删除。

D9同类新泛化没有执行：已有执行包和合成核验，受实际目录权限限制，随后用户决定暂缓。D原正式实验和四项开发均已做过，不能将D9未做误写为整个D未做；也不能把C6称为M5全部15任务完整泛化完成。

## 6. 哪些解释被限制，哪些原因仍未排除

| 方向 | 已经限制的简单解释 | 仍开放的解释 |
|---|---|---|
| 改善位置 | 不能说所有预测改善都只在选择区域外，或前端竞争者完全没有改善。 | 改善能否改变临界入选排序、进入与退出的净命中，仍取决于状态；没有共同上游机制。 |
| 稀疏与状态 | 不能把所有结果只描述为同向收益被零状态稀释；最新C6每个主体状态均至少有17个剩余高值，未耗尽。 | 小效应、尾部稀疏、正负状态与估计不精确尚未排除。 |
| 训练信息 | 信息效应不完全等同训练数量；Alpha的标签细节有受控证据。 | 样本组成、覆盖、信息容量、可预测性、噪声及其交互尚未分开。 |
| 表示/学习器 | 仅消除碰撞不保证收益；已知ridge数值问题不能单独解释最新稳定结果。 | 跨位点迁移、非线性/交互、正则化、有效支持、树模型量化等仍可能影响信息利用。旧ridge研究并未全部重验。 |
| 预算 | 未证明“只要把16加大就行”，使用量与真实成本分配已有区分。 | 实际多轮学习、真实成本差异、收益最小有意义阈值尚未验证。 |
| 目标/UQ | 全局指标改善不保证发现；一个上尾二值代理或一个排序目标不构成通用修复。 | 其他topK目标、UQ中的可减少不确定性、与选点规则的匹配及长期信息价值仍开放。 |

因此可以讨论“有证据支持的条件因素”，不宜给六类原因做概率排名，也不能宣称已经找到生物机制。

## 7. 复现、核验和推广究竟完成到哪一层

| 层级 | 已有证据 | 不能冒称的内容 |
|---|---|---|
| 历史选择重建 | A的800/800匹配；多个C/D干预精确重建原基线。 | 不等于独立生物复制。 |
| 新初始化重新拟合 | 输入干预新20/60；细节干预多批以及最新R/S。 | 同公开景观上的随机初始化不是新增assay。 |
| 替代数值实现 | 稳定ridge primal/dual桥接及新拟合；保存参数和预测独立重算。 | 不等于所有方法由独立团队从原始数据全链重建。 |
| 跨任务固定矩阵 | C三项、五任务、最新六任务均保留全矩阵；D原外部任务与开发另列。 | 未达到广泛重复标准；D9尚未执行，已有开发任务不能重新视作未见确认集。 |
| 新上下文科学有效性审查 | 已完成单元的限定声明审查；最新C6全部726状态、1368统计组、20直接任务差及111286特征行核对。 | 咨询accept不是指定协作者的正式PR批准、Gate通过或candidate/frozen晋升。 |

最新C6主体720状态、2880目标拟合；含smoke共726状态、2904目标拟合，另有数值桥接。原文件和全部尝试封存。核验断言数、bootstrap次数、分支数均不增加独立科学样本量。

失败与修正没有被成功结果覆盖：B的attempt-001因来源身份校验失败停止，没有产生可用科学终态，attempt-002才完成；原顺序原因单元的14次attempt为13成功、1次信息拆分接通失败，随后单独修正ridge数值问题；同位点支持单元也保留启动失败及重试计数偏差。它们不能被写成全程首次成功，也不构成独立阳性重复。最新C6自己的三次科学attempt均首次成功。[E17,E19,E30]

统计口径永久分开：B原98.75%区间；C及多数后续单元99.375%描述区间；D按4组统计。某比较区间含零不能说效应为零；某比较显著、另一比较不显著不能替代直接交互。广泛开发下不声称所有历史比较具有统一确认性错误率保证。

样本/标签身份、允许观测与隐藏评分分离、公平比较是运行前和运行中的底线，已有单元按其报告执行核验。隔离多为代码/进程数据流，不是同Unix账户下操作系统拒读；本次收尾没有重新访问候选真值，也没有替所有历史单元作新的安全或原始标签认证。

## 8. 收尾范围、审核和未完成事项

本 PR 汇总已完成的科学工作，并同步当前状态。A/B已接受并集成；C/D和后续原因分析仍按各自开发证据边界解释。本 PR 没有重新运行模型、访问候选真值或升级任何证据。

| 状态轴 | 收尾状态 |
|---|---|
| 实现与运行 | A/B/C/D及已列原因单元的历史源码与成功/失败身份保持原样；D9追加泛化暂缓。 |
| 验证 | 引用既有独立数值、原产物与统计审查；本次核对公开摘要、来源SHA、链接和结果转录。 |
| 科学结论 | 条件性信息收益与预测—发现差异可保留；共同探索优势、统一机制、广泛泛化、多轮收益未建立。 |
| 接受与晋升 | 不修改已接受A/B，不将开发结果变成candidate/frozen；M4 Gate3 STOP不变。 |
| 远端协作 | 现有PR103/105/106保持各自证据身份；新收尾PR等待指定非作者QUHONGYE-gif审批，再决定合并及相关对象处置。 |
| 论文 | 可据已有结果组织条件分析图；本次未生成正式图稿或冻结论文证据，必须呈现失败复现和推广边界。 |

未解决的科学问题在第6节保留；它们不再自动触发追加实验。原C正式四项确认没有被替代cohort补作成功；D原外部结果已执行，未执行的是新增M/U/L/F方法的D9迁移。

## 9. 来源与公开材料边界

[来源清单](sources.json)绑定已有公开报告的不可变commit、原报告SHA，以及本PR历史阅读副本的原始身份。[公开文件校验](checksums.json)覆盖本目录全部其他文件。阅读副本为了公开阅读移除了操作交接、服务器路径与不可公开的制品链接；原报告仍保持原样，不冒称字节相同。

最新C6的[固定完整统计摘要](c6-analysis.json)保持原始字节（SHA见校验表），包含两批六任务、所有主量及20个直接任务差；[独立有效性审查阅读副本](notes/C6_validity.md)说明了此前的核验层级和限制。这个公开包仅含报告与紧凑统计，不包含原始数据、逐候选标签/预测、模型、完整运行制品或重新执行入口；来源SHA是身份依据，不表示这些未发布制品能从本PR独立重建。它是结果呈现与协作收尾，不是完整可独立运行的复现发布包。

- [P](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/M4_TO_M7_REVISED_EXPERIMENT_PLAN.md)
- [C0](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5c-gfp-20260908/README.md)
- [C1](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5c-benign-20260908/README.md)
- [D0](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-formal-20260908/README.md)
- [D1](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-feature-iteration-20260909/README.md)
- [D2](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-beta-iteration-20260909/README.md)
- [D3](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-oof-diagnostic-20260909/README.md)
- [D4](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-site-coverage-20260909/README.md)
- [E0](notes/E0.md)
- [E1](notes/E1.md)
- [E2](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-input-intervention-20260909/README.md)
- [E3](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-gfp-20260909/README.md)
- [E4](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-frontier-20260909/README.md)
- [E5](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-budget-20260910/README.md)
- [E6](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-replication-20260910/README.md)
- [E7](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-state-diagnosis-20260910/README.md)
- [E8](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-generalization-precision-20260910/README.md)
- [E9](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-hxk4-followup-20260910/README.md)
- [E10](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-ridge-strength-20260911/README.md)
- [E11](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-entry-ranking-20260911/README.md)
- [E12](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-selection-region-20260911/README.md)
- [E13](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-state-precision-20260912/README.md)
- [E14](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-training-information-20260909/README.md)
- [E15](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-fixed-count-coverage-20260909/README.md)
- [E16](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-information-composition-20260910/README.md)
- [E17](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-site-support-20260911/README.md)
- [E18](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-label-balanced-support-20260911/README.md)
- [E19](notes/E19.md)
- [E20](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-ranking-objective-20260909/README.md)
- [E21](notes/E21.md)
- [E22](notes/E22.md)
- [E23](notes/E23.md)
- [E24](notes/E24.md)
- [E25](notes/E25.md)
- [E26](notes/E26.md)
- [E27](notes/E27.md)
- [E28](notes/E28.md)
- [E29](notes/E29.md)
- [E30](notes/E30.md)
- [C6_validity](notes/C6_validity.md)

[P]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/M4_TO_M7_REVISED_EXPERIMENT_PLAN.md
[C0]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5c-gfp-20260908/README.md
[C1]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5c-benign-20260908/README.md
[D0]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-formal-20260908/README.md
[D1]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-feature-iteration-20260909/README.md
[D2]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-beta-iteration-20260909/README.md
[D3]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-oof-diagnostic-20260909/README.md
[D4]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/30517e24ca543a123b308f80d199227bf7703215/docs/results/m5d-site-coverage-20260909/README.md
[E0]: notes/E0.md
[E1]: notes/E1.md
[E2]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-input-intervention-20260909/README.md
[E3]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-gfp-20260909/README.md
[E4]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-frontier-20260909/README.md
[E5]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-budget-20260910/README.md
[E6]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-replication-20260910/README.md
[E7]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-state-diagnosis-20260910/README.md
[E8]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-generalization-precision-20260910/README.md
[E9]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-hxk4-followup-20260910/README.md
[E10]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-ridge-strength-20260911/README.md
[E11]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-entry-ranking-20260911/README.md
[E12]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-selection-region-20260911/README.md
[E13]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-state-precision-20260912/README.md
[E14]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-training-information-20260909/README.md
[E15]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-fixed-count-coverage-20260909/README.md
[E16]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-information-composition-20260910/README.md
[E17]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-site-support-20260911/README.md
[E18]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-label-balanced-support-20260911/README.md
[E19]: notes/E19.md
[E20]: https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/blob/8d844c1d384524ee072662562680187a4c2a7a99/docs/results/m5-e3-ranking-objective-20260909/README.md
[E21]: notes/E21.md
[E22]: notes/E22.md
[E23]: notes/E23.md
[E24]: notes/E24.md
[E25]: notes/E25.md
[E26]: notes/E26.md
[E27]: notes/E27.md
[E28]: notes/E28.md
[E29]: notes/E29.md
[E30]: notes/E30.md
[C6_validity]: notes/C6_validity.md
