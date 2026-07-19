# 复现要求

## 最小 Provenance

每条正式运行至少绑定：

- full Git commit；
- Python/environment 或 container digest；
- dataset manifest hash；
- split hash 与 feature hash；
- model/acquisition config；
- seed、budget 和 batch size；
- runtime attempt 与 compute metadata；
- 输出文件 SHA-256；
- code-visible success marker。

## Run identity

`run_id` 由 canonical `RunSpec` 内容计算，不包含提交时间、机器临时目录等不稳定信息。相同
输入定义必须产生相同 `run_id`；不同数据、代码、配置或 seed 必须产生不同 ID。

## Artifact completion

Worker 先写入临时对象或本地临时文件，校验成功后再发布最终对象。所有必需产物存在且 checksum
一致后才能写 `_SUCCESS`。汇总程序不得读取没有 `_SUCCESS` 的运行。

## 结果冻结

冻结结果写入新的 versioned release prefix，并保存 release manifest。禁止原地覆盖已经用于
图表、报告或论文的 artifact。协议变化通过新 config version 和 superseding ADR 表达。
