# Experiment configuration

配置按照用途分为 `smoke`、`development`、`pilot` 和 `frozen`。配置文件进入正式执行前必须
通过对应 JSON Schema，并在 RunSpec 中以 Dataset、Processing、Split 和实验配置的 SHA-256
绑定。

- `smoke`：只验证代码路径和 deterministic behavior；
- `development`：允许方法开发和调参；
- `pilot`：测量实际资源、失败率与实验矩阵；
- `frozen`：协议冻结后的主结果配置，不允许原地修改。

`configs/experiments/conformance/` 只保存 production contract 的合成一致性 fixture。它们可以
使用明确的 synthetic placeholder method 和最小数组来验证 Schema/loader，但不得作为正式 M2
run config、预算/seed 冻结或 benchmark evidence。

`held_out_authorized` 默认必须为 `false`。只有对应 scientific gate 和人工授权完成后才能创建
新的授权配置版本。

## Dataset source inventory

`configs/datasets/` 保存通过 Dataset Onboarding Issue 审核的官方下载计划，包括
canonical URL、发布方 checksum、byte size、task-level license 和 COS object prefix。
同一目录也可保存经独立 Issue prospectively frozen 的 processing/split machine-readable config；
这类 config 必须以名称和字段明确区分 source inventory，并绑定对应 Dataset/Processing Manifest。
任何 config 都不替代 Dataset、Processing 或 Split Manifest，也不单独构成 COS 或 scientific evidence。
