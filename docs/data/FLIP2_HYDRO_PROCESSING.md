# FLIP2 v3 Hydrophobic Core Processing

## 范围与证据边界

本流程对应 GitHub Issue #26，将已完成 immutable raw ingest 的 FLIP2 v3 Hydrophobic Core
（Hydro）转换为可审计、可复现的 multi-wild-type candidate universe，并生成 checksum-linked
Processing Manifest。

本 processing revision 只建立 candidate identity、wild-type mapping、label/field visibility、
source reconciliation、duplicate/near-duplicate audit 和 deterministic staging contract。它不冻结
benchmark split，不授权 held-out，不读取模型或 replay 结果，也不构成 benchmark evidence 或
scientific gate。

## 接受的输入

输入 Dataset Manifest 固定为：

- object identity：`flip2 / zenodo-18433203-v3 / hydro`；
- SHA-256：`5770fd53ff2117888edb5d21bff4f86dd6f9855ef105ce74ddbf2397244b6fcc`；
- raw inventory：1 个 README 与 6 个 gzip CSV，共 7 objects / `2,197,163` bytes。

2026-07-28 的 read-only fresh audit 从 COS 精确读取 Dataset Manifest 与 7 个 raw objects。
Dataset Manifest 与 tracked bytes 完全一致，7/7 SHA-256/size、6/6 gzip integrity 和 Dataset
Manifest Schema 均通过。本次 COS writes/deletes 为 0。

六个 CSV 的字段严格为 `sequence,target,set,validation`，每个文件均包含：

- `24,935` rows / `24,935` unique sequences；
- missing、non-finite target 与 within-file duplicate 均为 0；
- 完全相同的 candidate universe 和 canonicalized target mapping；
- cross-source target conflict 为 0。

`random_split`、`three_to_many`、`low_to_high` 与三个 `to_<wild-type>` 文件仅作为 publisher
relationship/audit inputs。所有 `set` 和 `validation` 字段都写入独立
`upstream-split-metadata.csv`，不得进入 observable candidate features 或自动成为 benchmark
membership。`low_to_high` 是 target-derived metadata，后续 consumer 必须继续隔离。

## 冻结的 wild-type identity

三个组由 source sequence length 和各 `to_<UniProt>` 文件的排他 test membership 共同确认。
reference sequence 来自官方 UniProt entry 的精确 dataset subsequence，并由 SHA-256 固定。该
mapping 不声明三个蛋白之间存在 homology。

| Wild type | Length | Candidates | Reference SHA-256 | Reference present | Mutation range |
|---|---:|---:|---|---|---|
| `P06241` | 57 | 9,972 | `304f8fd4d48558b27bcba3eb1f9e62500be733da34813a060366ad4da0528999` | false | 1–7 |
| `P01053` | 63 | 6,463 | `dd005937bdc1ad8fef672c3d610baae1fa6d395a7d9e0aa8459537d5af07838d` | false | 1–7 |
| `P0A9X9` | 65 | 8,500 | `b969267b954bad343a488f0af8a68c5a097ee3ae9fdf96d48897b217362bad77` | true | 0–7 |

每个组都只在七个冻结位置发生变化；其他 residue 必须与 reference scaffold 完全一致，七个位置的
允许值为 `F,I,L,M,V`。未知 length、固定 scaffold 漂移、未批准 residue、reference presence
漂移或 identity collision 均 fail closed。

## Label 与 candidate contract

accepted FLIP2 artifact 支持的最窄语义是 continuous hydrophobic-core stability fitness source
score：

- `label_direction = higher_is_better`；
- `label_units = source_score_physical_unit_unasserted`；
- target 保留 canonical source decimal，不做 normalization 或 clipping；
- observed range 为 `[-4.76689312547837, 0.119071964022942]`；
- median 为 `-3.20608122700315`，distinct target values 为 `24,745`；
- numeric high-value threshold 只属于 Experiment Config，不由 Processing Manifest 冻结。

Candidate ID 使用：

```text
sha256(
  "flip2:zenodo-18433203-v3:hydro"
  + NUL
  + wild_type_id
  + NUL
  + sequence
)
```

`wild_type_id` 被显式写入 identity preimage，避免把 multi-WT identity 隐式依赖于 sequence
length。输出字段边界为：

```text
candidates.csv
  candidate_id,wild_type_id,sequence

hidden-truth.csv
  candidate_id,target

upstream-split-metadata.csv
  candidate_id,source_key,set,validation
```

observable candidates 不含 target 或 publisher role shortcut。六个 source 只能在 target 相同
时聚合；within-file duplicate、candidate universe drift、target conflict、missing/non-finite target
均 fail closed。

## Duplicate 与 near-duplicate audit

Exact sequence 在每个 source 中必须唯一，六个 source 的相同记录按一致 target 聚合。
Near-duplicate 只在同一个 wild-type group 内使用 full-length Hamming distance 计算；不同长度和
不同 WT 的距离不视为可比较。

| Wild type | Hamming-distance-1 pairs |
|---|---:|
| `P06241` | 36,830 |
| `P01053` | 20,150 |
| `P0A9X9` | 31,684 |
| Total | 88,664 |

这些 pairs 在 processing 阶段允许存在；grouping、cross-role leakage threshold 和 exclusion 只能由
后续 Split Manifest 冻结。

## Deterministic staging 与 publication gate

Production config：
[`hydro-processing-v1.json`](../../configs/datasets/flip2/zenodo-18433203-v3/hydro-processing-v1.json)。

本地 staging 命令：

```bash
python scripts/process_flip2_hydro.py \
  --config configs/datasets/flip2/zenodo-18433203-v3/hydro-processing-v1.json \
  --dataset-manifest /outside-git/dataset-manifest.json \
  --raw-root /outside-git/raw \
  --staging-root /outside-git/staging \
  --git-commit <full-reviewed-commit>
```

CLI 只允许在 Git worktree 外排他创建 staging objects，不包含 COS client，也不允许覆盖已存在的
bundle。相同 Dataset Manifest bytes、processing config bytes、完整 Git commit 和 raw inputs
必须生成相同 artifacts。

正式 COS publication 必须在 implementation、CI 和 human review 完成后另行获得授权，并采用：

1. fresh exact-key input readback；
2. 两次独立 deterministic build；
3. payload-first、Processing Manifest-last；
4. `--forbid-overwrite`、first-error fail-fast；
5. fresh exact-byte/checksum/size/row-count/Schema/domain readback；
6. zero DeleteObject。

当前文档和实现不授权该 formal publication。
