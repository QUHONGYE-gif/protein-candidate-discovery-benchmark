# M6 E4：54小时预算与Docker停用后的双线程复测

日期：2026-09-13。authority：GitHub #107；Draft PR #108。
用户明确指示：使用指定服务器运算，时间预算调整为54小时，允许多线程，可先关闭Docker。

## 运行设置新版本

[operation v2](../../../configs/experiments/m6/e4-operation-v2.json) 记录本次执行边界：
wall=194400秒、2个member线程、RF内部/原生线程仍1；其他内存/产物/网络/重试/标签边界不变。
这是实验内部操作记录，尚不是完整runner或已注册RunSpec，不表示科学执行已启动。
旧6小时方案和两次历史资源报告保留；只由这个新版本替代本次操作的时间上限与线程设置。

原预检80%规则保留，新上限约43.2小时。[resource-check-v2.json](resource-check-v2.json)
绑定操作记录与新报告SHA-256，扣除本次探针43.718238秒后重新判定，结果 **PASS**。
原始探针代码仍输出 `six_hour_cap_feasible=false`，这是旧6小时诊断字段；
不能将它当成新54小时预算失败，也不能原地改写原报告字段。

## Docker操作与实测

已按用户指示正常停止原先运行的三个Multica容器（前端、后端、数据库），随后停止
Docker service/socket；未删除容器、卷或启动配置。停止后可用内存约3.0GiB。
复测时机器负载很低，但本次同时发生其他计算负载变化，不能把性能改善完全归因于Docker。
恢复服务时应仅恢复原先三个容器，避免启动其他原本未运行的工作负载。

精确执行源码：`e1fd7a8dbfd5b38f9430464bc2aba23670ecc2f5`。
新尝试：`thread-probe-docker-off-20260913-001`，UTC15:14:40–15:15:24，exit0。
[原始report](report.json) SHA-256：
`81e1c6f3772cb67e9693009bbb164d6775582c4d03380ee356b873294d948ab1`。
下载与服务器report.sha256一致，6次Scores身份相同，均无真实标签或科学轨迹。

| 模式 | 单次中位耗时 | 19,680次调用保守外推 | 按本轮最大值外推 |
|---|---:|---:|---:|
| 1线程 | 9.0138秒 | 49.2756小时 | 50.6770小时 |
| 2线程 | 5.4940秒 | 30.0338小时 | 30.0415小时 |

速度比1.6407×；峰值RSS151,310,336 bytes，约144.30MiB。
约30.04小时外推低于新预算的80%门槛，资源可行性检查通过。
这仍是最大维度合成scorer的保守外推，不是已经执行30小时，也不是完整runner耗时承诺。
完整运行仍必须实时执行54小时硬上限和其余资源限制。

## 输入调查及未决项

本轮定位了Alpha/IRED的现有HF缓存及processing清单；两份processing清单实际SHA-256分别为：

- Alpha：`cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec`；
- IRED：`929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba`。

均与方案一致。HF缓存路径存在，不等于HF字节/允许子集已验证；未读取完整标签。
本轮尚未找到这两任务的实际父split/membership缓存。旧COS配置获取Alpha split返回403，
之后按迁移线索改用cos-migration配置的GET也非零退出，不能仅由此前403推断对象不存在。
未写COS，未新下载源数据；小型既有split元数据恢复尝试没有成功取得目标文件。

用户提供的服务器迁移目录对应ProteinGym四-assay数据：GFP/BLAT/SPG1/CAPSD。
已fresh-read Issue #11及评论：它确认四-assay data readiness及后续M5C候选cohort，
不等于已将当前M6 E4的Alpha/IRED方案改为ProteinGym；#107仍将其列为E5后续。
若用户希望本轮采用这批输入，需明确更新任务/矩阵版本，不应把不同数据集的split替换进旧身份。

资源门槛通过后，仍需解决输入选择/父身份、子集与oracle衔接、完整append-only runner及新进程回读。
科学轨迹为0；Gate A仍PENDING，科学结论未产生，#107未关闭、#108未合并。
