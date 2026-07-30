# FLIP2 v3 Hydro Within-WT Topology Stress Tracer

## 范围与证据边界

本流程对应 GitHub Issue #27，在已接受的 Hydro Processing Manifest 上建立
`flip2-hydro-within-wt-topology-stress-tracer-v1`。它是一个非 primary、非 M2 数据集的
topology stress tracer，只使用公开且 observable 的 `wild_type_id`、`candidate_id`、sequence
和相对各自 public reference 的 mutation count。它不读取 hidden truth，不授权 held-out，不运行
feature、model 或 replay，也不构成 benchmark result、server-execution evidence 或 scientific
gate。

冻结输入为：

- Processing Manifest SHA-256：
  `e5f1b5b2778192290c99427be56ddf5f59f5a30a03f1cfebb67da137bed7525f`；
- observable `candidates.csv`：24,935 rows，SHA-256
  `f890b4d662890e424ae2ba7efbe70388d30acdfa4176b6e89179a59c6c5efa9e`；
- audit-only `upstream-split-metadata.csv`：149,610 rows，SHA-256
  `dd5ccad2a9c1c2c956a7e279852c669c928e648da940b98e203a716daba2bb71`。

CLI 没有 hidden-truth 参数。Processing Manifest、field visibility、checksum、size、row count、
candidate identity、WT reference、topology、publisher audit 或 config identity 发生漂移时必须
fail closed。

## 三个参考序列与距离边界

Hydro candidate 分属 `P06241`、`P01053` 和 `P0A9X9`。Hamming distance 只在相同
`wild_type_id`、相同 sequence length、同一 public reference 下定义和计算；不同 WT 之间标记为
`incomparable_without_accepted_alignment`，不会补齐、截断或比较序列。

每个 WT 独立建立 full-length Hamming-distance-1 图和 connected components：

| WT | candidates | Hamming-1 edges | components | singletons | non-singletons | largest |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| P06241 | 9,972 | 36,830 | 411 | 350 | 61 | 9,462 |
| P01053 | 6,463 | 20,150 | 484 | 423 | 61 | 5,909 |
| P0A9X9 | 8,500 | 31,684 | 451 | 387 | 64 | 7,971 |
| total | 24,935 | 88,664 | 1,346 | 1,160 | 186 | 9,462 |

完整 component-size multiset、mutation strata 和配额保存在
[`hydro-split-within-wt-topology-stress-tracer-v1.json`](../../configs/datasets/flip2/zenodo-18433203-v3/hydro-split-within-wt-topology-stress-tracer-v1.json)。

## 冻结分配协议

- 每个 WT 的全部 non-singleton components 固定进入 `development`；
- 每个 WT 的 singleton 独立按 mutation count、ordering digest 和 integer-deficit schedule
  分配，不跨 WT 借用或填补配额；
- 全局结果为 `development=23,775`、`candidate-pool=580`、`validation=290`、
  `held-out=290`，exclusions 为 0；
- 每个 component 完整进入一个 role，within-WT cross-role Hamming-1 edge 必须严格为 0；
- candidate-pool、validation 和 held-out 只包含 singleton。

该设计刻意制造强 topology/distribution shift。三个 giant components 全部进入 development；
因此这不是普通随机泛化，不复现任何 publisher protocol，也不能在看到下游结果后拆分 component、
排除 bridge candidate 或更改角色容量。

## Publisher metadata 审计

`random_split`、`three_to_many`、`low_to_high` 和三个 leave-one-WT-out publisher protocols
只作为 audit comparator。Split audit 固化六套 protocol 的：

- 全局及逐 WT publisher role counts；
- within-WT cross-role Hamming-1 edge counts；
- mixed component/candidate counts；
- 15 组两两 assignment disagreement counts。

其中 `low_to_high` 也只是 audit-only，不决定 tracer membership。publisher metadata 的任何行序、
source 枚举或角色都不能进入 group/order preimage。

## 确定性字节契约与输出

production-v1 canonical JSON semantic SHA-256 为
`afb0440d803b530aeba90d98656cb68d427766a33261405b28946481fbf431d9`。namespace、seed
`20260731`、group/order SHA-256 preimages、unsigned big-endian integers、WT 内角色容量、
tie-break 和 canonical output bytes 均被冻结。collision、capacity、candidate/input、topology、
publisher audit、assignment 或 exact-byte mismatch 必须 fail closed。

`scripts/split_flip2_hydro.py` 只允许在 Git worktree 外排他写入 staging：

```text
splits/flip2/zenodo-18433203-v3/hydro/
└── flip2-hydro-within-wt-topology-stress-tracer-v1/<protocol-hash>/
    ├── membership.csv
    └── split-audit.json

manifests/flip2/zenodo-18433203-v3/hydro/splits/
└── flip2-hydro-within-wt-topology-stress-tracer-v1/<protocol-hash>/
    └── split-manifest.json
```

本地 fixture validation、真实 observable-input preflight、正式 COS publication 和 fresh
readback 是四个独立状态。当前 implementation gate 只授权前三个文件类型的本地实现和测试，
不授权真实输入读取或 COS 写入。正式 publication 必须在 implementation、CI 和人工审查后另行
授权，并采用禁止覆盖、payload first、manifest last 与 fresh exact-byte/checksum/size/row-count/
Schema/domain readback。

Split Manifest 只声明 held-out partition identity。consumer 仍必须通过共享 held-out guard 验证
Experiment Config 的精确授权；其他 split 的授权不可复用。
