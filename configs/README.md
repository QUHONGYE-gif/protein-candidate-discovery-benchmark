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

## M3 protocol candidate

Issue #66 的三份 acquisition config 位于 `configs/acquisitions/m3/`，Alpha＋IRED 两份
Experiment Config v2 位于 `configs/experiments/m3-single-fidelity/`。它们的 exact bytes、size、
SHA-256、Cartesian matrix 和授权边界由
`manifests/experiments/m3/issue-66/protocol-freeze.v1.json` 绑定。

这些文件在独立 exact-bytes review 前只是 protocol candidate。它们不授权 #67 synthetic TDD、
真实 candidate truth、server/COS、Gate A、Gate B 或 formal execution。

## Dataset source inventory

`configs/datasets/` 保存通过 Dataset Onboarding Issue 审核的官方下载计划，包括
canonical URL、发布方 checksum、byte size、task-level license 和 COS object prefix。
同一目录也可保存经独立 Issue prospectively frozen 的 processing/split machine-readable config；
这类 config 必须以名称和字段明确区分 source inventory，并绑定对应 Dataset/Processing Manifest。
任何 config 都不替代 Dataset、Processing 或 Split Manifest，也不单独构成 COS 或 scientific evidence。

## Governance v2

`governance/v2.json` 固定两位 scientific authority 的 GitHub 身份与服务器用户名、
`/srv/pcdb/` namespace、Unix group `pcdb`、`pcdb-runner` / `pcdb-archiver` service account、
分层 backup/RPO、50% target / 70% hard-cap 和 `milestone-archives/v2/` archive policy。文件只
包含身份与政策，不包含凭据。ADR-0005 合入后新授权的 milestone 才使用此配置；当前 #91/M5B
保持原执行边界。服务器部署后必须在目标主机运行 `pcdb governance-check server`，由工具直接
读取 group membership、目录 mode 与 filesystem free space，不接受调用者回填观测值。
