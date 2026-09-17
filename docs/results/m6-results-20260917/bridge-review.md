> 独立咨询原文阅读副本；原始核算脚本、输入和全产物保留在开发实验记录，未纳入本紧凑公开包。此咨询不替代正式协作者审批。

# M6 真实代理政策桥接：独立科学有效性咨询

2026-09-17；第 3 阶段解释结果。结论：**ACCEPT 当前 CLAIM.md v1 的受限 L1 开发期声明**。未发现需要修正科学产物或重跑才能使用当前结论的 FATAL / 未关闭 MATERIAL 问题。这里的 ACCEPT 是 fresh-context 本地咨询意见，不是指定协作者批准、candidate/frozen 晋升、正式 Gate B 或新的实验授权。

## 对象与独立性

按 scientific-validity-review 及项目 reference，以 accepted contract、实际 source/helper/runner 身份、attempts、原始事件/数组及待审 CLAIM/REPORT 为输入。未读取执行者对话，未运行其审计函数，未导入其 evaluator/policy/helper，也未重新拟合 RF、连接服务器或发布 GitHub。自行编写核算脚本，从原始 JSONL、输入标签和保存 ensemble 核算。保留脚本及机器摘要为 review_verify.py、review-checks.json。

主要审查身份：

- contract SHA256 `a6e06cf93af23c0bd847fe52ab9a7ee3e044fe66d6771904a90b2c263038bc70`
- runner SHA256 `4151bb9ad16aadbb3db0796f51f7a68973d76d7142274c785e1c89ec948c159e`
- CLAIM SHA256 `347c55fa29d2f29f403d2eb70631787e53c7826be6e07c08e84881891f95d224`
- REPORT SHA256 `07876dff6320771bce6c2d605fa65c4c274f6d173a4ecc7b440943c2c2b9f2c7`
- summary SHA256 `df1a643e1daeda8c68da74ff7575891c1d813127d9d6f6eb2599b3ca4e42059f`
- 补充检查的 publication-staging/README.md SHA256 `f9eb7c717e68e8249b3cf64c720249afcd4e581d43632240efe043f0421019a5`。

以上及其他输入绑定见 review-checks.json。若科学输入、产物、选择规则或声明改变，本意见不自动覆盖新版本。

## 实际独立核验范围

1. 身份：核对 86 个 source 文件、36 个原输入文件及 4 个原研究元数据文件、4 个真实 proxy 及 receipt、20 个参考共 1000 个 artifact、2043 个新 manifest 条目及完成/读回链。7 个 executed-prelaunch 文件与本地 prelaunch 完全一致。20 组参考确为四任务 × 五 seed 的 LF1 hard_prescreen，全部纳入。
2. 比较资格：从 task/seed 哈希重建初始 16 HF 及完整 LF 购买顺序；每组 3 臂前 272 个 event 与独立预期相同。全部 40 个新臂首态 h/lf ensemble 与 E5 参考逐值相同，三臂 mean 相同；两新臂 B 分数相同。所有 60 轨迹均 317 events、61 HF + 256 LF、最终 6356 费用，45 次自适应选择均为 HF。初始/剩余池、HF 机会、成本与停止条件没有可反转比较的失配。
3. 标签、状态与选点：19,020 个 event 均有正确候选/保真标签、无重复购买、费用正确；2,700 个保存评分状态的 paid-state SHA 与独立重建一致。使用另写排序/回退逻辑核对全部 2,700 个 HF 选择，hard25 使用真实 LF 前 64 个候选及同一 tie 规则。B 非均值首位为 879/900，均值臂为 0/900；B 零信息值状态仅 BLAT 的 3 个，均符合保存记录。
4. ensemble 抽样：事先固定覆盖每 task/seed/arm 的第 0、22、44 个评分状态，共 180 份数组，其中新臂 120、参考 60；独立检查 shape/finite、所有已付费观测 clamping、mean、初始噪声尺度，并用逐查询协方差/三点期望重算 360 个信息值向量（B 或参考原 A）。均在 rtol 1e-10 / atol 1e-12 内一致。**不是全 2,700 份数组的独立公式核查，更不是全模型重新训练。**
5. 指标：按 HF 真值、ceil(top fraction) 且保留阈值并列，重算 60 × 3 = 180 套完整逐事件 recall/best/regret、1600/1856/3200/4800/6400 前缀、首次 recall ≥ 0.5 成本及删失。每池三个阈值目标数均为 2/3/6；全部原 evaluation 和 summary 一致。独立重算全部 24 个配对比较、方向、达标分母、条件成本节省，与原 summary 和本地 analysis 一致。

本核算包含 370,470 次断言（许多是逐事件标量核对；计数不是额外科学样本），0 次 RF 拟合。作者报告的 654,260 项审计不计为本次独立工作。对本地 Python sum 差异采用独立 math.fsum 汇总、1e-13 纯标量绝对核对；配对向量/方向未改变，无结果选择迹象。

代码读取支持逻辑数据流隔离：RF 只接收固定序列特征与 paid State，HF 标签由 replay 的 oracle 按购买揭示；全真值只用于锁定后的指标。特征定义均为 sequence 派生且无 fit_state。没有声称 OS 级隔离；本次也未重新获取 ProteinGym 原文件或独立重做原始序列特征提取，结论依赖已绑定输入的上游身份。

## 核心结果复核

| 任务 | 均值 − B，top1% 差 / 胜平负 / 方向 | 均值 − hard25，top1% 差 / 胜平负 / 方向 |
| --- | --- | --- |
| BLAT | 0 pp / 1-3-1 / unstable | −6.67 pp / 0-4-1 / inconclusive |
| CAPSD | +6.67 pp / 3-0-2 / unstable | +13.33 pp / 3-1-1 / unstable |
| GFP | +6.67 pp / 1-4-0 / inconclusive | +6.67 pp / 2-2-1 / unstable |
| SPG1 | +46.67 pp / 4-1-0 / positive | +66.67 pp / 5-0-0 / positive |

8 主比较为 2 positive / 4 unstable / 2 inconclusive；24 阈值比较为 7 positive / 10 unstable / 7 inconclusive。未用次终点替换主终点。

SPG1 hard25 在全部五 seed 均排除三个主目标；初始命中 [1,0,0,0,1]，后续新增全 0。移除过滤后的均值臂新增 [2,1,3,3,1]；top0.5%/1%/2% 差分别 +70/+66.67/+33.33 pp，均达到合同方向规则。共同已付费起点与相同 HF 次数支持本条件下移除候选限制产生发现收益的完整政策干预解释。它不要求后续状态保持一致。

均值 − B 的 SPG1 top0.5% 只有 3 胜 2 平，属于 inconclusive。CAPSD/GFP 去筛选的 top0.5% 均值分别下降 10/20 pp；两任务 hard25 五 seed 的最终 regret 全为 0，全池均值平均 regret 分别 0.272872 / 0.0360825。BLAT hard25 的 regret 也全为 0。故主召回、最佳值与各阈值必须分别解释。

删失保持正确：SPG1 去筛选的 top1% 比较没有共同达标配对，条件节省为 null；BLAT 均值对 B 的 +43.329% 仅基于共同达标的 3/5 配对；SPG1 对 B 为 2/5、+31.372%；GFP 对 B 为 2/5、−23.008%。这些均非全部 seed 的平均成本效应。

## 结论性边界与处理状态

1. **MATERIAL，当前声明已关闭：开发面板与复用参考不支持泛化/代理单因子结论。** 若把四个已知池、五既有 seed 当作未见 assay 或独立蛋白重复，或把旧模拟与当前真实轮次的差异单归因于 proxy，关键独立性/可交换性假设不成立。证据是 contract 的已知任务/seed 定义、REFERENCE_BINDING 和全部结果；E5 目标排除模式已用于提出后续问题。最小处理是保留已有“开发期、机制跟进、跨轮非 proxy-only、描述性方向”文字。CLAIM/REPORT 及综合总览已经这样处理，无需新增实验作为本轮闭环条件。
2. **MATERIAL，当前声明已关闭：召回优势不可推广为端点全面改善。** 同一轨迹的 CAPSD/GFP 严格阈值和 best/regret 结果会反转宽泛的“取消筛选更好”解释；SPG1 均值对 B 的严格阈值也未达到预列方向。最小处理是共同展示 24 比较、最佳值、删失与阈值差异。当前声明和综合总览已明确保留，未发现隐藏负结果或阈值选择。
3. **DEFERRED：独立确认范围有限。** 保存数组和分数决策的一致性核查能排除多类实现/报告错误，但不独立验证全部 RF 拟合或上游原始数据制备，也不分解 RF 不确定性、三点近似、价值效用等机制贡献。当前文件已禁止独立团队全链重训、唯一原因和全部机制解决的表述；此限制不阻断现有受限开发结论，也不授权或要求追加运行。

## 允许、不允许与建议

允许：在这四个已知 256 候选池、原 RF、固定真实 ESM1v/+1、五配对 seed、LF1/HF100 和预算 6400 下，报告所有任务的完整政策差；SPG1 的两个主比较达到描述性正方向，且去筛选三阈值一致。可说“旧模拟条件下三个任务的正方向在当前真实条件下没有整体重现”，同时明确跨轮不只改变 proxy。

不允许：普遍取消 hard25、B 普遍有害/已被修复、所有真实 LF 无效、纯 LF 因果收益、proxy 替换的唯一因果解释、所有阈值或 best/regret 全面改善、整体平均成本节省、未见任务/新 seed 批次确认、统计显著或等效，以及 M5/M6 统一根因、正式 Gate B 或证据晋升。

置信边界：保存轨迹内部的身份、配对、标签、选点和指标一致性较高；本次观察条件下 SPG1 的干预差距有直接支持。新 seed 批次、其他 assay/代理/模型/预算的效果仍未知，不能从五 seed 描述性规则量化为总体显著性。

建议 **accept** 当前受限 CLAIM v1；证据仍为 **exploratory / L1 ITERATION**，可进入人工阅读与结果整理，未晋升为 candidate/frozen。综合总览“最新真实代理干预”及科学边界忠实于此 CLAIM；旧三个单元仅作为已审结果引用，本次未重审其原始实验。没有额外执行或推广前置条件。
