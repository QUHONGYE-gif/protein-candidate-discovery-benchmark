# 复现要求

## 最小 Provenance

每条正式运行至少绑定：

- full Git commit；
- Python/environment 或 container digest；
- dataset manifest hash；
- processing manifest hash；
- split hash 与 feature hash；
- model/acquisition config；
- seed、budget 和 batch size；
- runtime attempt 与 compute metadata；
- 输出文件 SHA-256；
- code-visible success marker。

## Run identity

`run_id` 由 canonical `RunSpec` 内容计算，不包含提交时间、机器临时目录等不稳定信息。相同
输入定义必须产生相同 `run_id`；不同数据、代码、配置或 seed 必须产生不同 ID。

RunSpec 与 RunAttempt 使用独立 Schema。RunSpec 只保存影响科学运行身份的输入；每次实际
执行都创建新的 append-only RunAttempt，记录实际 commit/environment、compute metadata、
状态、输出 checksum、failure 和 completion marker。

## Artifact completion

Worker 先写入服务器 attempt 内的临时文件，校验成功后再 seal 最终对象。所有必需产物存在且
checksum 一致后才能写 attempt `_SUCCESS`。汇总程序不得读取没有 `_SUCCESS` 的运行。M5+
普通 run 零 COS write；Gate B 后的独立 archive 使用新 prefix、forbid-overwrite、exact inventory
和 fresh readback，并把 archive `_SUCCESS` 作为最后一个写入对象。

## 结果冻结

冻结结果写入新的 versioned release/archive prefix，并保存 manifest。禁止原地覆盖已经用于
图表、报告或论文的 artifact。协议变化通过新 config/contract version 和 superseding ADR 表达。
Governance v2 不授权删除 terminal evidence 或既有 COS raw object。
