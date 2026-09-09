# M5/E3：A/B已有比较的口径与可用性

2026-09-09，只读材料核对。A与B共同使用Alpha Amylase、IRED两个任务，不重复计数。
此次不读取A/B新的候选真值，不重跑已消耗授权的M2/M5正式实验。

## 已有固定排序规则的预测器比较

六份M2 RunSpec确认：每任务三个模型的feature、dataset、processing、split、experiment、
environment、seed以及static acquisition身份相同；变化的是模型及其配置。
评价器在相同候选池计算Spearman，并按预测均值降序取名义k计算高值recall。
这支持同任务固定排序规则的模型包比较，但不是仅改变某一输入的受控干预。

| 任务 | 模型 | 全池Spearman | top1%高值recall |
|---|---|---:|---:|
| Alpha | Ridge | 0.102183 | 0.000000 |
| Alpha | RF | 0.107530 | 0.086957 |
| Alpha | XGB | 0.288359 | 0.086957 |
| IRED | Ridge | 0.104306 | 0.047619 |
| IRED | RF | 0.149336 | 0.000000 |
| IRED | XGB | 0.157354 | 0.047619 |

以上直接转引[已接受M2汇总](../M2_GATE_B_REVIEW_PACKET.md)，数值按原文精度，不伪造新CI。
例如Alpha的XGB比RF全池相关性高约0.181，但top1%召回相同；IRED的RF相关性比Ridge高，
召回反而较低。这些是固定规则下预测与发现排序不一致的历史案例，不足以确定其根因，
也不证明任何模型或探索策略普遍无益。

## 不能直接与M5第一步配对的原因

| 比较轴 | M2静态证据 | M5A/B第一决策点及当前C |
|---|---|---|
| 训练状态 | 原固定development/validation划分，三模型共享身份 | 原32/64候选池初始观测、十seed |
| 排序与预算 | 静态全池排序；每阈值名义k；top1%对应Alpha23、IRED21 | 固定批量16，初始观测排除出剩余池 |
| 模型变化 | Ridge/RF/XGB模型包比较 | 原A/B固定模型改变策略；C固定模型改变输入 |
| 不确定性 | 一套静态结果，不提供配对seed效应区间 | 以seed为单位，保留32/64嵌套 |

M2 RunSpec的initial_sample_size/budget/batch_size=1是静态协议字段，不代表只训练或只评估
一个标签，不能拿它与M5测量预算直接对齐。原high-value评价器的名义k随阈值改变；
因此M2 top0.5%/1%/2%曲线还同时改变排序截断，不能冒充C固定16批量的阈值敏感性。

## 服务器已有产物核对

A正式attempt的aggregate-A/B保存逐轨迹六轮汇总（指标、排名诊断、重叠），未保存全池
预测数组；B成功attempt保存result/audit/inventory，其结果也是分支差汇总。
这些文件不能直接生成“同一M5状态、新旧预测器”的对照。
六份M2 sealed prediction的路径及哈希在[known-input index](../../manifests/experiments/m2/issue-58/gate-a-known-inputs.v1.json)
中可追溯；本轮检查的A/B运行目录与已知迁移scratch未定位其可直接使用的本地缓存，
不据此断言远端正式产物丢失。没有重新访问COS或扩张真值读取范围。

当前A/B历史固定规则证据已纳入共同表；A/B单步受控输入干预仍缺。若必须补齐这一层，
下一动作是具体化现有良性任务的输入变化及candidate-pool专属真值入口；原完整truth文件
的读取器会先解析所有标签再筛选，不能沿用它并声称只读candidate-pool。无需因此阻塞
已获批的C训练信息诊断，也不能声称A/B已完成与C完全相同的实验。
