# Experiment configuration

配置按照用途分为 `smoke`、`development`、`pilot` 和 `frozen`。配置文件进入正式执行前必须
通过对应 JSON Schema，并在 RunSpec 中以 Dataset、Processing、Split 和实验配置的 SHA-256
绑定。

- `smoke`：只验证代码路径和 deterministic behavior；
- `development`：允许方法开发和调参；
- `pilot`：测量实际资源、失败率与实验矩阵；
- `frozen`：协议冻结后的主结果配置，不允许原地修改。

`held_out_authorized` 默认必须为 `false`。只有对应 scientific gate 和人工授权完成后才能创建
新的授权配置版本。

## Dataset source inventory

`configs/datasets/` 保存通过 Dataset Onboarding Issue 审核的官方下载计划，包括
canonical URL、发布方 checksum、byte size、task-level license 和 COS object prefix。
Source inventory 只决定 raw ingest，不替代 Dataset、Processing 或 Split Manifest。
