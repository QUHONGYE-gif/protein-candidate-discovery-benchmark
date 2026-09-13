# M6 E4：执行授权、治理偏差与前置检查

日期：2026-09-13。authority：`github_issue`，#107；实现 PR：#108（Major / semantic）。

## 当前授权及不可追溯边界

负责人在明确获知 Gate A 必须先于正式 truth 读取、自动化不能代替两位科学负责人后，指示：

> 同意，进行真实执行的绑定和所有前置准备，并进行正式执行，后续进行补充审批

本记录据此将方案 `m6-e4-development-v1`、提交
`ddfe1c95cdc235128d7b71b88b51db6c669be996`、文档 SHA-256
`337c56e5786503a08f637d345758e7606fdcda2fa998112b0b4ad44d714ceeae`
作为本次执行范围。对该版本第 9 节，用户已授权真实绑定和执行，不再仅限规划发布。

这是针对本次执行的用户指示，不是已完成的 Gate A 决定，不修改 ADR-0005 或全项目规则。
若执行先于审批，必须记录 `gate_a=PENDING`、`execution_authority=user-directed-exception-20260913`，
并明确与 Governance v2 §4 / ADR-0005 事前授权要求的偏差；不得生成虚构的两人批准、
不得回填早于实际决定的时间，也不得宣称本次是通过完整治理链的生产验证。
后续科学审查可以接受、修订或否决已有证据，不能抹去先执行这一历史事实。

“正式执行”在操作上指真实服务器/真实公开候选输入的运行；证据仍限定为既有两任务的
有界开发研究，不会变成新的独立确认集。成本、预算、130条轨迹、资源限制、E5 排除、
held-out 禁止访问、零 COS 写入和失败后不自动重试等具体范围沿用该方案。

## 当前实现切片

- `e4_scorer.py`：5-member RF ensemble，420+2 列 HF 输入，已付费覆盖，同单位三点 lookahead。
- `e4_policy.py`：独立于旧 synthetic 行为的 E4 策略；计费配对 warmup、2LF:1HF 节奏、
  固定25%全池预筛、value/cost、并列 HF/均值/hash、全零 HF Greedy。
- `e4_preflight.py`：服务器单线程、无真实标签的最坏维度资源检查，5分钟超时，
  6小时预算的80%外推门槛、1.5 GiB 峰值 RSS 检查。
- 新持久化格式仅限 `m6-e4-preflight-v1` 的 `report.json` 与 `report.sha256`；记录源码、
  环境、实际时间、合成检查结果和治理状态。不是 RunSpec，不注册或修改既有 schema。
  preflight 创建唯一新目录，不写 `_SUCCESS`，也不提供科学运行入口。

scorer 调用保守上界为 19,680：HF-only 10×48；fixed_ratio 40×144；
hard_prescreen 40×48（逐次 LF 不拟合）；cost_aware 40×288。warmup 不拟合。
探针 LF=256/HF=64 测最大维度，状态预算7000只为容纳这一合成上界，不是6400科学轨迹。
外推不采用真实结果或策略表现来降低工作量估计；失败时保留检查失败，不能填成科学负结果。

## 输入准备

只读服务器检查已确认现有 Python 3.11.2、numpy 2.4.6、scikit-learn 1.9.0 环境存在。
已定位 Alpha/IRED 候选特征文件，但当前执行账号无文件读取权限，不能写为 hash verified。
HF truth 和父 split 的可读位置仍未绑定；不以路径存在、文件大小或旧 readiness 代替验证。
服务器具体路径只保留于本地操作记录，不写入公开 Issue/PR。

真实运行仍需：取得现有输入可读副本、验证父清单与 split/feature 身份；完成子集/oracle
衔接及 append-only runner 和新进程重算检查；通过资源预检及完整代码检查。
不使用测试 fixture 代替真实输入、不新增源数据下载、不扩大账号权限来绕过不可读状态。

此文记录授权与工程前置工作；不宣称实验已启动、科学证据完成、Gate A/B 通过或 #107 可关闭。
