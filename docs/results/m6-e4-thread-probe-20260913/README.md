# M6 E4 多线程测试：输出一致，当前双核环境仅小幅提速

用户于2026-09-13授权具备root权限的登录和多线程提速测试，随后明确指定root账号。
root登录成功，已只读验证两任务现有候选特征哈希；没有改写旧文件或放宽目录权限。
性能测试以原执行账号运行，避免把提权与性能变化混在一起。没有读取真实标签或运行科学轨迹。

## 执行和复核

- 精确源码：`e1fd7a8dbfd5b38f9430464bc2aba23670ecc2f5`，authority #107 / Draft PR #108。
- 归档 SHA-256：`d512b829246f5427f578381e86f0b895cc37e910d7af17e3ce4c7a1685235860`；服务器校验通过后解包。
- 尝试：`thread-probe-20260913-001`；UTC 06:54:00–06:54:59；exit 0；限制300秒。
- 环境：2 vCPU，约3.7 GiB RAM；Python3.11.2/numpy2.4.6/scikit-learn1.9.0/xgboost-cpu3.2.0。
- [原始报告](report.json) SHA-256：`89b202fdf77227a28ccd8d5f531f1e8eae95c3aa9810650f35b0355edb9aecad`。
- [独立回读验证](verification.json)：下载哈希、6次分数身份、中位数与外推均已验证。

## 保持不变的科学定义

仅对5个RF ensemble member使用1或2个线程。每个member的森林内部 n_jobs=1，
OPENBLAS/OMP/MKL线程限制均为1。64trees、深度8、bootstrap样本、seed、420+2维特征、
模型拟合和covariance归约次序不变。并行任务返回按member顺序收集，避免改变浮点归约。

本地测试证明预测样本、Scores与选择动作完全一致；服务器六次完整Scores序列化SHA-256均为
`3d010c82e2ee4e7aa36cef255927702c7b17da39083d8c82e99c059537ecc749`。

## 实测与时间估算

1/2线程交替各3次，共6次同一最坏维度synthetic scorer，不进行模型或科学参数搜索。

| member线程数 | 三次墙钟耗时（秒） | 中位数 | 按中位数外推 | 按本轮最大值外推 |
|---|---|---:|---:|---:|
| 1 | 10.3373 / 9.9464 / 9.8997 | 9.9464秒 | 54.37小时 | 56.51小时 |
| 2 | 9.5487 / 9.2821 / 9.9036 | 9.5487秒 | 52.20小时 | 54.14小时 |

中位速度比 **1.0417×**，约4.2%吞吐提升（墙钟缩短约4.0%）。累计峰值RSS为150,921,216 bytes，
约143.93 MiB，低于1.5 GiB限制。并行开关已实现为 `RFScorer(member_jobs=2)`；默认仍为1，
以保留旧调用。探针的 `recommended_member_jobs=2` 仅按较低中位数机械选择，
不能解释为统计显著或稳定的大幅提速；两组耗时范围重叠，第3组并行甚至略慢。

时间外推保留原19,680次scorer保守上界，仅替换单位耗时。52.20/54.14小时不是已耗费时间，
也不是真实完整矩阵的精确预测；不含runner I/O、完整回读等额外时间，且实际状态通常小于探针。
两线程仍远超6小时cap的80%门槛，`six_hour_cap_feasible=false`。

## 当前共享负载限制

测试同期存在其他用户Python计算作业，负载约1.9；未停止或修改这些作业。当前账号亲和性为CPU0,1，
后续只读检查的user.slice和用户slice均显示 `cpu.max=max 100000`，未发现这两层的固定单核quota。
这不证明全部层级/整个历史时段没有限制，也不足以区分CPU竞争、线程调度与解释器开销的贡献。
因此上述估算只适用于本轮共享负载，不能称为独占双核上限。不要把4.2%增益线性放大到更多核。

## 输入绑定与后续边界

root fresh-readback已确认候选特征：

| task | size bytes | SHA-256 |
|---|---:|---|
| alpha-amylase | 16306803 | 9a89f12e7c521462a412320937f4455200c75282fae8196d1302b0eac0bf5596 |
| ired | 14923443 | 1573d70651f2f6450bc354960b170dff2b8b19fd4766f1e0fc6a9622f4e6cb6b |

两者与 `manifests/experiments/m2/issue-58/gate-a-known-inputs.v1.json` 一致。
这解决了特征读取阻塞；HF truth、父split及完整输入/runner/readback仍未绑定完成。
root可登录不等于所有输入都存在或已经验证。

可以在后续执行显式选择2个member线程，但当前资源cap仍不满足。
后续应先协调可用CPU或评估保持科学定义不变的重复拟合缓存，再重新做无标签性能检查；
若仍无法满足cap，需要明确的新资源/矩阵版本，不自动将6小时扩大为约54小时。
没有真实实验、COS写入、Gate状态转换、合并或关闭Issue。

工程验证：执行源码完整 `make check` 为 **986 passed / exit 0**，Ruff、mypy、governance-check通过；
[CI34743848524](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/actions/runs/34743848524)
SUCCESS。本次新增5项并行相关测试（含4个非法线程参数用例），M6+oracle定向检查70 passed。
