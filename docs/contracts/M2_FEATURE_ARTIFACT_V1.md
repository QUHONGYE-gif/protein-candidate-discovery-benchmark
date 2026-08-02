# M2 Feature Artifact v1 contract

> 状态：**Issue exact body approved / TDD implementation evidence pending**
>
> GitHub Issue：[#44](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/44)
>
> Exact-body approval：
> [comment 5157887998](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/44#issuecomment-5157887998)

## 1. 范围与证据边界

本文冻结 `m2-feature-artifact-v1` 的逻辑结构、字段职责、task binding、model-visible role、
observable-input boundary 与 learned-state ownership。适用范围仅为 Gate 1 已通过的首批
Alpha Amylase＋IRED cohort，唯一设计基线为
`main@d390f73d12090395eac8f27f8692b5b7abea248e`。

规范中的“必须”“禁止”和“仅”是 normative requirement。对应的 closed Draft 2020-12 Schema 为
[`m2-feature-artifact-v1.schema.json`](../../schemas/m2-feature-artifact-v1.schema.json)。
Schema 负责结构、局部类型、允许枚举和 task-specific constants；需要比较动态 ID 集、artifact
内容或多个字段的规则由 strict domain validator fail closed 执行。

本契约不实现 feature builder，不生成真实 Alpha/IRED feature，不读取真实 target、high-value
membership/tie、held-out、WT-holdout 或 external-validation，不选择或训练 surrogate model，也
不授权 COS/server publication、正式 M2 execution、benchmark evidence 或科学结论。

## 2. 与 canonical feature hash 的职责分离

本契约只冻结 logical bundle envelope、三份 role-scoped matrix members、零个或多个显式 fitted-state
members，以及它们之间的引用关系。Issue #47 必须在不改变这些科学与数据访问职责的前提下独立冻结：

- 每个 matrix/state member 的 canonical byte encoding；
- 表格或 state 的具体序列化格式、row/feature ordering 与 numeric lexical rendering；
- member checksum composition、bundle/root preimage 和 root algorithm；
- `RunSpec.feature_sha256` 的最终精确定义；
- canonical golden vectors 与 independent reconstruction rules。

因此本文和 Schema 不选择 CSV、JSON、binary table 或其他持久化编码，不定义 canonical numeric
文本，不定义 array order 为 hash order，也不把任一 member SHA-256 冒充最终
`RunSpec.feature_sha256`。若实现需要 binary table、新生产依赖、RunSpec/RunAttempt/metric-result
shape 变化，必须停止并返回人工复审。

## 3. 精确 task bindings

`dataset_task_id` 与 `benchmark_task_id` 是两个独立必填字段。`amylase` 与
`alpha-amylase` 只能通过下表显式映射，消费者不得把它们当作隐式 alias：

| Dataset task | Benchmark task | Dataset Manifest | Processing Manifest | Split Manifest |
|---|---|---|---|---|
| `amylase` | `alpha-amylase` | `c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac` | `cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec` | `c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a` |
| `ired` | `ired` | `37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643` | `929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba` | `9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e` |

任何 ID 或 checksum 漂移都必须失败，不能 fallback 到另一个 task、旧 manifest 或相近名称。

## 4. Envelope ownership

顶层对象必须且只能包含：

- `schema_version = "m2-feature-artifact-v1"` 与 `contract_status = "production"`；
- 第 3 节的两个 task IDs 和三个 manifest SHA-256；
- `feature_config`：稳定 `feature_config_id` 与 exact config bytes 的 SHA-256；
- `generator`：完整 generator Git commit 与 environment/container digest；
- `observable_sources = ["sequence"]`；
- `feature_definitions`：全部 model-visible feature 的 machine-checkable registry；
- `matrices`：恰好三份 role-scoped matrix member；
- `fitted_states`：零个或多个显式 learned-state member。

`feature_config` 拥有 derivation selection 与参数；它不拥有真实标签、high-value 定义或 held-out
授权。`generator` 记录实际生成 feature members 的代码与环境，不改变上游 manifest identity。
Artifact reference 只记录 immutable object key、exact member SHA-256 与 byte size；其 canonical
bytes 和 root composition 仍由 #47 拥有。

## 5. 三个 model-visible roles

v1 bundle 必须包含且只包含以下三个 matrix members：

| Task | `development` | `validation` | `candidate-pool` |
|---|---:|---:|---:|
| Alpha Amylase | 722 | 361 | 2,262 |
| IRED | 4,448 | 1,034 | 2,070 |

每个 member 必须声明 `candidate_id_field = "candidate_id"`、task-specific `row_count`、positive
`column_count`、其 `feature_ids`、artifact reference 与所使用的 `fit_state_refs`。

Domain validator 必须在接受 bundle 前从 exact Split Manifest membership 独立建立三个 expected ID
sets，并验证：

1. 每个 member 内 candidate ID 非空且唯一；
2. 每个 member 的 candidate IDs 与同名 Split role 独立 exact-cover；
3. missing、unknown、duplicate 或错误 role candidate 均失败；
4. 三个 member 的 candidate-ID sets 两两不相交；
5. `row_count` 等于实际行数和 expected Split role count；
6. 每个 member 的 `feature_ids` 唯一，exact-cover `feature_definitions`，且
   `column_count` 与全部 scalar feature definitions 一致；
7. 三个 matrix members 声明同一 feature column order；`feature_definitions` registry 和
   `matrices` bundle enumeration 本身不定义 canonical order，后者由 #47 冻结。

`held-out`、`WT-holdout`、`external-validation` 及其他 role 不是 v1 member。Schema 必须拒绝其
role 值；domain validator 必须拒绝 artifact 中出现的相应 ID，而不是删除、跳过或降级为 warning。
Split membership 的 `role` 仅用于 exact-cover 验证，不得成为 matrix feature column。

## 6. Observable derivation registry

两个 accepted Processing Manifests 都只把 `sequence` 暴露给 feature derivation，并把 `target`
保存在独立 hidden-truth artifact。v1 因此固定 `observable_sources = ["sequence"]`，每条
feature definition 必须包含：

- stable `feature_id`；
- numeric `dtype`：`float32`、`float64`、`int32` 或 `int64`；
- scalar `shape = []`；
- `allowed_values = {finite: true, missing: "forbid", boolean: "forbid"}`；
- stable `derivation_id`、`derivation_version`、`source_fields = ["sequence"]`；
- nullable `fit_state` reference。

所有 matrix cells 必须是其声明 dtype 的 finite numeric values；missing、NaN、positive/negative
infinity、boolean-as-number 与 dtype mismatch 都必须失败。本文只冻结 logical value policy；具体
byte encoding 和 numeric lexical form由 #47 冻结。

Feature derivation、feature config、fitted state 与 matrix 禁止消费、携带或派生自：

- candidate-pool target、development/validation target 或任一 hidden label；
- held-out、WT-holdout 或 external-validation truth；
- high-value membership/flag、boundary score、boundary tie count 或同义派生量；
- publisher truth-derived role 或由 truth 选择的 reference/statistic；
- unrevealed observation、model outcome、metric result 或 scientific verdict。

Domain validator 必须检查 registry、matrix schema/header 与 state metadata 中的 reserved
truth-derived names，并验证 generator 的输入接口只能接收 accepted observable candidates、
exact Processing Manifest、exact Split Manifest/membership、feature config 与显式 fitted-state
members。字段名扫描不能单独证明 target-free；没有 hidden-truth input path 和完整 checksum-linked
input chain 同样是必需条件。

## 7. Learned state 与 fit boundary

无状态 derivation 必须写 `fit_state = null`。若任何 preprocessing、imputation、scaling、
vocabulary、reference statistic、feature selection 或 calibration 需要 learned state，则：

1. state 必须作为 `fitted_states` 中的独立 versioned member 持久化；
2. `fit_role` 与 `input_identity.role` 必须都是 `development`；
3. `input_identity` 必须 exact-bind 本 envelope 的 Dataset/Processing/Split Manifest SHA-256；
4. state 必须具有稳定 `fit_state_id`、`state_version`、`derivation_id` 和 immutable artifact
   object key/SHA-256/size；
5. 对应 feature derivation 的 non-null `fit_state` 与使用它的 matrix `fit_state_refs` 必须
   exact-resolve 同一个 `fit_state_id` 和 SHA-256；
6. 每个声明的 state 必须被至少一个 derivation 使用；missing、unknown、duplicate、conflicting
   或未使用的 state/ref 均失败；
7. state 不得只隐藏在 model pickle、进程内存、generator cache 或未清单化 sidecar 中。

只有 `development` observable inputs 可以影响 learned state。`validation` 与 `candidate-pool`
只能用已经冻结的 state 做 transform，不得影响 mean、scale、imputation、vocabulary、reference
statistics、feature selection 或 calibration fitting。若需要 validation-assisted fitting，必须停止
并先通过 superseding ADR 改变项目 core invariant；本契约不得静默放宽。

Domain validator 还必须验证所有 fitted-state input identities 与 envelope exact-match、所有 matrix
引用的 state 集与其 feature derivations 所需 state 集一致。`fitted_states = []` 仅在全部 derivation
均为 `fit_state = null` 且全部 matrix `fit_state_refs = []` 时合法。

## 8. Validation order

消费者必须按以下顺序 fail closed：

1. 以 strict JSON parser 拒绝 duplicate keys、invalid UTF-8、non-finite constants 与未知
   `schema_version`；
2. 用 closed Draft 2020-12 Schema 验证结构、task constants、三个 allowed roles 与 row counts；
3. 验证 feature config、generator 和所有 referenced artifact 的 exact identity；
4. 从 exact Split membership 验证逐 role unique/exact-cover 与跨 role non-overlap；
5. 验证 feature registry、matrix columns/dtypes/values 与 observable-only derivation boundary；
6. 验证 fitted-state ownership、development-only fit 和全部 state references；
7. 最后由 #47 contract 验证 canonical member bytes、member/root composition 与
   `RunSpec.feature_sha256`。

任一步缺少输入或不匹配都必须拒绝整个 bundle；不得接受部分 role、自动补行、重写 non-canonical
artifact、忽略未知字段或从另一个 bundle 猜测 state。

## 9. 生命周期与 supersession

Accepted `m2-feature-artifact-v1` envelope、matrix/state members 和 feature config 都是 immutable
artifacts，不得原地覆盖。重试或重新生成必须产生新的 immutable objects，并保留原证据。增加 role、
允许 vector shape、改变 dtype/value policy、放宽 observable source 或 fit scope、改变字段职责或
兼容规则，都需要新 Schema/contract version；若改变 accepted ADR 或 core invariant，还必须新增
superseding ADR。

后续 #47 可以冻结 v1 的 canonical encoding 和 root identity，但不得借 canonicalization 改变本契约
的 task bindings、required roles、row counts、target-free boundary、development-only fit 或显式
fitted-state ownership。
