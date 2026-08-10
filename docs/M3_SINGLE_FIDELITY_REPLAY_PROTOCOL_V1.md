# M3 deterministic single-fidelity replay protocol v1

本文是 Issue #66 的 protocol-only candidate。它冻结 Alpha＋IRED 第一版单保真回放的科学与
工程输入，供 Issue #67 实现 synthetic vertical slice，并供 Issue #68 在独立授权后准备真实
preflight 和 Gate 2。

本文及其配置进入 `main` 只表示 protocol bytes 已接受。它不授权读取真实 candidate truth、
登录服务器、执行 replay、访问 COS、创建 RunAttempt、写 `_SUCCESS` 或判定 Gate 2。

## 1. 身份和声明边界

- protocol version：`m3-single-fidelity-replay-v1`；
- canonical hash tuple 中的 `protocol_id` exactly 等于上述 protocol version；
- predecessor bookkeeping baseline：`main@e2321596958e37491d25152ecd68a36aa059f745`；
- tasks：`alpha-amylase`、`ired`；
- feature config：`sequence-composition-420-v1`，SHA-256
  `2b9a9dbfb322f8b34296d208a2b8eb9392cac2b7056508039589fc3a0e34b77d`；
- models：冻结的 Ridge、Random Forest、XGBoost 三份 M2 config；
- seed set：exactly `[1729]`；
- held-out、WT-holdout、external validation、M4、多保真和科学优越性结论：关闭。

Dataset/Processing/Split Manifest、feature root 和 model config 的 exact hash 位于
[`run-spec-matrix.v1.json`](../manifests/experiments/m3/issue-66/run-spec-matrix.v1.json)。这些
identity 延续 Issue #58 已接受的 cohort，不得根据 M2 结果调整同一 cohort 的 feature、model、
hyperparameter、model set 或 seed。

## 2. Canonical tuple 和确定性排序

本文中的 hash tuple 使用以下唯一编码：

1. 按文中给定顺序构造 JSON array；
2. UTF-8 编码；
3. `allow_nan=false`、`ensure_ascii=false`、compact separators `(',', ':')`；
4. 不添加末尾换行；
5. 对所得 bytes 计算 SHA-256。

Candidate ID 始终使用完整 lowercase 64-hex。所有 score tie 最终都由 candidate ID ascending
拆分。Missing、duplicate、unknown 或 non-finite input 一律 error，不允许静默丢弃、截断或
fallback。

## 3. Matrix、initial、batch 和 budget

正式兼容矩阵是完整 Cartesian product：

```text
2 tasks × 3 frozen models × 3 acquisitions × 1 seed = 18 RunSpecs
```

Random 不消费 predictive mean，但仍保留对应 model identity，使 RunSpec 形状、模型兼容性与
对比单元固定。不得观察结果后删去重复的 Random rows。

所有 18 个组合固定：

- `initial_sample_size = 16`；
- `batch_size = 8`；
- `budget = 32` distinct revealed observations；
- initial 16 计入总 budget；
- round 0、1、2 的合法累计 reveal boundary 为 16、24、32。

Initial selection 不读取 truth。对每个 candidate 计算：

```text
SHA256([protocol_id, task_id, seed, candidate_id])
```

按 digest ascending、candidate ID ascending 取前 16。ReplayEngine 随后把这 16 个 ID 一次性
交给 HiddenOracle；批次必须先完整验证，再原子 reveal。每轮拟合时 observation table 按
candidate ID ascending；trajectory 仍记录真实 reveal order。

## 4. Acquisition configs

| acquisition | config bytes | SHA-256 | 选择规则 |
|---|---:|---|---|
| `random-seeded-sha256-v1` | 538 | `24f80499cb48ad6716f86a9c47bf098d47acdb78841bcb7b8926504619983e0f` | round-specific seeded hash ascending |
| `greedy-predictive-mean-v1` | 499 | `3c7c537bf6ada03c0a86c720c1a56382247baa00ad9f71eccf4f2f7a120adc15` | predictive mean descending |
| `uncertainty-bootstrap-std-v1` | 1,032 | `05c5e444068869d17f01ec79d1414fb3fa0989f05b08f66c8253a952c95a442f` | bootstrap prediction std descending |

### 4.1 Random

对每个未揭示 candidate 计算：

```text
SHA256([protocol_id, task_id, seed, round, candidate_id])
```

按 digest ascending、candidate ID ascending 取 exactly 8。Random 不允许调用 model prediction
或 oracle truth。

### 4.2 Greedy

使用 frozen model 对全部未揭示 candidates 产生 float64 predictive mean，按 mean descending、
candidate ID ascending 取 exactly 8。Prediction row 必须 exact-cover 未揭示 candidate set；
missing、extra、duplicate、unknown 或 non-finite prediction 均使整轮失败。

### 4.3 Uncertainty

三个 model pair 统一使用 acquisition-side adapter `bootstrap-prediction-std-v1`：

- 只使用当前已揭示 observations；
- observation table 先按 candidate ID ascending；
- exactly 5 个 size-`n`、with-replacement bootstrap samples；
- member RNG 为 NumPy `PCG64`；
- member seed 是
  `SHA256([protocol_id, run_seed, task_id, model_id, round, member_index])` 的前 8 bytes，
  按 unsigned big-endian 解码；
- 每个 member 使用同一 frozen model config 重新拟合；model hyperparameter 和 internal seed
  policy 不变；
- score 是 5 个 float64 predictions 的 population standard deviation，`ddof=0`；
- score 单位与 task label 相同，higher 表示更不确定；zero variance 合法；
- 任一 member fit/predict 失败或产生 non-finite，整轮 error，不回退到 Greedy/Random。

该 score 是未校准的 engineering uncertainty baseline，不得描述为 calibrated predictive
uncertainty。

## 5. UQ feasibility table

| model | mean source | uncertainty source | 修改 frozen model config | 新 dependency | failure |
|---|---|---|---|---|---|
| `ridge-sklearn-v1` | frozen `Ridge.predict` | 5 个相同 config bootstrap refits 的 prediction std | 否 | 否 | fail closed |
| `random-forest-sklearn-v1` | frozen `RandomForestRegressor.predict` | 5 个相同 config bootstrap refits 的 prediction std | 否 | 否 | fail closed |
| `xgboost-cpu-v1` | frozen `XGBRegressor.predict` | 5 个相同 config bootstrap refits 的 prediction std | 否 | 否 | fail closed |

Bootstrap sampling randomness 属于 acquisition config。RF/XGBoost 内部 random state 继续使用
各自 frozen model config 的 `1729`，不得按 member 改写。

## 6. Experiment Config 和 late-bound RunSpec fields

| task | Experiment Config bytes | SHA-256 |
|---|---:|---|
| Alpha Amylase | 4,752 | `8429109f531202fd51903dff43a3553b255365e99c9cf48411fba446029a5c12` |
| IRED | 4,734 | `3a886fa02b5411108d60774ba94c861e7ccaedec134a6c4263ae4d1bcf143c1f` |

两份 config 保留既有 high-value definition、evaluator ownership 和四项 M3 sequential metrics，
只把 experiment ID、三类 acquisition、initial、batch 和 budget 更新为本协议值。

[`run-spec-matrix.v1.json`](../manifests/experiments/m3/issue-66/run-spec-matrix.v1.json)
完整冻结 18 个组合的 task/model/acquisition/seed 与全部已知 hashes。它不是伪造的 final
RunSpec：`git_commit` 和 `environment_digest` 只有在 #67 exact implementation merge、#68
environment review 后才能绑定。因此这两个字段被显式列为唯一 late-bound fields；#68 Gate A
前必须生成并审核 18 份完整 RunSpec 和 run ID。

## 7. Hidden-label boundary

- Candidate features、surrogate 和 acquisition 只能接收 observable features 与已揭示 observation
  prefix；
- 只有 ReplayEngine 可以提交 selected IDs 给 HiddenOracle；
- high-value membership 和 universe maximum 只属于 evaluator/oracle；
- persisted trajectory 只允许包含已经 reveal 的 observation；
- unrevealed labels、high-value flags、tie membership 和 boundary summaries 不得进入 model、
  acquisition、checkpoint 或 selection logs。

## 8. Trajectory、checkpoint 和 recovery

Canonical trajectory 至少绑定 protocol/config、RunSpec/run ID、task/manifest/feature/model/
acquisition/seed/budget identities，并按顺序记录：

- initial selection；
- 每轮 selected candidate IDs；
- reveal 后的 observations；
- round boundary 和 cumulative distinct reveal count；
- checkpoint 与 metric artifact checksums。

每个完整 reveal boundary 后生成一个不可覆盖的 canonical checkpoint。Recovery 只接受同一
RunSpec 下 checksum-valid 的最高完整 checkpoint。恢复后的 final canonical trajectory 必须与
uninterrupted execution byte-identical。成功 attempt 不可覆盖；RunAttempt timestamp、compute
metadata、status 和 failure 不进入 run ID 或 trajectory identity。缺少 `_SUCCESS` 的 attempt
不得聚合。

## 9. Metrics

每个合法 reveal boundary 复用现有 Experiment Config v2 evaluator 计算：

- `best_found_value`；
- `simple_regret`；
- `high_value_discovery_recall`；
- `success_at_budget`。

`success_at_budget` 只在 trajectory 至少达到预注册 budget 时 defined。不得新增 metric
framework，也不得把 raw unitful metric 跨 task 直接求平均。

## 10. Worked example

[`worked-example.v1.json`](../tests/fixtures/m3/issue-66/worked-example.v1.json) 使用 6 个纯
synthetic candidate，手算并机器复核：

- compact JSON tuple hashing；
- initial selection；
- Random round selection；
- distinct budget 单调性；
- reveal 后 observation 边界；
- 两个 metric boundaries；
- interrupted/recovered 与 uninterrupted trajectory checksum 一致。

它不是 Alpha/IRED evidence，不能替代 #67 synthetic implementation 或 #68 real-input preflight。

## 11. Resource caps 和 stop conditions

#68 的任何 execution packet 不得超过：8 vCPU、16 GiB RAM、20 GiB scratch、6 hours wall、
10 GiB network、2 GiB COS net-new、512 evidence objects、50 CNY、每个 RunSpec 一次 formal
attempt。Automatic retry、replacement、DeleteObject 和 overwrite 均为 0。

若 preflight 表明 cap 不足、identity drift、hidden-label boundary 失败、实际 keyset 偏离预期、
scratch validation 失败或需要 retry/overwrite/Delete 才能继续，必须停止并重新审核，不能在执行
期间放宽协议。

## 12. Gate 和授权状态

本 candidate 的当前状态固定为：

```text
protocol_candidate_materialized = true
protocol_final_review_accepted = false
protocol_bytes_authorize_synthetic_tdd = false
synthetic_tdd_requires_independent_exact_bytes_review = true
protocol_bytes_authorize_real_input_preflight = false
protocol_bytes_authorize_m3_formal_execution = false
protocol_bytes_authorize_gate_2 = false
held_out_reads = 0
candidate_truth_reads = 0
server_executions = 0
cos_writes = 0
```

下一步只能对本 PR 的 exact bytes 做独立 review。只有该 review 明确批准 protocol freeze 并授权
#67 synthetic TDD 后，才能改变 #67 lifecycle；该批准仍不自动授权 #68 Gate A 或 Gate B。
