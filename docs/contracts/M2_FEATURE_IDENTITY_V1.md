# M2 canonical feature identity v1

> 状态：**Issue exact body approved / bounded TDD implementation**
>
> GitHub Issue：[#47](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/47)
>
> Exact-body approval：
> [comment 5164331018](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/47#issuecomment-5164331018)

## 1. 选择与边界

`m2-alpha-ired-static-v1` 唯一接受的 feature root 是完整 canonical bundle descriptor 的
exact-byte SHA-256：

```text
RunSpec.feature_sha256 =
    SHA-256(exact UTF-8 bytes of m2-feature-bundle-descriptor-v1)
```

直接 hash 单份 matrix 无法覆盖另两个 role、config、fitted state、manifest chain 与 generator；
custom Merkle tree 没有当前 proof consumer，增加了不必要的 tree semantics；archive/container hash
会把生产器和容器 metadata 的不稳定性带入 root。因此三者均不属于 v1。

本契约只实现公开、合成、小型 fixture 的本地 engineering validation。它不生成真实 Alpha/IRED
features，不读取 hidden truth、held-out 或 external-validation，不执行正式 M2 run，不写
COS/server，也不产生 benchmark evidence 或科学结论。逻辑 task binding、role membership、
observable-only derivation 和 development-only fitted-state ownership仍由
[`M2_FEATURE_ARTIFACT_V1.md`](M2_FEATURE_ARTIFACT_V1.md) 及其 validator 负责。

## 2. 前瞻性版本分派

新语义只在下列 tuple 全部精确匹配时生效：

| Contract | Exact version |
|---|---|
| RunSpec schema | `1` |
| Protocol | `m2-alpha-ired-static-v1` |
| Descriptor | `m2-feature-bundle-descriptor-v1` |
| Descriptor format | `pcdb-canonical-json-v1` |
| Matrix format | `m2-feature-matrix-csv-v1` |
| Fitted state | `m2-feature-fitted-state-json-v1` |
| Feature config | `m2-feature-config-v1` |
| Feature registry | `m2-feature-registry-v1` |
| Logical artifact | `m2-feature-artifact-v1`, status `production` |

其他 RunSpec v1 protocol 保留旧行为：`feature_sha256` 仅是 lower-case 64-hex，不要求或猜测
descriptor。`RunSpec` 字段、canonical JSON 和 `run_id` 算法均未改变。

## 3. Exact member formats

Descriptor、config 与 fitted-state JSON 使用递归 sorted-key、2-space indent、UTF-8 no BOM、LF 和
exactly one terminal LF。Parser 拒绝 duplicate/unknown keys、non-canonical bytes、JSON float、
negative structural integer、non-NFC string、invalid UTF-8 与 non-finite extension；persisted bytes
不会先重写再接受。

Config closed Schema 为
[`m2-feature-config-v1.schema.json`](../../schemas/m2-feature-config-v1.schema.json)。它要求：

- derivations按 `feature_id` 排序并 exact-cover registry；
- parameters 按 `name` 排序；
- 每个 typed value 显式声明 `bool`、`int32`、`int64`、`float32`、`float64` 或 `utf8`、
  row-major `shape` 与 flat `values`；
- integer 使用有范围的 canonical decimal string；float 使用有限 IEEE-754 big-endian lower-hex；
  string 必须 NFC。

Fitted state closed Schema 为
[`m2-feature-fitted-state-json-v1.schema.json`](../../schemas/m2-feature-fitted-state-json-v1.schema.json)。
每个 state 只允许 development fit，并 exact-bind三份 manifests。其 non-empty tensors 必须按
name exact-cover descriptor root 中同一 state identity 的 non-empty `tensor_definitions`，包括
dtype 与 shape。

Matrix 使用无 quoting/escape/whitespace 的 `m2-feature-matrix-csv-v1`。Header 是
`candidate_id` 加 ASCII-sorted feature IDs；rows 按 lower-case 64-hex candidate ID 排序。
Integer cell 使用 canonical decimal；float cell 使用有限 IEEE-754 big-endian lower-hex。
三份 descriptor entries 固定按 `development`、`validation`、`candidate-pool` 排列。

## 4. Complete descriptor 与消费顺序

Closed descriptor Schema 为
[`m2-feature-bundle-descriptor-v1.schema.json`](../../schemas/m2-feature-bundle-descriptor-v1.schema.json)。
它完整绑定 task/manifest chain、generator commit/environment、config、完整 feature registry、
三份 role matrix、所有 fitted states/tensor inventories，以及每个 member 的 immutable object key、
byte size 和 SHA-256；descriptor 不包含 self-hash。

消费者按以下顺序 fail closed：

1. strict-parse descriptor并检查 exact version tuple；
2. fresh-read且 exact-cover全部引用 member，先检查 key/size/SHA；
3. 执行 #44 logical/domain、matrix dtype/value、Split membership exact-cover 与跨 role isolation；
4. 检查 config 三方 ID/Schema/size/SHA binding 和 state tensor exact-cover；
5. 从已验证的 bytes 独立重建完整 descriptor，要求 exact-byte equality；
6. 计算 descriptor SHA-256，并与 prospective RunSpec 精确匹配。

任何缺失、额外、重复、顺序/lexeme/version/hash/size 漂移都会拒绝整个 bundle；不允许 partial role、
hidden sidecar、member substitution、“latest” lookup、自动修复或 warning-only mode。

## 5. Golden root

批准的 serialization-only golden vector 位于 `tests/fixtures/contracts/`。其 descriptor 是
4,944 bytes，最终 root 为：

```text
99eb2b96c9c83d5f4631a48b81ba52f876f1de12ee381f623fd449b2311917cb
```

该 tiny synthetic descriptor 只证明 byte conformance，不满足生产 Alpha/IRED task constants。
测试套件另行在内存中构造一个完整 public synthetic Alpha-bound bundle，使 #44 与 #47 validators
联合通过，而不生成或记录真实 feature、target 或科学证据。
