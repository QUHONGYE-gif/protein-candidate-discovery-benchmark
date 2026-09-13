# M6 E4 四 assay 输入修订与资源取舍

日期：2026-09-13。authority：`github_issue`，Issue #107；PR #108（Major / semantic）。
修订 ID：`m6-e4-proteingym-v2`。状态：**输入替换已授权；资源取舍待决；未启动科学运行**。

## 已接受的变更

用户明确提供已迁移的 `four-assay-unified-v1` 输入目录，并对“将本轮 E4 改用该目录中的
四个 ProteinGym assay，并相应修订输入和矩阵”答复“同意”。据此替换 Alpha/IRED；
保留四 assay 完整队列，不再把找回旧 Alpha/IRED split 作为本轮前置条件。
既有54小时、2个 member worker、RF内部和原生线程各1的操作授权持续有效。
旧方案和操作记录保持原样，本修订不追溯改写旧输入或资源报告。

| task / assay_id | 已核验父 candidate-pool 数 | E4 固定子集数 |
|---|---:|---:|
| BLAT_ECOLX_Firnberg_2014 | 4783 | 256 |
| CAPSD_AAV2S_Sinai_2021 | 42328 | 256 |
| GFP_AEQVI_Sarkisyan_2016 | 51714 | 256 |
| SPG1_STRSG_Olson_2014 | 536962 | 256 |

使用各自 `candidate-pool`，ID哈希域仍为 `m6-e4-pool-v1:task:candidate_id`，task采用表中
完整 assay_id；按哈希升序取256，并列按ID。新的输入身份必须绑定父清单、子集和新特征
产物；不可套用 Alpha/IRED 的 feature artifact hash。候选序列 `mutated_sequence` 可按既有
`sequence-composition-420-v1` 数学定义生成420维特征，但新产物尚未生成或绑定。
父 split 只有 candidate-pool、无 held-out；它提供数据准备身份，不冒充既有 M5C 实验协议。
本文件定义的是新的 M6 开发子集，仍不形成独立确认集。

E4 HF 使用已处理的 `DMS_score`，方向 `higher_is_better`；LF仍由授权子集HF生成可控代理。
本次不采用或比较已下载的 zero-shot 列，不把模拟LF记为E5真实代理，也不宣称M5C已完成。
除输入队列及由其带来的矩阵/跨任务汇总修订外，[原方案](M6_E4_DEVELOPMENT_PROPOSAL.md)
第3–6节的成本、每池大小、初始16 HF、计费LF warmup、RF、四策略、C0–C3、主指标和
删失规则保持不变。四 assay 分别汇总，不把seed当独立任务。

## 输入 fresh readback

[审计报告与可复算脚本](results/m6-e4-proteingym-inputs-20260913/README.md)已核验：

- 四份 dataset、processing、split manifest 的实际SHA-256与原导入证据一致；父引用闭合。
- 四份 candidates 和 membership 的实际size/hash、行数、ID唯一性与双向exact cover一致。
- 所有membership只有candidate-pool；候选表只有ID、mutant和mutated_sequence，无标签列。
- 四份固定256子集hash由两个独立服务器进程得到一致结果，未按结果筛选。

当前不含HF文件hash/标签解析、派生feature矩阵或完整runner验证。隐藏truth和zero-shot文件
未打开。没有新下载、COS写入、旧数据覆盖或科学轨迹。发布的紧凑报告不含序列/标签。

## 54小时下的具体取舍

使用Docker停止后的已保存synthetic探针，2线程最大单次5.495400719344616秒；完整
四任务矩阵按原5 seeds重算，不沿用旧两任务30.04小时PASS。

| 方案 | seeds / task | 逻辑单元 | 唯一轨迹 / HF引用 | scorer调用上界 | 外推小时 | 54小时预检 |
|---|---:|---:|---:|---:|---:|---|
| 保留原5 seeds | 5 | 320 | 260 / 60 | 39360 | 60.0830 | RESOURCE_STOP |
| 建议的54小时修订 | 3 | 192 | 156 / 36 | 23616 | 36.0498 | 可行候选，尚未授权 |

预检门槛是剩余wall的80%，未扣前置耗时时上限43.2小时；估算只代表当前固定最坏维度
外推，不是完整运行保证。执行前仍需对精确源码重新核验资源及计入当次前置耗时。
保持5 seeds需将wall至少放宽至约75.11小时才达到这一余量要求（尚不含前置准备耗时）；
与现有54小时冲突，因此不能启动后靠截断矩阵补救。

建议选择3 seeds：固定1729、1730、1731；保留全部四 assay、四条件、四策略、256池、
成本与模型。逐task方向门槛仍为mean delta >= +0.05 / <= -0.05，一致性从4/5改为
3/3同向（保留至少80%一致性并向上取整）；有正有负仍为unstable，其余inconclusive。
比5 seeds更难判断稳定性，不声称精度相同。这是明确的待接受统计修订，不擅自生效。

无论选择哪种seed数，跨task汇总拟改为：只有四个assay都满足C0 positive、C2 negative，
且各task的C2−C0满足相应seed一致性，才记 `SUPPORTS_BOUNDED_HYPOTHESIS`；部分支持或
方向冲突记 `UNSTABLE / REVISE`，其余 `INCONCLUSIVE / STOP_CURRENT_MATRIX`。
所有逐task/seed结果仍完整报告，汇总不可用于省略异质性。

## 科学解释与后续执行

已读取willzhang对旧exact head `f708730d2506ce740249e4b98ec7dc7feb675a56` 的
[科学审核正文](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/108#pullrequestreview-5190883496)。
该审核正文的限定APPROVE不等于本修订批准，当前API review状态为 `DISMISSED`。
保留其两项解释限制：主差是固定RF+lookahead多保真策略相对HF-Greedy的净发现差，包含
HF规则和warmup机会成本；C2是高值区镜像的总干预，不是等全局Pearson下局部偏差的纯效应。

后续顺序：接受资源/seed取舍 → 固定新方案及配置hash → 生成并绑定新特征与子集 →
HF独立oracle及可控LF接入 → append-only runner、成本账本与新进程重算验证 →
required checks和精确源码部署 → 资源预检 → 有界真实运行 → 全部方向与失败记录审查。
仍需完整runner实现，不把本次输入审计写成端到端执行就绪。

Gate A保持PENDING，既有用户指示执行例外不追溯修改；后续审批记录真实时间。
#107保持OPEN、#108保持Draft。Docker目前仍停止，未删除原三个容器或数据卷。
