# FLIP2 v3 Imine Reductase 原始数据入库

## 范围与证据边界

本流程对应 GitHub Issue #8，只负责冻结并入库 IRED 官方原始文件。它不执行数据清洗、
candidate identity、duplicate/near-duplicate audit 或 split/leakage audit，不授权 held-out，
不运行 replay，也不构成 scientific gate 证据。

Canonical source 是 Zenodo record `18433203` version `v3`，DOI 为
`10.5281/zenodo.18433203`。task README 指向上游 Zenodo record `11357236`，声明
`CC-BY-4.0`、first-hand lab provenance，并要求保留 Gantz et al. 的论文归属。外部发布必须同时
保留 FLIP2 release provenance、task README 和上游归属信息。

## 冻结的 source inventory

| Source key | Bytes | Publisher MD5 |
|---|---:|---|
| `ired/README.md` | 632 | `f8461790121caa6a2fcefcc5f20d1f6f` |
| `ired/random.csv.gz` | 194747 | `9f26de3ed0cc925d77604c07784debb1` |
| `ired/two_to_many.csv.gz` | 194734 | `f514fa09bb8ba5fd092d9417ed8642d9` |

共 3 个文件、`390113` bytes。`random.csv.gz` 是上游 reference，不因入库而成为冻结 benchmark
split。

## 本机 staging

正式数据不得写入 Git worktree。使用仓库外 staging，并保持本机网络配置和 credential 只存在于
本地环境：

```bash
.venv/bin/python scripts/prepare_dataset.py \
  --inventory configs/datasets/flip2/zenodo-18433203-v3/ired.json \
  --staging-root /absolute/path/outside/repository/pcdb-data-staging
```

下载先进入 `incoming/**/*.part`。发布方 MD5 与 byte size 验证通过后，文件才通过排他 hard link
进入 `verified/`，同时计算 SHA-256。Dataset Manifest 通过 Draft 2020-12 Schema 与 format
validation 后排他写入 staging；任何校验失败都不得发布 verified 文件或 manifest。

## COS object layout

Raw objects：

```text
raw/flip2/zenodo-18433203-v3/ired/<source-filename>
```

Dataset Manifest：

```text
manifests/flip2/zenodo-18433203-v3/ired/dataset-manifest.json
```

Machine-readable runtime evidence：

```text
manifests/flip2/zenodo-18433203-v3/ired/ingest-attempts/<attempt-id>.json
```

上传逐对象使用 overwrite forbidden。3/3 raw objects 必须从 COS 回读到新的临时目录，并按
Dataset Manifest 重新验证 SHA-256 与 byte size；raw readback 成功后才能上传 manifest。相同 key
的重复写入必须返回明确的 already-exists 错误。运行证据必须绑定精确 Git commit、环境、规范化
命令、object inventory、readback 和不可覆盖结果，且不得包含 credential、proxy secret 或本机
绝对路径。

## 2026-07-20 本机下载证据

- 官方文件：3 个，总计 `390113` bytes；
- publisher MD5 与 byte size：3/3 通过；
- 独立 SHA-256：3/3 已写入 Dataset Manifest；
- Dataset Manifest：通过 Draft 2020-12 Schema 与 repository domain test；
- raw ingest commit：`da8a8bd011b7cbb2f52fc7f02bae6a31fbb38a05`；
- COS raw readback：3/3 对象的 SHA-256、byte size 与本机 verified bytes 全部一致；
- Dataset Manifest：`dataset-manifest.json`，大小 `1599` bytes，SHA-256 为
  `37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643`；COS 回读与 Git
  文件 exact-byte 一致，并再次通过 Schema；
- Dataset Manifest 相同 key 重复上传使用 `--forbid-overwrite --err-retry-num 0`，明确返回
  `409 FileAlreadyExists`，远端修改时间与 ETag 未改变；
- DatasetIngestAttempt：`flip2-ired-20260720T134110Z.json`，大小 `3007` bytes，SHA-256 为
  `bbe790fbfd985f1d1add7d3e124450b48367763e06909bb3bb18c7e4faa08263`；COS 回读 exact-byte
  一致并通过 Schema，相同 evidence key 重复上传同样返回 `409 FileAlreadyExists`；
- 对象计数：批准的 raw task prefix 为 3，manifest task root 为 1，`ingest-attempts/` 为 1；
  没有覆盖或删除对象。

上述证据表示当时的 IRED raw ingest runtime 完成；它本身不建立 Processing、Split、replay 或
scientific gate 状态。后续 processing/split 进展分别见
[IRED processing](FLIP2_IRED_PROCESSING.md) 与 [IRED split](FLIP2_IRED_SPLIT.md)。

## 原始字段边界

两个官方 CSV 当前可观察字段为 `sequence,set,validation,target`。官方 `two-to-many` split
描述为以 0、1、2 个 mutation 的 variants 训练，以更多 mutations 的 variants 测试。raw ingest
只忠实保留上游文件，不据此确认 target 单位与方向、missing-value policy、stable candidate ID、
duplicate 规则、split membership 或 hidden-label authorization；这些责任属于后续 Processing
Manifest 与 Split Manifest Issue。
