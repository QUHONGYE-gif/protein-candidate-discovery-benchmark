# FLIP2 v3 Alpha Amylase Split

## 范围与证据边界

本流程对应 GitHub Issue #19，在已接受的 Alpha Amylase Processing Manifest 上建立
`flip2-amylase-topology-isolation-v1`。它负责 sequence-only grouping、确定性 membership、
leakage audit 与 Split Manifest；不读取 hidden truth，不授权 held-out，不生成 feature，不运行
model/replay，也不构成 benchmark result 或 scientific gate。

输入固定为：

- Processing Manifest SHA-256：
  `cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec`；
- observable `candidates.csv` SHA-256：
  `50c13a23f9fbfe3892b074a1ce7b31eb5537dfe21ceb682baccbf412c0d42aab`；
- audit-only `upstream-split-metadata.csv` SHA-256：
  `be2a3c7f2f4502fc1178391816b56b60b59c3b88ac352c71df4a93be4f44af2a`。

CLI 没有 hidden-truth 参数。任何输入 checksum、size、row count、candidate universe 或 Processing
Manifest 绑定不一致都必须 fail closed。

## 冻结的科学协议

- grouping unit 是 full-length Hamming-distance-1 图的 connected component；
- 3,706 个候选形成 4,311 条 Hamming-1 edges 和 943 个 components；
- 最大 component 含 2,262 个候选并整体作为 `candidate-pool`；
- 其余候选精确分配为 `development=722`、`validation=361`、`held-out=361`；
- exclusions 为 0，任何 component 不得跨 role，cross-role Hamming-1 edge 必须为 0；
- 五个 publisher split 仅用于记录其跨边界 edge 数，不参与 benchmark membership。

该设计把 61.04% 的候选集中到 candidate pool，导致显著 topology/distribution shift。这是预先接受并
必须报告的限制，不能在看到 downstream result 后通过拆组或删除 bridge candidate 修改。

## 确定性字节契约

machine-readable config 位于
[`amylase-split-topology-isolation-v1.json`](../../configs/datasets/flip2/zenodo-18433203-v3/amylase-split-topology-isolation-v1.json)。
它冻结：

- protocol namespace 的精确 ASCII bytes；
- `candidate_id`/`group_id` 的 lowercase 64-hex 编码；
- member count 的 unsigned `uint32` big-endian 编码；
- seed `20260722` 的 unsigned `uint64` big-endian 编码；
- group/order SHA-256 preimage；
- group sorting、hash collision fail-closed 与 role tie-break order；
- Issue #19 中的三组 hash golden vectors 和 assignment-kernel vector。

相同 processing bytes、split config bytes 和完整 Git commit 必须产生相同 membership、audit、
Split Manifest 与 object keys。candidate row order、upstream metadata row order和 process scheduling 不得
改变 membership bytes。

## Split Manifest v1 扩展

Split Manifest v1 增加以下 optional evidence fields，因此旧 v1 manifest 仍保持 Schema-valid：

- `split_config_sha256` 与完整 `git_commit`；
- membership `row_count`；
- audit artifact object key、SHA-256 和 byte size；
- partition/group summaries；
- known limitations。

Alpha Amylase domain validation 对这些字段强制要求，并从冻结输入重新构建完整 expected bundle，任何
manifest 或 artifact exact-byte 差异都 fail closed。如果这些字段未来对所有 Split Manifest 全局必填，
必须创建新 Schema version，不能静默收紧 v1。

## 本地只读 preflight

2026-07-23 从 COS fresh 下载上述三个已接受输入到 worktree 外临时目录。未下载 hidden truth，未写
COS。当前 implementation preflight 得到：

| Role | Candidates | Groups | Largest group |
|---|---:|---:|---:|
| `candidate-pool` | 2,262 | 1 | 2,262 |
| `development` | 722 | 356 | 15 |
| `validation` | 361 | 293 | 3 |
| `held-out` | 361 | 293 | 3 |

同时验证 4,311 edges、943 components、711 singletons、232 non-singletons、second-largest size 15、
cross-role edge 0，以及五个 publisher split 的 crossing counts 与冻结 config 完全一致。

这些结果及其 checksum 是已提交且通过 exact-head CI 验证的本地 implementation preflight evidence，
不是正式发布的 artifact identities。正式 artifact checksum、Split Manifest identity、
server-execution evidence 和 COS object inventory 必须在 reviewed exact commit 上重新生成并单独报告。

## 输出与运行门禁

脚本 `scripts/split_flip2_amylase.py` 只允许在 Git worktree 外排他写入：

```text
splits/flip2/zenodo-18433203-v3/amylase/
└── flip2-amylase-topology-isolation-v1/<protocol-hash>/
    ├── membership.csv
    └── split-audit.json

manifests/flip2/zenodo-18433203-v3/amylase/splits/
└── flip2-amylase-topology-isolation-v1/<protocol-hash>/
    └── split-manifest.json
```

本地 staging、正式 COS publication 与 fresh readback 是三个独立状态。正式 COS 写入必须等实现、CI、
人工审查完成后取得单独用户授权，并使用禁止覆盖、payload first、manifest last 与全新目录回读。Split
Manifest 只拥有 held-out partition identity；每次运行的 `held_out_authorized` 仍由 Experiment Config
拥有，默认保持 `false`。消费者在接受 held-out 授权前必须计算所访问 `split-manifest.json` 精确
bytes 的 SHA-256，并要求其等于 Experiment Config 的 `split_sha256`；缺失或不匹配时必须
fail closed，其他 split 的授权不可复用。
