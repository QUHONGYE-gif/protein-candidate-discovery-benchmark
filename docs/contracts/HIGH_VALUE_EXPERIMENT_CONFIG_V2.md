# Alpha＋IRED High-value Experiment Config v2 Phase A 冻结候选

> 状态：**Proposed / awaiting human review**
> GitHub Issue：#36
> 基线：`main@c8fbb2e95ec3f1034e1adad8ad453ab012840914`
> 本文只冻结 production contract candidate，不创建 production Schema、loader、evaluator 或
> 正式 task config。只有人工评论对本文所在的 exact commit 明确给出
> `APPROVED FOR TDD IMPLEMENTATION`，才能进入 Phase B。

证据链：

- parent design：[Issue #28](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/28)；
- non-production prototype：
  [PR #30](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/30)，
  merge commit `bcccb2c3bbc2e7df3f71c1eb95c95e7e1303dbd7`；
- project-owner Decision Guard：
  [comment 5103169292](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/28#issuecomment-5103169292)；
- independent collaborator approval：
  [comment 5103432919](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/28#issuecomment-5103432919)；
- production-contract gate：
  [Issue #36](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/36)。

## 1. 冻结范围与不变量

本候选承接 Issue #28 已双人批准的操作性规则：

- primary：冻结 `candidate-pool` universe 上的 `quantile=0.01`；
- sensitivity：`quantile=0.005` 与 `quantile=0.02`，不得替代 primary；
- label direction：`higher_is_better`；
- nominal K：唯一计算为 `ceil(q × frozen_candidate_count)`；
- target boundary ties：`include_all_equal`；
- missing/non-finite target 或 duplicate candidate ID：fail closed；
- primary aggregation：task-first；seed 只表示 task 内算法随机性；
- high-value membership 只属于 evaluator/hidden oracle。
- 若 primary 的 `actual_high_value_count / frozen_candidate_count > 0.02`，或任一预注册
  sensitivity 使同一比较的结论方向相对 primary 严格反转，则二元 high-value 证据必须标记为
  `fragile-inconclusive`；primary 仍保持 1%，不得事后改阈值。

精确 task bindings：

| Task | Dataset Manifest SHA-256 | Processing Manifest SHA-256 | Split Manifest SHA-256 | Role / count | Nominal K（0.5% / 1% / 2%） |
|---|---|---|---|---|---|
| Alpha Amylase | `c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac` | `cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec` | `c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a` | `candidate-pool` / 2,262 | 12 / 23 / 46 |
| IRED | `37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643` | `929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba` | `9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e` | `candidate-pool` / 2,070 | 11 / 21 / 42 |

任一 manifest、role、count、definition 或 task identity 漂移必须在读取 truth 或计算 metric
之前失败。

## 2. Experiment Config identity 与 canonical bytes

### 2.1 选择

`RunSpec.experiment_config_sha256` 定义为 **production Experiment Config exact canonical file
bytes 的 SHA-256**。不接受任意格式文件的 hash，也不使用脱离文件的 semantic hash。

理由：

- 任意 exact bytes 会让纯格式变化产生不同科学身份；
- parsed semantic hash 依赖未绑定的 parser/number normalization，不能直接证明实际审查文件；
- exact canonical file bytes 同时提供唯一语义表示、Git 可审查字节和直接 checksum binding。

### 2.2 Reference parser

production loader 必须：

1. 读取 exact bytes，并拒绝 UTF-8 BOM、非法 UTF-8 和空文件；
2. JSON 解析时拒绝 duplicate object keys；
3. 拒绝 `NaN`、`Infinity`、`-Infinity` 和其他非 JSON 常量；
4. 要求顶层值为 object；
5. 根据 `schema_version` 选择唯一 Schema/validator；
6. 用 reference serializer 重新生成 bytes，并要求与输入 exact-byte equal；
7. 最后执行 JSON Schema 与 fail-closed domain validation。

不得先 canonicalize 任意输入再把它静默当作已审查 production config。

### 2.3 Reference serializer

规范性 Python reference procedure：

```python
(
    json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n"
).encode("utf-8")
```

冻结规则：

- UTF-8、无 BOM；
- object keys 按 Python `sort_keys=True` 的 Unicode code-point order；
- 缩进 2 spaces；
- separators 使用该 `indent=2` 调用的 Python 标准输出；
- JSON booleans/null 使用小写 token；
- 只允许有限 JSON numbers；domain validator 进一步限制 definition fraction 为冻结值；
- exactly one terminal LF，之后无空格或额外 newline；
- production identity 是上述 exact bytes 的 SHA-256。

跨语言实现可以验证已跟踪 bytes，但在宣称兼容前必须逐字节复现 reference vectors。若未来需要
改变 serializer、number lexical form 或 terminal newline，必须新增 schema/config identity
version，不得原地修改 v2。

### 2.4 V2 normative field shape

Phase B 的 Schema 与 domain validator 必须实现以下字段名和职责，不得在编码时另选结构：

- 顶层必需字段：`schema_version`、`contract_status`、`experiment_id`、`phase`、`task_id`、
  `dataset_manifest_sha256`、`processing_manifest_sha256`、`split_sha256`、`models`、
  `acquisitions`、`seeds`、`initial_sample_sizes`、`batch_sizes`、`budgets`、`evaluation`、
  `held_out_authorized`；
- `schema_version` 只能为 `"2"`，`contract_status` 只能为 `"production"`；
- `experiment_id`、`phase`、method/runtime arrays 与 held-out conditional 保持 v1 的 field
  meaning 和基本约束；本 Issue 不冻结其正式 Alpha/IRED values；
- `task_id` 只能为 `alpha-amylase` 或 `ired`，并与第 1 节对应的三个 manifest SHA-256、
  `candidate-pool` 和 frozen count 形成不可拆分 binding；
- `evaluation.primary_high_value_definition_id` 必须为 `upper-tail-1pct`；
- `evaluation.high_value_definitions` 必须按
  `upper-tail-1pct`、`upper-tail-0.5pct`、`upper-tail-2pct` 的 canonical order 各出现一次；
- 每个 definition 必须包含 `definition_id`、`purpose`、`kind="quantile"`、`value`、
  `candidate_role="candidate-pool"`、`frozen_candidate_count`、`ranking_policy`、
  `reporting_policy` 和 `derivation`；
- primary/sensitivity、fraction、nominal K、ranking/reporting policy 必须与第 1、3 节 exact
  match；
- `evaluation.metric_sets.m2_static` exact 为
  `["spearman_rank_correlation", "high_value_recall_at_nominal_k"]`；
- `evaluation.metric_sets.m3_m5_sequential` exact 为
  `["best_found_value", "simple_regret", "high_value_discovery_recall", "success_at_budget"]`；
- `evaluation.metric_sets.task_seed_aggregates` exact 为 `["success_probability"]`；
- `evaluation.aggregation` exact 声明 `primary_unit="task"`、
  `seed_role="within-task-algorithmic-randomness"`、`pooled_seed_primary=false`；
- `evaluation.interpretation_policy` exact 声明 primary tie-expansion fraction 上限 `0.02`、
  sensitivity fractions `[0.005, 0.02]`、comparison rule
  `strict_sign_reversal` 和 triggered status `fragile-inconclusive`；
- `evaluation.access_boundary` 必须声明 owner 为 evaluator/hidden oracle，并列出禁止的
  feature/model/calibration/acquisition consumers；
- 所有 objects `additionalProperties=false`，identity-bearing arrays 禁止重复，unknown field
  fail closed。

prototype 的 `nominal_k_probability_reporting` 只是一项 illustration，不进入 production v2。
Phase B 可以把重复 policy object 抽成 Schema `$defs`，但不得改变上述 persisted JSON shape。

## 3. Metric contract

### 3.1 公共符号

- `U`：Split Manifest 冻结的完整 `candidate-pool` candidate-ID set；
- `N = |U|`：必须等于 task binding 的 frozen count；
- `y(c)`：evaluator/hidden oracle 持有的 finite target；
- `p(c)`：M2 static evaluator 接收的 finite prediction score；
- `K = ceil(qN)`：definition 的 nominal K；
- `b`：按 `(target descending, candidate_id ascending)` 排序后的第 K 个 target score；
- `H = {c ∈ U | y(c) >= b}`：tie-inclusive high-value membership；
- `R_t`：截至 sequential round `t` 已由 oracle 揭示的 distinct candidate IDs。

`candidate_id ascending` 只提供 canonical ordering；它不得从 target boundary tie 中删除候选。
所有 evaluator 输入必须 exact-cover 所需 universe；unknown、missing 或 duplicate candidate ID
均 fail closed。

High-value definition 必须报告：

- `nominal_k = K`；
- `actual_high_value_count = |H|`；
- `boundary_score = b`；
- `boundary_tie_count = |{c ∈ U | y(c) = b}|`。

### 3.2 M2 static metrics

#### `spearman_rank_correlation`

- 输入：`U` 上 exact-cover 的 prediction/target pair；
- 对 prediction 与 target 分别使用 average ranks for ties；
- value：两组 ranks 的 Pearson correlation；
- direction：higher is better，范围 `[-1, 1]`；
- undefined：`N < 2`，或任一 rank vector variance 为 0；
- missing/non-finite/duplicate/unknown candidate：error，而不是 undefined。

这是冻结的 continuous ranking metric；prototype placeholder `ranking_quality` 不进入 production。

#### `high_value_recall_at_nominal_k`

- prediction ranking：`(prediction descending, candidate_id ascending)`；
- predicted set `P_K`：ranking 的前 exactly K 个 candidate；prediction boundary tie 可由
  `candidate_id` 确定性拆分，因为它不是 target-derived high-value membership；
- value：`|P_K ∩ H| / |H|`；
- direction：higher is better，范围 `[0, 1]`；
- `|H|` 由 `q > 0`、`N > 0` 和 tie-inclusive construction 保证至少为 1；
- missing/non-finite/duplicate/unknown candidate：error。

prototype placeholder `top_k_recall` 由该无歧义 ID 取代。

### 3.3 M3–M5 sequential metrics

#### `best_found_value`

- value at round `t`：`max(y(c) for c in R_t)`；
- direction：higher is better；
- `R_t` 为空时 undefined，reason 为 `no_revealed_observations`。

#### `simple_regret`

- value at round `t`：`max(y(c) for c in U) - best_found_value(t)`；
- direction：lower is better，理论范围 `[0, +∞)`；
- `R_t` 为空时 undefined，reason 为 `no_revealed_observations`；
- 结果小于 0 表示 contract/input drift，必须 error，不做截断。

#### `high_value_discovery_recall`

- value at round `t`：`|R_t ∩ H| / |H|`；
- direction：higher is better，范围 `[0, 1]`；
- `R_t` 为空时 value 为 `0.0`，不是 undefined。

#### `success_at_budget`

- `B` 是 Experiment Config 预注册的 distinct revealed-observation count；`R_B` 是 canonical
  trajectory 中从 initial selection 开始计数的前 B 个 distinct reveal events；
- attempt-level value：预算边界 `B` 时 `R_B ∩ H` 非空；
- type：boolean；`true` 为成功；
- 只有 trajectory 至少包含 B 个合法 distinct reveal events 时才 defined；不足 B 时无论
  RunAttempt 为 running、failed 或 succeeded，均 undefined，reason 为
  `budget_not_reached`；成功状态不能补造缺失的预算证据；
- 跨 preregistered seeds 的 `success_probability` 是同一 task/method/budget 下
  `success_at_budget`（`true=1`、`false=0`）的 arithmetic mean；任一预注册 seed undefined
  时该 probability 也 undefined，不得静默丢弃 seed；
- 不得 pool tasks，不得把 seed 当作 task。

本 Phase A 不冻结 cost-aware、labels-to-target 或 M6 multi-fidelity metrics；新增这些指标必须
通过单独 protocol Issue。

### 3.4 Undefined 与 non-finite

Undefined metric 必须持久化为结构化状态，不得写 JSON `NaN`：

```json
{
  "status": "undefined",
  "value": null,
  "undefined_reason": "no_revealed_observations"
}
```

只有本文明确列出的数学前置条件不足可以产生 undefined。输入缺失、identity drift、duplicate、
unknown candidate 或 non-finite input 都是 error，必须终止该 evaluation artifact。

### 3.5 Task-first aggregation 与 fragile/inconclusive

对任一预注册 metric/method/scope：

1. 先在每个 task 内对全部预注册 seed 的 defined numeric value 做 arithmetic mean；boolean
   使用 `true=1`、`false=0`；
2. 再对 Alpha 和 IRED 两个 task mean 做等权 arithmetic mean；
3. 任一预注册 seed 或 task undefined 时，primary aggregate 也 undefined；不得通过删除
   undefined 项改变分母；
4. 不得把全部 seed values 直接 pool 后求平均。

对两个预注册 methods `A` 和 `B` 的同一 task/metric/scope 比较，先把 difference 统一为
“正数支持 A”：higher-is-better metric 使用 `A - B`，lower-is-better metric 使用 `B - A`。
仅当 primary difference 与任一 0.5%/2% sensitivity difference 均非零且符号相反时，构成
`sensitivity_direction_reversal`。相等（difference 为 0）不是方向反转；所需 result undefined
时比较状态也为 undefined，不得宣称稳健。

二元 high-value 证据的 interpretation status：

- `fragile-inconclusive`：primary 的 `actual_high_value_count / N > 0.02`，reason 为
  `primary_tie_expansion_exceeds_2pct`；或出现上述严格方向反转，reason 为
  `sensitivity_direction_reversal`；
- `robust-to-preregistered-checks`：相关 primary/sensitivity 结果全部 defined，且两个条件均
  未触发；
- `undefined`：完成上述判定所需的任一结果 undefined。

`fragile-inconclusive` 不改变 primary definition，也不删除二元结果；报告必须将解释重心转向
本文已冻结的连续 `spearman_rank_correlation`、`best_found_value` 和 `simple_regret`。本
Issue 不读取真实 target，因此不会产生实际 interpretation status。

## 4. V1/V2 dispatch、sentinel 与 rollback

### 4.1 并行版本

- v1 保持 `schemas/experiment.schema.json`、`schema_version="1"` 和现有行为不变；
- Phase B 新增 `schemas/experiment-v2.schema.json`，要求 `schema_version="2"`；
- v2 顶层必须包含 `contract_status="production"`；
- `schema_version="2-prototype"`、任何 `proposal_status` 字段、unknown version 或
  `contract_status!="production"` 必须被 production loader 拒绝；
- loader 不以字段猜测版本，不提供 permissive fallback。

### 4.2 Migration 与 rollback

- 不自动把 v1 迁移为 v2，也不把 v2 降级为 v1；
- existing v1 config、fixtures、Schema validation 与 RunSpec behavior 必须 regression-protected；
- v2 config 使用新文件和新 SHA-256，不覆盖任何 v1 config；
- rollback 仅表示调用方显式选择一个已审查 v1 Experiment Config；不得在相同
  `experiment_config_sha256` 下改变解析语义；
- prototype 文件永久保持 non-production evidence，不改名为 production config。

## 5. Evaluator/oracle ownership 与 leakage boundary

### 5.1 Ownership

production interface 分为三类：

1. `HiddenOracle`：持有 truth，只对已选择 candidate 产生 `Observation`；
2. evaluator：在获授权的 evaluation path 内持有 truth-derived private state，计算 high-value
   membership 和 metrics；
3. prohibited consumers：candidate features、surrogate、calibration、acquisition strategy。

High-value membership `H` 不得从 evaluator API 返回，也不得进入 feature table、prediction
record、model input、calibration state 或 acquisition context。允许持久化的是 definition
summary 和 aggregate metric result。

Phase B 必须至少固定以下 synthetic-only leakage tests：

- `HiddenOracle` 仍只接受 selected candidate IDs，并只返回对应 `Observation`；
- evaluator 的 public output 无 candidate-level `H`、boundary membership 或 unrevealed target；
- prediction/trajectory 输入出现 unknown、missing 或 duplicate candidate ID 时 fail closed；
- feature、surrogate、calibration 或 acquisition 输入出现 target、high-value flag、boundary
  score 或其派生字段时 fail closed；
- production loader 对 proposal sentinel、unknown version 和 non-canonical bytes fail closed；
- held-out truth loader 在任何读取之前要求 exact split identity 与非空人工授权 reference；
- synthetic conformance test 必须证明 aggregate result 可以复现，且 prohibited consumers
  无法观察 synthetic hidden membership。

### 5.2 Evaluator inputs

evaluator 必须接收并核对：

- exact Experiment Config bytes 和 `experiment_config_sha256`；
- Dataset/Processing/Split Manifest exact identities；
- exact candidate role 与 candidate-ID universe；
- hidden truth owned inside the authorized evaluator boundary；
- M2 prediction rows，或 M3–M5 immutable trajectory/observations；
- metric ID、definition ID、round/budget scope。

Phase B conformance fixtures 只能使用 synthetic truth。真实 Alpha/IRED target、actual tie count、
held-out、COS/server execution 和正式 evaluator run 均不属于 Issue #36。

### 5.3 Canonical aggregate result shape

每个 task/definition/metric/scope 产生一条 result：

```json
{
  "schema_version": "1",
  "experiment_config_sha256": "<64-lower-hex>",
  "dataset_manifest_sha256": "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac",
  "processing_manifest_sha256": "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec",
  "split_sha256": "c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a",
  "run_id": "<64-lower-hex>",
  "attempt_id": "attempt-001",
  "input_artifact_sha256": "<64-lower-hex>",
  "task_id": "alpha-amylase",
  "method_id": "example-method",
  "seed": 0,
  "candidate_role": "candidate-pool",
  "definition_id": "upper-tail-1pct",
  "metric_id": "simple_regret",
  "evaluation_scope": "sequential",
  "round": 3,
  "budget": 32,
  "status": "defined",
  "value": 0.125,
  "undefined_reason": null,
  "support": {
    "frozen_candidate_count": 2262,
    "nominal_k": 23,
    "actual_high_value_count": 24,
    "boundary_score": 0.91,
    "boundary_tie_count": 2,
    "evaluated_candidate_count": 32
  }
}
```

闭合规则：

- `evaluation_scope` 只能是 `m2-static` 或 `sequential`；
- `m2-static` 要求 `round=null`、`budget=null`；
- `sequential` 要求 non-negative `round`、positive `budget`；
- 三个 manifest SHA-256 必须 exact match Experiment Config task binding，`run_id` 必须来自绑定
  同一 config SHA-256 的 RunSpec；
- `input_artifact_sha256` 绑定 M2 prediction artifact 或 M3–M5 trajectory artifact；
- `status="defined"` 要求 finite number 或 boolean value，且
  `undefined_reason=null`；
- `status="undefined"` 要求 `value=null` 和本文允许的 reason；
- object additional fields fail closed；
- result 只用于 immutable evaluation evidence，不返回 candidate-level `H`。

跨 definition/method 的 interpretation summary 是独立 immutable aggregate artifact，必须
绑定同一 `experiment_config_sha256` 和其全部输入 result checksums，并持久化
`interpretation_status` 与上述枚举 reason。它不得回写或覆盖单条 metric result；具体 method
IDs、预算与正式 summary file Schema 留给后续 M2 protocol Issue。

## 6. Held-out 与授权边界

- Issue #36 的 Alpha/IRED v2 conformance fixtures 必须固定 `held_out_authorized=false`；
- high-value contract 不授予 held-out、WT-holdout 或 external-validation access；
- future held-out config 仍必须绑定 exact accessed Split Manifest SHA-256，并携带非空人工授权
  reference；
- publisher role、另一个 task/split 的授权或 `contract_status="production"` 不能替代 held-out
  authorization；
- evaluator 必须在读取 held-out truth 前调用现有 exact split/authorization guard。

## 7. ADR、RunSpec 与实现损失上限

- ADR-0004 记录并行 v2 和 canonical-file-byte identity，状态在人工批准前为 Proposed；
- ADR-0003 不被 supersede：Experiment Config 继续拥有 threshold、metrics、method 和
  held-out authorization；
- RunSpec Schema/dataclass 不增加 high-value、metric 或 evaluator 子字段，只绑定完整
  `experiment_config_sha256`；
- Phase B 仅本地/CI CPU，0 新 production dependencies、0 COS、0 网络数据、0 真实标签、
  0 held-out、0 model/replay，临时空间上限 10 MiB；
- 若实现要求改变 RunSpec、读取真实 truth、扩大依赖/资源或无法保持 v1 exact behavior，立即
  停止并返回 human review。

## 8. Phase A 对账与下一门禁

本文候选覆盖 Issue #36 Phase A：

- [x] #28、PR #30、两次批准和 #36 链接已核对；
- [x] identity alternatives 已比较，并选择 exact canonical file bytes；
- [x] primary/sensitivity、task bindings、universe、tie/non-finite/duplicate/reporting 已固化；
- [x] static/sequential metrics 的 IDs、公式、方向和 undefined behavior 已固化；
- [x] M2、M3–M5、task-first 与 seed 角色边界已固化；
- [x] v1/v2 dispatch、sentinel、migration/rollback 已固化；
- [x] evaluator/oracle、result shape、leakage 与 held-out boundary 已固化；
- [x] ADR-0003 与 RunSpec boundary 已固化；
- [ ] 独立人工评论对本文所在 exact commit 给出
  `APPROVED FOR TDD IMPLEMENTATION`。

因此本提交完成的是 **Phase A freeze candidate**，不是 Phase A approval，更不是 Phase B
implementation complete。人工复核若修改任何 normative rule，必须更新本文并锚定新的 exact
commit 后重新批准。
