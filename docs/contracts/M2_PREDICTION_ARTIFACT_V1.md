# M2 static prediction artifact v1

> 状态：**Issue exact body approved / bounded TDD implementation**
>
> GitHub Issue：[#46](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/46)
>
> Exact-body approval：
> [comment 5169120347](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/46#issuecomment-5169120347)

## 1. 范围与声明边界

本文冻结 `m2-prediction-artifact-v1`、唯一 M2-static RunSpec v1 profile，以及 prediction、
RunAttempt 和四份 metric results 的完整证据闭包。它只适用于 Gate 1 已通过的 Alpha Amylase
与 IRED 首批 cohort，依赖已经冻结的
[`m2-feature-artifact-v1`](M2_FEATURE_ARTIFACT_V1.md) 和
[`m2-feature-bundle-descriptor-v1`](M2_FEATURE_IDENTITY_V1.md)。

本契约只授权 CPU-only、公开合成 fixture 的本地 engineering validation。它不构建真实 feature，
不训练模型，不读取真实 target、high-value membership、tie membership 或 held-out，不执行
COS/server 操作、正式 M2、benchmark release 或科学比较，也不引入生产依赖。

## 2. 唯一 M2-static profile

新语义仅在下列版本和 profile 全部精确匹配时生效：

```text
RunSpec.schema_version       = "1"
RunSpec.protocol_version     = "m2-alpha-ired-static-v1"
Experiment Config version    = "2"
Experiment Config status     = "production"
acquisition_id               = "m2-static-full-pool-v1"
initial_sample_size          = 1
batch_size                   = 1
budget                       = 1
```

三个正整数是该 exact protocol 的 not-applicable compatibility sentinel：不选择 candidate、不揭示
label、不运行 replay round，也不消耗 acquisition budget。它们不得作为科学 sample、batch 或
budget 报告。其他 protocol 的既有语义和 RunSpec v1 shape 不变。

参数为空的 acquisition config 使用 `pcdb-canonical-json-v1`：

```json
{
  "acquisition_id": "m2-static-full-pool-v1",
  "prediction_scope": "candidate-pool-exact-cover",
  "schema_version": "m2-static-acquisition-config-v1",
  "selection_policy": "none"
}
```

它包含一个 terminal LF，exact size 为 186 bytes，SHA-256 为
`d50c876c6d114eb74a878bbce43a2f760e21507dad80c0d0f87ce02f2ff7c9d5`。对应 Experiment
Config 必须且只能注册该 acquisition，并使用 `initial_sample_sizes = [1]`、
`batch_sizes = [1]`、`budgets = [1]`；其 model、seed、task 和三份 manifest identity 必须
与 RunSpec 精确一致，且 `held_out_authorized = false`。

## 3. Canonical prediction bytes

Closed Draft 2020-12 Schema 为
[`m2-prediction-artifact-v1.schema.json`](../../schemas/m2-prediction-artifact-v1.schema.json)。
Artifact 使用 sorted-key、2-space indent、UTF-8 no BOM、LF 和 exactly one terminal LF。
Parser 拒绝 invalid UTF-8、duplicate/unknown keys、JSON float、non-canonical bytes 和改写后接受。

顶层精确包含 version/protocol、Experiment Config、三份 manifests、feature root、run/attempt、
task/role/count、model/config、static acquisition/config、seed 和 `predictions`。Alpha count 固定
为 2,262，IRED 固定为 2,070；role 只能是 `candidate-pool`。

每行只含：

```text
candidate_id = lower-case 64-hex
prediction   = finite big-endian IEEE-754 float64 的 16 个 lower-case hex
```

行按 candidate ID ASCII 严格升序且不重复。Exponent-all-ones、JSON number、boolean、
decimal/exponent text、空值和额外字段均失败；正负零保留不同 bit pattern。Prediction envelope
不得包含 truth、observed label、high-value/tie membership、boundary summary、uncertainty、
calibration、rank、selection 或 evaluator intermediate。

## 4. 身份、feature root 与 candidate exact-cover

消费者必须从 exact RunSpec bytes 重建 `run_id`，从 exact Experiment Config bytes 计算 config
hash，并 fresh-read #47 全部 members、独立重建 descriptor exact bytes。只有 descriptor SHA-256
可作为 `RunSpec.feature_sha256`；任意合法 64-hex 不能替代该证明。

Prediction 必须 exact-match：

```text
experiment_config_sha256
dataset_manifest_sha256
processing_manifest_sha256
split_sha256
feature_sha256
run_id
attempt_id
task_id
candidate_role
candidate_count
model_id
model_config_sha256
acquisition_id
acquisition_config_sha256
seed
```

Prediction candidate IDs 必须 unique/exact-cover 已验证 Split 的 `candidate-pool` membership。
Missing、unknown、duplicate、forbidden-role、reordered、partial-pool 或 count drift 均失败。

## 5. Capability-separated lifecycle

执行顺序必须 fail closed：

1. 分别严格验证 exact RunSpec、完整 RunAttempt、Experiment Config 和 #44/#47 feature inputs，
   再进行 cross-artifact checks。
2. Target-free producer 只收到验证后的 public feature members、model config、seed 和非 truth
   identities；接口不提供 truth provider、evaluator 或 truth-bearing shared state。
3. Producer 返回完整 canonical prediction bytes。Orchestrator 验证、计算 size/SHA-256 并 seal；
   RunAttempt output inventory 必须有且只有一个匹配 prediction object key/size/hash 的 reference。
4. Evaluator 接收 fresh copied/read-only prediction bytes，重新计算 hash、验证 canonical
   exact-cover 后，才可通过 evaluator/hidden-oracle 私有边界访问 truth。
5. Evaluator 只返回既有 aggregate metric-result bytes。Candidate-level truth、high-value/tie
   membership 不持久化，也不进入 prediction、model state、cache、log 或 exception。
6. 所有 artifact/result exact bytes、output references 和完整 result set 验证通过后，才允许创建
   既有 empty `_SUCCESS` marker；失败或 partial inventory 不得成功。

Producer/evaluator callback 的异常跨边界时只转换成稳定 generic error。若同一 coordinated attempt
不能维持该 capability separation，必须停止并评审新的 versioned linkage。

## 6. 四结果完整证据集

每个 run/attempt/prediction 必须提供且只能提供以下 projected identities：

```text
(spearman_rank_correlation, null)
(high_value_recall_at_nominal_k, upper-tail-1pct)
(high_value_recall_at_nominal_k, upper-tail-0.5pct)
(high_value_recall_at_nominal_k, upper-tail-2pct)
```

四份结果均先通过既有 `high-value-metric-result-v1` strict/domain validation，再 exact-bind 同一
prediction SHA-256、run、attempt、task、model、static acquisition 和 seed。它们都必须满足：

```text
evaluated_candidate_count
  = prediction candidate_count
  = frozen candidate-pool count
```

Recall 的 `frozen_candidate_count` 与 `nominal_k` 还必须匹配 Experiment Config 中对应 definition。
RunAttempt outputs 中每份 supplied result 都需要唯一的 exact object key/size/hash reference。
完整 supplied set 上出现 missing、extra、duplicate 或 conflicting identity 时整体失败。
RunAttempt 可列出其他必需 metric artifacts，但不能让 supplied 四结果或 prediction reference
变得不唯一。

## 7. 实现入口与停止条件

`pcdb.contracts.prediction` 提供：

- strict `load_run_spec_v1`、`load_run_attempt_v1` 与 `load_prediction_artifact`；
- `validate_m2_static_profile`；
- `orchestrate_m2_static_prediction`，用于测试 target-free producer、fresh-byte evaluator 和
  aggregate-only return boundary；
- `validate_m2_static_prediction_evidence`，从 exact byte streams 验证完整证据闭包。

新增依赖、真实输入或 prediction、COS/server、held-out、正式 M2，或任何 RunSpec/RunAttempt/
metric-result/Experiment Config/#44/#47 persistent shape 变化，均超出本文范围并必须停止复审。
