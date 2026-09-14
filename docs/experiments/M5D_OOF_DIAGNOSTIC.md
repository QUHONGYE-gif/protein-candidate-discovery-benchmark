# M5D 第三阶段：初始已揭示观测的 OOF 不确定性诊断

## 授权、决策与现有证据

负责人在第二阶段结果后同意进行诊断。authority=github_issue；repository 为
QUHONGYE-gif/protein-candidate-discovery-benchmark，issue_number=104，
url=https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/104。
authorization_and_acceptance：负责人授权本轮开发诊断，独立审核和验收随后补充；关联 PR #105。
本契约在新诊断结果读取前提交；阶段 ITERATION，不修改前两轮契约或原正式结果。

CONFIRMED：固定F2＋XGBoost时，弱探索有局部收益，较强探索总体下降；原残余池sigma–
绝对误差Spearman约0.145；唯一服务器129.28.97.247，既有三个终态哈希均一致。
INFERRED：初始观测内交叉拟合可检查误差排序，不使用剩余候选标签来训练或校准。
UNKNOWN：该排序能否跨组重现，是否由折间尺度变化驱动，折外样本是否足以形成非恒定输出。

决策问题：固定F2＋XGBoost的bootstrap sigma能否在初始观测的折外预测上稳定识别较大误差？
可证伪假设：较大的sigma伴随更大的绝对误差，最高sigma四分位比全体平均误差明显更高，
且这种关系在多个泄漏组和折内也成立。本轮仅诊断排序，不改变采集策略或宣布发现收益。

## 唯一实验与有效性边界

复用feature-iteration-20260909/full-001的F2矩阵、排序词表与候选身份，原完整终态
SHA-256为69fabeba628fa813a9a6d5b5c08785b86396797ea0a54e66ffff608026acf1ac。
输入处理/划分、9复合物/4组、初始32/64、种子1729–1738保持不变，共180状态。
这些复合物已用于开发，不是新的独立外部确认。只有初始观测标签参与本轮诊断。

四折划分：按digest([m5d-oof-diagnostic-v1, complex, seed, candidate_id])再按ID排序初始
观测，以排序下标mod4分折；与标签无关，不把初始量放入排序身份。每个候选恰好OOF一次。
每折训练24或48个初始观测，预测8或16个留出观测。训练矩阵、拟合和bootstrap仅用该折
训练观测；留出标签、原剩余候选标签及诊断绝不进入spawn模型进程。F2词表保持既有的
可观测候选池构建方式，不因折或标签重新选择特征。

每折复用原XGBoost固定参数：1个点模型＋5个bootstrap模型，sigma=5个成员预测std(ddof=0)。
bootstrap_draws沿用既有算法，protocol_id=m5d-oof-diagnostic-v1，run_seed=原seed，
task_id=complex，model_id=xgboost-cpu-v1，round_index=fold_index+1；训练ID升序。
共720折、4,320次拟合，产生8,640条状态内OOF预测（跨seed重叠，不是8,640个独立样本）。
保存所有折的训练/验证ID、训练标签与特征哈希、点预测、5成员预测和sigma。

## 指标、权重与方向规则

每状态拼接恰好一次的OOF预测，计算sigma与绝对误差的Spearman（并列平均秩），
以及4个折内Spearman的有效值平均。另报告MAE、预测Spearman、mean(sigma)、
mean(sigma)/MAE、sigma为零比例、最高/最低sigma四分位的MAE、最高四分位MAE/全体MAE。
四分位取ceil(N/4)个，按sigma及ID确定；恒定sigma时秩相关与富集均为null；零分母为null。
比较基准为无误差排序的rho=0及均匀选择期望富集=1；不构造新的学习型误差代理。
不得将mean(sigma)/MAE解释成概率校准证明。OOF仅以75%初始样本训练，不与原残余池
相关直接作同训练状态的优劣判断。折内结果用于暴露拼接后由折间差异产生的相关。

先平均复合物内种子与初始量，再平均组内复合物，最后四组等权；同时报告初始32/64。
20,000次组级bootstrap，PCG64(104105)，99.375% Type-7区间只描述，不作显著性推断。
每个组至少90%状态的OOF相关、折内相关及富集有定义，否则整体inconclusive。

预声明开发规则（非科学Gate）：
- positive：组级OOF rho>=0.20，折内rho>0，最高sigma四分位富集>=1.20，至少3/4组
  OOF rho>0且无组rho<0；建议continue到另行设计的验证，不自动修改策略。
- negative：OOF rho<=0且富集<=1；建议stop当前sigma排序方向。
- inconclusive：完整有效结果未获得、有效率不足，或所需汇总未定义；修订诊断。
- unstable：其余情形；建议revise，不把弱相关升级成有效不确定性证明。
0.20与1.20是运行前固定的开发方向阈值，不是通用质量标准。所有组和初始量均完整报告。

## 执行、复核与停止

新study /srv/pcdb/runs/M5D/oof-diagnostic-20260909/，仅在129.28.97.247运行；零COS写入。
先用3BT1_A_U、1CHO_EFG_I、initial32、seed1729做2状态/8折smoke，每折重复拟合一次
必须精确一致；训练/留出及初始/剩余标签隔离测试通过后才全量。full须同源码同输入成功smoke。
累计smoke/full<=1小时，单工作进程/单原生线程，进程树RSS<=1.5GiB、study<=512MiB。
复用已有环境，无新依赖、服务、付费资源或protein-6000操作。尝试不可覆盖；记录干净
Git commit、配置/数据/矩阵身份、实际命令、环境、失败类别与资源。身份/泄漏失败停止；
缺陷修复另建尝试并保留失败，不因诊断负向重跑或增加bootstrap数、折数与模型。

从磁盘核对折覆盖、训练哈希、保存成员重建sigma，以及全部指标和汇总，再以新进程
--verify复核。该回读不重训全部模型；smoke提供重复拟合证据，两者不能混称独立科学审核。
完成固定矩阵即停止。候选级记录仅留服务器，Git发布紧凑结果；独立审核待补充。

最多三个风险：仅四个泄漏组；队列/特征/模型已参与开发选择；OOF训练更少且只检验
误差排序，不能外推到新蛋白、多轮发现或概率校准。无须新增负责人选择即可按上述范围执行。
