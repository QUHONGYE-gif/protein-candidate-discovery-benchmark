# E4 真实开发运行器与证据格式

Issue #107 / PR #108。用户已于2026-09-14明确保留5 seeds、4/5一致性并授权76小时。
输入范围为[四 assay 修订](M6_E4_PROTEINGYM_REVISION.md)，操作绑定operation-v3。
下面是实验内部运行格式，不修改公共RunSpec或既有M0–M5协议。Gate A仍PENDING，执行
依据用户指示例外，不称为完整Governance v2生产验证；不写COS或Gate B归档_SUCCESS。

一次study独占创建目录；无自动重试/续跑。启动先记录源码、运行环境和operation hash，
无标签性能预检通过后才读取HF。输入审计与已发布的四任务父身份精确比较；先取256个
可观察ID，再生成420列特征，最后仅转换子集HF数值。候选/特征、HF、LF及模型观测分离。
所有新产物位于study目录，数据和普通run零COS写入。wall涵盖prepare/probe/run/readback。

每条轨迹保存逐次scorer输出及其已购买State hash、逐次购买event、评价和文件hash；
scorer只见特征与State。新进程从保存的score向量及State hash重算全部选择，再由独立
replay oracle验证LF/HF揭示、成本、停止、全部指标及summary。它验证已保存模型输出的
决策/账本/评价复现，不声称第二次训练全部RF；RF重训确定性由定向合成测试验证，避免
将额外完整RF训练隐藏在76小时预算外。输出score数组按candidate_id排序，以控制存储。

仅全部260条轨迹通过新进程回读、320比较完整、文件hash一致后写本地
`READBACK_VERIFIED.json`；这不是科学Gate B或COS归档_SUCCESS。失败写FAILED.json并保留
部分产物，不能把resource/execution failure当科学negative。策略之间无共享标签或状态。
完整study结束立即停止，不自动E5/M7。任何预算失败保留原失败，无自动缩矩阵。
