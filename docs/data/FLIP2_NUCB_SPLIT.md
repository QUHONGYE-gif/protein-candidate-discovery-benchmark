# FLIP2 v3 NucB Split

## 范围与证据边界

本流程对应 GitHub Issue #25，在已接受的 NucB Processing Manifest 上建立
`flip2-nucb-topology-extrapolation-v1`。它只使用公开、observable 的 `candidate_id`、sequence
和相对冻结 reference 的 mutation count，负责 Hamming-1 grouping、确定性 membership、
publisher audit 与 Split Manifest。activity class 和 `target` 始终属于 hidden truth；本流程不读取
hidden truth，不授权 held-out，不运行 feature、model 或 replay，也不构成 benchmark result、
server-execution evidence 或 scientific gate。

冻结输入为：

- Processing Manifest SHA-256：
  `76b637d4dced5aa5b8dfd4552a904a462621a18095ac772260daf1ac692295fd`；
- observable `candidates.csv`：55,759 rows，SHA-256
  `a13fbe0309106567adaea537f29e74fba3daa2e96b230c0c87408b7936930467`；
- audit-only `upstream-split-metadata.csv`：111,518 rows，SHA-256
  `39e4b8d50b265e7d9d49e290c668c5b64dda7b8fd74a2f1737807db20b166561`。

CLI 没有 hidden-truth 参数。Processing Manifest、field visibility、checksum、size、row count、
candidate universe、reference、topology 或 config identity 发生漂移时必须 fail closed。

## Observable-only 审计与方案比较

full-length Hamming distance = 1 的图包含 76,890 条 undirected edges、26,333 个 connected
components。22,983 个 components 为 singleton；其余 3,350 个 components 共覆盖 32,776 个
candidates。最大 component 含 24,844 candidates，第二大含 39 candidates。

实施前比较了三类方案：

1. **Whole-component topology allocation** 可以实现零 Hamming-1 crossing，但 24,844-candidate
   giant component 使常规平衡四分法不可行。本协议因此将所有 graph-connected candidates 固定为
   development。
2. **Mutation-distance band allocation** 使用 observable mutation count，但多个实用 cutoff 会切断
   Hamming-1 edges；远端零 crossing band 又过小。mutation count 因此只用于 graph-isolated
   singleton 的 prospective quota stratification。
3. **Publisher replay** 最接近原始论文切分，但 `random` 与 `two_to_many` 分别跨越 26,242 和
   28,197 条 Hamming-1 edges，且有 48,758 个 candidate assignments 不一致。它们只保留为单独
   命名的 comparator，不决定 primary membership。

compact audit 持久记录每个 publisher source 的 role counts、mixed component/candidate counts、
cross-role edges 和跨 source disagreement。

## 冻结的 topology-extrapolation 协议

- 全部 3,350 个 non-singleton components 固定为 `development`，共 32,776 candidates；
- 22,983 个 singleton components 按 mutation count 与全局 integer-deficit schedule 分配为
  `candidate-pool=11,493`、`validation=5,745`、`held-out=5,745`；
- exclusions 为 0；每个 accepted candidate 恰好出现一次；
- 每个 component 必须完整进入一个 role，cross-role Hamming-1 edge 必须严格为 0；
- candidate-pool、validation 和 held-out 均只包含 singleton。

该设计刻意制造强 topology/distribution shift：development 占 58.78%，且包含巨型 connected
component；candidate-pool 占 20.61%，三个非 development roles 都是 graph-isolated candidates。
它不是普通随机泛化，也不复现 publisher protocol，不能在看到下游结果后拆分 component、排除
bridge candidate 或更改角色容量。

完整 component-size multiset、21 个 production mutation strata 及对应角色配额保存在
[`nucb-split-topology-extrapolation-v1.json`](../../configs/datasets/flip2/zenodo-18433203-v3/nucb-split-topology-extrapolation-v1.json)。

## 确定性字节契约

config 冻结：

- production-v1 canonical JSON semantic SHA-256
  `068a40609bcc73e1e5f92b645f6f822298ce80e30c93eb40ef9310a8c42dcbd2`；
- namespace `pcdb:split:flip2-nucb-topology-extrapolation-v1:v1` 的 exact ASCII bytes；
- public reference SHA-256
  `5ce46bb493dbedbebac11e8f358ab1405919032bade046c66360b8b6c7ca89a5`；
- seed `20260727`、group/order SHA-256 preimages、unsigned big-endian integers、role order 与
  tie-break；
- Issue #25 amendment 的 N1–N8 golden vectors、7-token miniature schedule、real-candidate
  regression vector 和 production stratum quotas；
- collision、capacity、candidate/input、topology、component-size、assignment 或 exact-byte
  mismatch 必须 fail closed，不允许 repair、spillover、fallback、shuffle 或静默 exclusion。

全局 schedule 长度为 22,983。第 `i` 个位置只在未满 role 中最大化
`i * target - N * assigned`，平局按 `candidate-pool`、`validation`、`held-out` 顺序解决。
singleton 再按 mutation count、ordering digest bytes、group digest bytes 依次消费 schedule。

candidate row、upstream row 或 upstream source enumeration 顺序不得改变 membership 与 audit
语义；相同的 frozen input bytes、config bytes 和完整 Git commit 必须生成相同 canonical artifact
bytes、checksums 与 object keys。

## 输出与运行门禁

`scripts/split_flip2_nucb.py` 只允许在 Git worktree 外排他写入 staging：

```text
splits/flip2/zenodo-18433203-v3/nucb/
└── flip2-nucb-topology-extrapolation-v1/<protocol-hash>/
    ├── membership.csv
    └── split-audit.json

manifests/flip2/zenodo-18433203-v3/nucb/splits/
└── flip2-nucb-topology-extrapolation-v1/<protocol-hash>/
    └── split-manifest.json
```

本地 fixture validation、真实 observable-input preflight、正式 COS publication 和 fresh
readback 是四个独立状态。当前 implementation gate 不授权真实输入读取或 COS 写入。正式
publication 必须在 implementation、CI 和人工审查后另行授权，并采用禁止覆盖、payload first、
manifest last 与 fresh exact-byte/checksum/size/row-count/Schema/domain readback。

Split Manifest 只声明 held-out partition identity。consumer 必须同时验证 Experiment Config 的
`held_out_authorized=true`、被访问 Split Manifest exact bytes 的 `split_sha256` 和非空人工授权
reference；缺失或不匹配时 fail closed，其他 split 的授权不可复用。
