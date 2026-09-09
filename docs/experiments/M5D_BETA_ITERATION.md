# M5D 第二阶段：固定 F2＋XGBoost 的 beta 与尺度诊断

## 授权、证据与决策

负责人在第一阶段结果后明确同意后续开发。本轮 authority 为 github_issue，repository
为 QUHONGYE-gif/protein-candidate-discovery-benchmark，issue_number 为 104，
url 为 https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/104。
authorization_and_acceptance：负责人授权本轮开发执行，独立审核与验收随后补充；沿用 PR #105。
本契约在新 beta 结果读取前提交。证据阶段 ITERATION，不修改原正式或第一阶段契约。

已确认：F2 降低平均预测误差，但原 beta=1 的发现收益不稳定；第一阶段已保存预测，
终态 SHA-256 为 69fabeba628fa813a9a6d5b5c08785b86396797ea0a54e66ffff608026acf1ac。
推断：复用同状态预测可隔离探索权重变化。未知：较弱或较强探索能否跨组改善发现，
以及 sigma 尺度和误差排序与候选重排的关系。先由 smoke 验证复用与端点一致性。

决策问题：固定 F2 与 XGBoost 后，仅改变探索权重，能否在不明显损害其他组及最佳发现
的条件下，改善相对 Greedy 和原 beta=1 的 top-1% 即时召回？
可证伪假设：至少一个非端点 beta 在两个对照上均正向，且对原 beta=1 的改善覆盖至少三组。

## 固定配置、比较与有效性

复用唯一服务器 129.28.97.247 上 feature-iteration-20260909/full-001 的 F2、
xgboost-cpu-v1 预测，不重新训练、不校准、不重新构造特征。完整来源产物哈希必须通过；
原处理与划分身份不变。9 个已用于开发的复合物、4 个泄漏组；初始32/64、种子1729–1738、
批量16，共180个同状态。它们不是新的未触碰外部测试集。

固定 beta 为 [0, 0.25, 0.5, 1, 2]，按 mu + beta*sigma 递减、候选ID升序打破并列。
预测接口只允许 ids/mean/sigma/acquisition 四个既有字段，禁止标签或诊断进入选择。
先完成全部选择，之后 evaluator 才读取真值。beta=0/1 必须逐状态精确复现第一阶段 G/E；
beta=1 的全部64个匹配随机选择也必须一致。残余池、状态、候选顺序精确匹配。
每个 beta 保留原64个精确重叠匹配随机对照，总计900个臂状态、59,400个分支。

主指标不变：包含边界并列的 top-1% 即时召回增量；报告每个 beta 对0和1的配对差。
保留 top-0.5%/2%、最佳发现、简单遗憾减少及 E−Matched Random。先平均每复合物内
种子与初始量，再平均组内复合物，最后四组等权。20,000次四组bootstrap，
PCG64(104105)，99.375% Type-7 区间只作描述；不以种子扩充科学样本数。

逐状态诊断：std(mu)、std(sigma)、mean(sigma)、std(sigma)/std(mu)、mean(sigma)/MAE、
预测及sigma–绝对误差Spearman、所选均值/实际目标均值、相对Greedy置换数、
所选候选平均预测绝对误差、被替换候选的真实目标差。分母为零返回null并报告有效计数。
全部尺度都在原目标 kcal/mol 下，比例无单位；不按诊断改变beta或作概率校准结论。

## 预声明判定与停止

每个 beta 报告：
- positive：相对0和1的总体top1差均>0，对1至少3/4组>0、无组损失超过5pp，且相对
  两端点的总体最佳发现差均>=0。
- negative：对0和1的总体top1差均<=0且至少一个<0。
- inconclusive：两个总体差均为零，或未获得有效完整结果。
- unstable：其他情况。
端点不能被判positive，两个端点仍完整报告。判定是开发方向规则，不是科学Gate。
若存在positive仅建议后续验证；有局部收益但没有positive则revise；没有任何正向
对照差则stop。完成固定矩阵后停止，不挑最好beta继续试验或宣布赢家。

## 尝试、资源、复核与范围

新study /srv/pcdb/runs/M5D/beta-iteration-20260909/，使用不可覆盖smoke/full尝试。
smoke为3BT1_A_U与1CHO_EFG_I、initial32、seed1729，共2状态×5beta，重复选择必须相同。
full只能在同源码、同输入、同契约smoke成功后启动。实际命令、干净完整commit、环境、
上游终态及产物哈希、失败类别和资源消耗保存到终态及追加attempts.jsonl。
累计smoke/full墙钟<=30分钟，单进程/单原生线程、RSS<=1.5GiB、study<=512MiB。
复用已有环境；不增加依赖、新服务、付费资源或COS写入；不涉及protein-6000。
身份/泄漏/复现失败立即停止并诊断；保留失败且修正后新尝试；不因负结果重跑。
从磁盘重新生成选择、全部分支指标、诊断与汇总，再以新进程--verify复核后才记执行完成。
完整候选级预测和选择保留服务器，Git仅保存紧凑汇总、契约和溯源记录。

结论风险限三项：只有4组；特征/模型已按同一开发队列结果选择；单轮同状态结果不能
保证多轮或新蛋白泛化。本轮不做新模型、不改指标/预算、不进行外部确认或证据晋级。
