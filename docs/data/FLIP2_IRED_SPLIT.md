# FLIP2 v3 IRED Split

## 范围与证据边界

本流程对应 GitHub Issue #22，在已接受的 IRED Processing Manifest 上建立
`flip2-ired-topology-extrapolation-v1`。它只使用公开、observable 的 `candidate_id`、sequence
和相对冻结 WT 的 mutation count，负责 Hamming-1 grouping、确定性 membership、leakage audit
与 Split Manifest。它不读取 hidden truth，不授权 held-out，不运行 feature、model 或 replay，也
不构成 benchmark result 或 scientific gate。实现与 fixture 本身不自动构成 server-execution
evidence；Issue #22 后续另行授权并完成的 real-input preflight、正式 publication 与独立 readback
证据见下文，两个状态不得互相替代。

冻结输入为：

- Processing Manifest SHA-256：
  `929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba`；
- observable `candidates.csv` SHA-256：
  `56bea1f4adeba11425582def8aa32e8d5ac93e3a2caefa39474f4612920c0f4f`；
- audit-only `upstream-split-metadata.csv` SHA-256：
  `558c86c98454ceacd3e7c6fa406703ec7d7513eee2a9eebd08e79b3f3778cc93`。

CLI 没有 hidden-truth 参数。输入 checksum、size、row count、candidate universe、reference、
topology 或 Processing Manifest 绑定出现任何漂移都必须 fail closed。

## 冻结的拓扑外推协议

- 8,586 个候选以 full-length Hamming-distance-1 edge 建图；
- 图必须精确包含 9,279 条 edges 和 4,139 个 connected components；
- 唯一非 singleton component 含 4,448 个候选，整体固定为 `development`；
- 其余 4,138 个 components 必须全部为 singleton；
- singleton 精确分配为 `candidate-pool=2,070`、`validation=1,034`、
  `held-out=1,034`，exclusion 为 0；
- 每个 component 只能属于一个 role，cross-role Hamming-1 edge 必须为 0；
- 两个 publisher split 只用于 crossing-edge 和 assignment-disagreement audit，不参与 membership。

该设计刻意测试从一个密集、相互连通的开发拓扑向孤立变体外推，因而具有强烈的
topology/distribution shift：development 占 4,448/8,586（51.8%），candidate pool 仅占
2,070/8,586（24.1%）。这些比例是 manifest-facing 的持久限制。该协议不是普通随机切分，不能在
看到下游结果后拆开巨型 component、删除 bridge candidate 或改用 publisher split。

## mutation-stratified 全局调度

reference sequence 及其 SHA-256
`27fbaa950b2c68b73f7595a2a53e86a8ab627118640b8e181a08b57a324e2fdb`
继承自 accepted processing config。每个 singleton 按完整序列相对 reference 的 Hamming distance
进入 mutation-count stratum。

assignment 先生成一条长度 4,138 的全局整数 deficit schedule。目标容量依次为
`candidate-pool=2070`、`validation=1034`、`held-out=1034`；每一步只在未满 role 中最大化
`i * target - N * assigned`，平局按上述 role 顺序解决。随后按 mutation count 升序遍历
singleton；同一 stratum 内按 ordering digest bytes、group digest bytes 排序并依次消费全局
schedule。不得为单个 stratum 重新启动 schedule，也不得执行 repair、spillover、随机 fallback
或静默 exclusion。

machine-readable config 位于
[`ired-split-topology-extrapolation-v1.json`](../../configs/datasets/flip2/zenodo-18433203-v3/ired-split-topology-extrapolation-v1.json)。
它同时冻结每个 mutation stratum 的 singleton 数与三种 role 配额；任何配额、容量、stratum
或 mutation range 漂移都会失败。

## 确定性字节契约

config 冻结：

- production-v1 config 的 canonical JSON semantic SHA-256
  `e5ca5322bad75d1a9761dfd67b010658a21d6d75a6626d0f7d939043a3d742a0`；
- protocol namespace 的精确 ASCII bytes；
- group member count 的 unsigned `uint32` big-endian 编码；
- seed `20260724` 的 unsigned `uint64` big-endian 编码；
- singleton mutation count 的 unsigned `uint16` big-endian 编码；
- group/order SHA-256 preimage、role 顺序与 tie-break；
- Issue #22 的 7 组 hash vectors、6-token miniature schedule 和 production stratum quotas；
- distinct membership 的 group-ID collision 与 distinct singleton 的 ordering-key collision
  都必须 fail closed。

production loader 必须先验证上述完整 canonical config identity，再解析协议；即使修改后的字段仍
能通过局部类型或内部一致性检查，也不得继续沿用同一个 split ID/version。Synthetic tests 使用独立
的 `fixture-*` identity 和 test-only loader，不能替代或绕过 production-v1 gate。

相同 processing bytes、split config bytes 和完整 Git commit 必须生成相同 membership、audit、
Split Manifest 与 object keys。candidate 或 upstream metadata 的输入行顺序不得改变输出。

## 2026-07-26 正式 publication 与独立回读

在 implementation、CI、人工审查与单独用户授权完成后，正式运行绑定 exact commit
`906ff82813ba73d3d2a1793dc11abcafbe44c3c5`。运行只 fresh 读取三个 accepted observable inputs；
`hidden-truth.csv` 未读取。两次确定性 staging exact-byte identical，protocol hash 为
`9dde9ee30c6a30c5a691eb2eeae4782093245e0cc8d93cdfc72c7c69af765995`。

| Artifact | `row_count` | Bytes | SHA-256 |
|---|---:|---:|---|
| `membership.csv` | 8,586 | 1,221,313 | `5b4a88887e92d9c06e2a67ef90683a367c3551ea7ad5ea67b50d7650c8b131a7` |
| `split-audit.json` | 1 document | 4,703 | `cf199a5a61ede93d70927438495b8adf78157c0b26799e8d1ea58f128d3c9bb6` |
| Split Manifest | 1 document | 3,211 | `9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e` |

publication 使用禁止覆盖的 payload-first、manifest-last 顺序。发布后的 fresh readback 验证 exact
`2 + 1` inventory、bytes、SHA-256、size、row/role counts、Schema 与 IRED domain invariants。
非发布方随后重新 fresh 下载三个正式对象和三个 accepted observable inputs，从 exact commit 重建，
确认正式 bundle 3/3 exact-byte identical、8,586 个 membership rows、角色计数
2,070/4,448/1,034/1,034、9,279 条 Hamming-1 edges、4,139 个 components 和 0 cross-role
edges。正式 [publication report](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/22#issuecomment-5077267677)
与 [independent review](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/22#issuecomment-5078199435)
共同建立 split server-execution/publication evidence。

duplicate manifest probe 在 overwrite forbidden、无自动重试条件下非零失败，随后独立 inventory
仍为 `2 + 1` 且三个对象 bytes/hash 未变。但是该次 COSCLI invocation 没有暴露可解析的
HTTP `409` / `FileAlreadyExists` 字符串，因此证据不声称观察到了精确 409。历史 publication
也不证明当前 credentials 已配置、仍有效或已获新的 COS/server authorization。

该证据不授权 held-out、hidden truth、feature/model/replay、benchmark result、M2 completion 或
scientific gate。

## 输出与运行门禁

`scripts/split_flip2_ired.py` 仅可在 Git worktree 外排他写入 staging：

```text
splits/flip2/zenodo-18433203-v3/ired/
└── flip2-ired-topology-extrapolation-v1/<protocol-hash>/
    ├── membership.csv
    └── split-audit.json

manifests/flip2/zenodo-18433203-v3/ired/splits/
└── flip2-ired-topology-extrapolation-v1/<protocol-hash>/
    └── split-manifest.json
```

本地 fixture validation、真实 observable-input preflight、正式 COS publication 和 fresh readback
是四个独立状态。正式 COS 写入必须等 implementation、CI 和人工审查完成后另行获得用户授权，
并采用禁止覆盖、payload first、manifest last 与全新目录回读。

Split Manifest 只声明 held-out partition identity。消费者必须同时验证 Experiment Config 的
`held_out_authorized=true`、所访问 Split Manifest exact bytes 的 `split_sha256` 和非空人工授权
reference；缺失或不匹配时 fail closed，其他 split 的授权不可复用。
