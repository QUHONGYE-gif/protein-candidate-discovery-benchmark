# FLIP2 v3 Alpha Amylase 原始数据入库

## 范围与证据边界

本流程对应 GitHub Issue #3，只负责冻结并入库官方原始文件。它不解压或清洗数据，不创建
Processing Manifest 或 Split Manifest，不授权 held-out，不运行 replay，也不构成 scientific
gate 证据。

Canonical source 是 Zenodo record `18433203` version `v3`，DOI 为
`10.5281/zenodo.18433203`。Zenodo record 标记 CC-BY-4.0，但 task 自带的
`amylase/README.md` 明确标记 MIT；本任务按更具体的 task-level README 记录 `MIT`，同时原样
保留 README 与 Van der Flier et al. 论文 attribution。

## 准备本机 staging

正式数据不得写入 Git worktree。建议使用：

```bash
export PCDB_STAGING_ROOT=/absolute/path/outside/repository/pcdb-data-staging
mkdir -p "$PCDB_STAGING_ROOT"
```

执行前保持 Tailscale DNS 接管关闭，并确认 Clash 代理可用。下载工具使用 Python 标准 HTTPS
客户端，会继承当前 shell 的 `HTTPS_PROXY`。

```bash
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
export NO_PROXY=127.0.0.1,localhost,::1

.venv/bin/python scripts/prepare_dataset.py \
  --inventory configs/datasets/flip2/zenodo-18433203-v3/amylase.json \
  --staging-root "$PCDB_STAGING_ROOT"
```

下载先进入 `incoming/**/*.part`。发布方 MD5 和 byte size 验证通过后，文件才以 hard link
排他发布到 `verified/`，同时计算 SHA-256。已存在的 verified 文件必须重新通过校验；不会被
静默覆盖。生成的 Dataset Manifest 会先通过 Draft 2020-12 Schema 和 format validation，再以
排他创建方式写入：

```text
$PCDB_STAGING_ROOT/manifests/
  flip2/zenodo-18433203-v3/amylase/dataset-manifest.json
```

## COS object layout

原始文件写入：

```text
raw/flip2/zenodo-18433203-v3/amylase/<source-filename>
```

Dataset Manifest 写入：

```text
manifests/flip2/zenodo-18433203-v3/amylase/dataset-manifest.json
```

上传必须逐文件使用 `coscli cp ... --forbid-overwrite`。上传后必须从 COS 回读到新的临时目录，
逐项比较 manifest 中的 SHA-256 和 byte size。对任一相同 key 的重复上传必须返回
`409 FileAlreadyExists`。全部 raw objects 验证完成后才上传 manifest；本切片不发布
`_SUCCESS`，因为 `_SUCCESS` 属于完整处理或 run attempt 的完成契约。

## 2026-07-20 raw ingest 证据

- 官方文件：6 个，总计 `257725` bytes。
- 本机 staging：发布方 MD5 和 byte size 全部通过，并生成 SHA-256。
- COS raw 回读：6/6 对象的 SHA-256 与 byte size 全部与 Dataset Manifest 一致。
- Dataset Manifest：已通过 Draft 2020-12 Schema，SHA-256 为
  `657111a8517c8cbc9a17a5ac98964af77839d4fee8efee16438464c9d8347ce9`。
- COS manifest 回读：与 staging 原件 exact-byte 一致，再次通过 Schema。
- 不可覆盖：相同 manifest key 二次上传返回 `409 FileAlreadyExists`。
- 对象计数：批准的 raw prefix 为 6，manifest prefix 为 1，无额外对象。

上述证据只表示 raw ingest runtime 完成。代码仍需 PR/CI 评审，Processing、Split、
replay 和 scientific gate 仍未开始。

## 原始字段边界

官方 CSV 当前可观察字段为 `sequence,target,set,validation`。raw ingest 只记录这些字段存在，
不据此完成 label direction、missing-value、candidate ID、duplicate、split membership 或 leakage
审计。上述责任属于后续 Processing Manifest 与 Split Manifest Issue。
