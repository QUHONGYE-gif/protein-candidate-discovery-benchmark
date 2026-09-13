# M6 E4 服务器资源预检：RESOURCE_STOP

2026-09-13，负责人明确允许向指定服务器上传源码并执行。此前源码传输的自动审批阻断已解除；
源码上传、哈希回读和一次合成资源预检已完成。真实 E4 轨迹仍为 **0**。

## 证据与身份

- 执行源码：`3823c58259131046e0bf806c7c51417c17badbc3`，对应 PR #108 / authority #107。
- 源码归档 SHA-256：`79500eda4c0a4a57a229a863fa4d212bddd7da14dd5debffc0b3a1697c947025`；
  上传后服务器回读与本地一致。归档约766 KiB，未传入新数据或凭证。
- 环境 SHA-256：`21194a7a0e251e54f0d90973c62dbeeafc5685424f4ce8b945614a96e3a96377`。
  Python 3.11.2、numpy 2.4.6、scikit-learn 1.9.0、xgboost-cpu 3.2.0；三个原生线程环境变量均为1。
- 尝试：`preflight-20260913-001`；UTC 06:41:50 至 06:42:00；exit code **2**。
- 原始紧凑报告：[report.json](report.json)。SHA-256：
  `cc7ec9c640fb87ac7f9526ec242383689057c041d1c023b1fee62d6096c91a14`。
- 独立会话回读服务器哈希与下载字节一致；新本地进程重算环境 digest、工作量外推和停止规则，
  见 [verification.json](verification.json)。未写 `_SUCCESS`。

## 结果

| 检查 | 实测/外推 | 限制 | 结果 |
|---|---:|---:|---|
| 单次最大维度 scorer | 9.961496 秒 | 探针最多300秒 | 已完成 |
| scorer 调用保守上界 | 19,680 次 | 固定130条唯一轨迹 | 未更改矩阵 |
| 总 scorer 时间保守外推 | 196,042.251 秒，约54.46小时 | 剩余约6小时的80%，约4.80小时 | RESOURCE_STOP |
| 峰值 RSS | 145,788,928 bytes，约139.04 MiB | 1.5 GiB | 未超限 |

这是方案规定的最坏维度合成外推，并非已经耗费54小时，也不是实际完整实验耗时的精确预测。
没有根据真实标签或策略表现缩小工作量估计；未超时试跑真实矩阵、未缩减模型或矩阵来绕过检查。
这是资源可行性停止，不能解释为科学 negative、LF 无效或 Gate B terminal。

## 输入状态与剩余工作

同轮只读权限核验显示，两任务已有候选特征文件均为仅所有者可读写的 mode 600，
当前执行账号仍不可读；HF truth 与父 split 的可读位置尚未绑定。未更改文件/目录权限，
未切换账号，未读取 held-out，未访问旧成功轨迹或写 COS。

继续真实执行前需要两项具体解决：

1. 数据持有者提供授权的只读输入，包括候选 features、父 split/processing 身份和方案允许的
   HF 子集；对实际字节核验 hash，不能仅凭文件名与大小认定就绪。
2. 在结果不可见时修订并接受资源可行方案。可先评估保持模型/矩阵不变的实现优化；
   若改变模型、候选数、seed/条件或资源上限，必须记录新版本，不覆盖本次预检。

子集/oracle 接入、完整 append-only runner 与新进程轨迹重算仍未完成；不将预检能力
或现有 scorer/策略实现描述为完整科学执行入口。

## 治理与工程状态

依据 [本次用户执行指示和偏差记录](../../experiments/M6_E4_EXECUTION_EXCEPTION.md)，
Gate A 仍为 `PENDING`，后续审批保留实际时间。源码基线完整 make check 为981 passed / exit 0，
[CI 34743052967](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/actions/runs/34743052967)
SUCCESS；该结果验证工程实现，不替代服务器资源检查或真实输入。

本次只发布资源检查证据，没有科学 Gate 转换或 milestone closeout，不更新 STATUS，
不合并 PR、不关闭 #107、不自动继续 E5。
