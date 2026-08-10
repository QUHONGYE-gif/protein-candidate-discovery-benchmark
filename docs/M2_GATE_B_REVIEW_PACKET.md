# M2 Gate B 审核包

本文最初说明 Issue #58 的 Gate B review packet candidate；其 exact bytes、identity、执行顺序与
停止条件继续作为正式运行的历史审核依据。Issue #58 已于 2026-08-10 完成 final evidence review
并关闭，因此本文同时记录 packet 的最终执行 disposition。它不授权新的 execution、retry、
replacement、M3、Gate 2、held-out 或 scientific claim。

## 当前状态

- Gate A evidence：`PASS / ACCEPTED`，历史 replacement-start deviations 已逐次授权、披露并由
  final reviewer 接受；Gate A 未重跑。
- Gate B authorization：已对 exact packet/runtime/environment 授予并由一次 formal entry 消耗；
  不产生第二次执行授权。
- Formal runtime：merged commit `1e84448584c05f35f456041634757b4ae184869c`、tree
  `d132e309a1786ee2a4bb3247eec685e6f8c9ce65`、environment digest
  `0994a73f124c2baa6f330bf8739db1673217bdec6ad7d9046ded908fc8dba4fc`。
- Packet digest：`f4b26934ab22b9c74feaf05f970b0ea96f529c59045703322b5f5b242d9428f9`；
  scientific source identity 仍为六份 RunSpec 绑定的
  `9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5`，未与 runtime provenance 混写。
- Formal execution：单次启动、exit code 0、automatic retry/replacement/overwrite/Delete 均为 0；
  六份 RunAttempt succeeded，六个 empty `_SUCCESS` 完成。
- Evidence：68/68 objects、81,949,677 bytes，missing/extra/duplicate 为 0；COS 与 scratch
  exact-cover，独立 fresh readback 通过，keyset SHA-256 为
  `7f669230b81fc725b655764fbb0e8741540a8fcbbb28c6cca11a68fdce84bd65`。
- Final review：`APPROVED / FINAL EVIDENCE ACCEPTED / ORIGINAL PURPOSE ACHIEVED`；
  [Issue #58](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/58)
  已按 completed 关闭。

## 正式结果与声明边界

| task | model | Spearman | HV recall 0.5% | HV recall 1% | HV recall 2% |
|---|---|---:|---:|---:|---:|
| alpha-amylase | ridge-sklearn-v1 | 0.102183 | 0.000000 | 0.000000 | 0.000000 |
| alpha-amylase | random-forest-sklearn-v1 | 0.107530 | 0.000000 | 0.086957 | 0.217391 |
| alpha-amylase | xgboost-cpu-v1 | 0.288359 | 0.083333 | 0.086957 | 0.086957 |
| ired | ridge-sklearn-v1 | 0.104306 | 0.090909 | 0.047619 | 0.047619 |
| ired | random-forest-sklearn-v1 | 0.149336 | 0.000000 | 0.000000 | 0.000000 |
| ired | xgboost-cpu-v1 | 0.157354 | 0.000000 | 0.047619 | 0.071429 |

这些结果只形成两个冻结 task 的 static-baseline evidence。Prediction 与 high-value recall 排序会随
task/threshold 改变，不能推出某模型跨 task 普遍最优。M3 anti-post-hoc freeze 已在首次 candidate
truth read 前发布并验证，但其 authorization boundary 仍为 `m3_execution_authorized=false`、
`gate_2_authorized=false`、held-out reads 为 0。

完整执行记录见 [formal execution 与独立 readback](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/58#issuecomment-5232065229)，
最终批准见 [final evidence review](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/58#issuecomment-5232152978)。

## 审核材料

审核材料位于 `manifests/experiments/m2/issue-58/`：

| 文件 | bytes | SHA-256 | 用途 |
|---|---:|---|---|
| `gate-a-known-inputs.v1.json` | 16,873 | `783e338fd20396ea340d10140592c2752a30ac8fc7115e71b432c0796fe03ae2` | 已接受 Gate A scratch 的 compact exact identity index |
| `m3-anti-post-hoc-freeze.v1.json` | 2,548 | `89da42b38940fb60a65a4c18b1e8e6bf613229b27dfcb6efdb13d0afb07ee322` | 首次 candidate-truth read 前必须发布并 fresh validate 的 M3 freeze candidate |
| `gate-b-operator-plan.v1.json` | 105,496 | `19279631eab29fff0e8e322c3778d147e6aa31a723459aea066d160cc117a13d` | 113-step fail-fast publication/readback plan |
| `gate-b-packet.v1.json` | 189,759 | `f4b26934ab22b9c74feaf05f970b0ea96f529c59045703322b5f5b242d9428f9` | Gate B canonical review packet candidate |

这些文件不包含 credential、candidate truth、high-value membership、服务器地址或本机绝对路径。
Known-input index 只保存 logical path、size、SHA-256、RunSpec value 和 attempt identity，不保存
feature/prediction rows。该 compact index 的完整 canonical value 也嵌入 Gate B packet，使 loader
可以把 M3 roots、六个 RunSpec/run IDs 和 38 个 known payload identity 交叉绑定到同一 reviewed
index；约 82 MB 的 payload 继续保留在已接受的 Gate A scratch，不进入 Git。

## 68-object evidence layout

冻结 evidence bucket 为 `pcdb-benchmark-artifacts-1408226911`，exact prefix 为：

```text
m2/issue-58/alpha-ired-static-v1/9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5/
```

Inventory 精确包含：

- 26 个 shared objects：两套 feature logical/descriptor/config/三 role matrices、六份
  RunSpec、两份 Experiment Config、三份 model config、一份 acquisition config、一份
  environment manifest 和一份 M3 freeze record；
- 42 个 per-attempt objects：六个 run 各一份 sealed prediction、四份 aggregate metric
  results、一份 RunAttempt 和最后创建的空 `_SUCCESS`；
- 38 个 payload identity 在审核前已知；
- 24 个 metric result 与 6 个 RunAttempt 只能在 runtime 生成，其 size/SHA 在 packet 中明确为
  `null`，不得使用 placeholder hash；
- 这 30 个 runtime-generated payload 的完整 generation/validation rule 也被 exact-frozen；缺字段、
  多字段或任一值漂移都会被 loader 拒绝；
- Dataset/Processing/Split Manifests 是六个外部 immutable references，不复制到 evidence
  prefix，也不计入 68 个对象。

Gate A sealed predictions 被作为 formal frozen inputs 复用，保持原 `attempt_id` 与 exact bytes。
这避免通过改写 `attempt_id` 产生新的、未经审核 prediction identity。正式 RunAttempt 必须使用同一
identity 并准确记录 publication/evaluation closure；reviewer 若不接受该 provenance，Gate B 继续
保持关闭，不能静默重封装 prediction。

## Fail-fast operator 与 readback

Operator plan 固定以下顺序：

1. fresh-readback 六个外部 Dataset/Processing/Split Manifest references，并验证 exact
   key/size/SHA；
2. 使用 forbid-overwrite 写入 26 个 known shared payload；
3. fresh readback 并验证 M3 freeze record；
4. 对每个 run 先 fresh validate sealed prediction；
5. prediction 写入并 fresh readback 通过后，capability-separated evaluator 才能读取该 task 的
   candidate truth；
6. evaluator 只能返回 Spearman 与三个 frozen high-value recall aggregate results；
7. payloads first，RunAttempt penultimate，空 `_SUCCESS` last；
8. `alpha-amylase × ridge-sklearn-v1` 完成后必须 exact-cover 26+7=33 keys；失败时其余五个
   run 保持 0；
9. 全部执行完成后 independent reader 必须拒绝 missing、extra、duplicate，并 exact-cover 68
   keys。

每一个 operator step 的失败动作都是 `stop-no-retry-no-next-run`。当前 Gate 不允许 automatic
retry、replacement attempt、DeleteObject 或 overwrite。

`execute_gate_b_operator()` 是该计划的最小 executable consumer。它只接受完整 Gate B packet
bytes，而不接受 standalone operator bytes；入口先 strict-validate known-input index、M3
roots/cohort、38 个 known payload identities、Issue #58 exact prefix、credential boundary、caps
与 68-object inventory，再从该 packet 派生 113 steps。随后每类操作绑定到
`GateBOperatorRuntime` 的窄能力。runtime 必须先通过 `bind_reviewed_packet()` 确认完整 caps、
credential scope 与 identities；binder 收到的是独立、递归不可变的 canonical snapshot，不能在
pre-step 中改写 caps、credential boundary、source identity 或 operator plan。该 pre-step 失败时
113 个操作全部为 0。dispatcher 同样为每个 runtime method 创建独立、递归不可变的 canonical
step snapshot；handler 不能在 validation 后清空或改写 `required_exact_keys`、output keys 或
referenced keys。随后才允许：
external/input fresh read、forbid-overwrite publication、sealed prediction readback、四项 aggregate
evaluator、RunAttempt generation、`_SUCCESS` 以及 33/68-key independent readback。任何 runtime
方法抛出异常都会立即终止；executor 不捕获、不重试，也不会进入下一 step/run。injected-fake tests
覆盖完整 113-step 调用、首个 external read 失败时 truth/write 均为 0，以及首个 evaluator 失败时
其余五个 run 为 0，以及 binder 对四类关键嵌套字段、runtime handler 对三类嵌套 key list 的篡改
尝试均 fail closed。

后续修复候选增加了唯一正式入口 `pcdb m2-gate-b`、`FrozenGateBRuntime`、只使用 CVM
Role 临时凭据的窄 COS XML client，以及由 `CosTruthBundleReader` 独占 candidate truth 读取能力的
`FrozenM2AggregateEvaluator`。runtime 仍完全消费 packet 中既有 113 steps，不重写或解释出另一套
顺序。COS client 没有 DeleteObject API；PUT 固定携带 `x-cos-forbid-overwrite: true`，临时 token
进入签名，且客户端在获取凭据和打开网络请求前拒绝已知 request body 越过 network cap，runtime
另行执行 object/bytes/wall-time caps。所有 known 和 runtime-generated payload 均先通过既有
`LocalArtifactStore` 以禁止覆盖方式写入唯一空 scratch；20 GiB scratch cap 在本地写入前检查，
fresh local readback 的 size/SHA 通过后才允许 COS publication。evaluator 的公开返回值
只能是四份 canonical aggregate result bytes，truth 和 high-value membership 不进入 runtime state、
RunAttempt 或 COS publication。

CLI 在执行前绑定并验证 actual runtime Git commit、reviewed environment manifest 对应的运行平台、
Python/package 版本和 thread policy；实际导入的 runtime、evaluator 与 COS adapter 必须来自该
exact checkout，且 worktree 必须 clean。CLI 在首次执行副作用前记录 attempt start。RunAttempt 的
`executed_git_commit`、`executed_environment_digest`、`started_at` 和 `finished_at` 记录实际执行身份
与边界；scientific source commit/environment 继续只保留在 immutable RunSpec 中，不再混写为
execution provenance。正式 prediction/high-value closure 使用独立提供的 reviewed execution identity
验证 `executed_*`，不再错误地要求其与 RunSpec scientific source exact-match。

该实现及其 injected-fake tests 仍只是 **engineering readiness candidate**：测试不访问真实 COS，
不读取 candidate truth，不写 evidence prefix，也不把 Gate B 改为已授权。reviewer 必须复核 exact
head 后，才可以另外给出正式 approval。

## 最小 CAM 边界

Packet 只记录非敏感权限摘要，不记录 SecretId/SecretKey：

- accepted-input bucket：只允许 exact object keys 的 fresh Get/Head 与 restricted inventory；
- new-evidence bucket：只允许上述 exact prefix 的 forbid-overwrite Put、fresh Get/Head 与
  restricted inventory；
- CAM actions 仅为对应 scope 的 `name/cos:GetObject`、`name/cos:HeadObject`、
  `name/cos:GetBucket`，以及 evidence prefix 的 `name/cos:PutObject`；
- 显式禁止 DeleteObject、overwrite、其他 bucket 与其他 prefix。

权限配置完成后也不得用 smoke object 试写，因为 68 是 hard cap。Gate B reviewer 明确批准前，
只能进行 policy/identity 的零写入检查。

### 2026-08-06 当前执行身份/策略零写入摘要

本轮只做非敏感、只读检查，没有输出 SecretId/SecretKey/token：

- 本机默认 COSCLI config 仅配置 `data` alias（public-data bucket）；对 accepted manifest prefix 的
  restricted list 与已冻结 manifest 的 remote hash/metadata 命令均返回 0；
- 指定的正式 compute server 当前用户下没有 `~/.cos.yaml`，也没有发现 COS/Tencent
  credential 环境变量名；因此它当前不能作为已配置的 Gate B publication identity；
- 没有执行 PutObject、DeleteObject、overwrite、candidate-truth read 或 evaluator call；新 evidence
  prefix 的 object count 仍不能由未配置的正式 identity 声称已验证；
- COSCLI 只读能力检查不能证明 attached CAM policy 的 exact-prefix Put/no-Delete 全集。正式执行前仍需
  由 CAM 控制台或可审核的 policy export 确认两侧 `HeadObject`、accepted exact-key
  Get/List，以及 evidence exact-prefix Get/Head/List/Put，同时确认无 DeleteObject 和无其他 scope。

该段是 2026-08-06 的历史检查结果，已由后续 CAM/CVM Role 零写入验收取代；不得再据此判断当前
权限状态。

### 2026-08-07 CAM/CVM Role readiness 更新

Issue #58 的后续零写入验收确认：`PCDBIssue58GateBRunner` 只关联收敛后的 Issue-scoped COS policy，
accepted inputs 保持 exact keys read-only，evidence store 保持 exact prefix 的 Get/Head/List 与带
forbid-overwrite 条件的 Put，未授予 DeleteObject，且 evidence bucket 从未启用版本控制。该结果关闭
的是外部身份/策略 blocker，不等于 Gate B approval，也没有执行 truth read、evaluator 或 COS write。

### 2026-08-09 merged runtime provenance

PR #63 已由 independent reviewer 对 exact head `80d77e8ea249286be03f16b41f02912303820d67`
批准，并 squash merge 为 `1e84448584c05f35f456041634757b4ae184869c`。二者 Git tree 均为
`d132e309a1786ee2a4bb3247eec685e6f8c9ce65`，但正式 RunAttempt 的 `executed_git_commit` 必须记录
actual merged runtime provenance，不能用 tree equality、PR head 或本地 branch 名替代。

后续 Issue #58 正式执行必须绑定以下可审核身份：

```text
runtime Git commit: 1e84448584c05f35f456041634757b4ae184869c
runtime Git tree: d132e309a1786ee2a4bb3247eec685e6f8c9ce65
environment digest: 0994a73f124c2baa6f330bf8739db1673217bdec6ad7d9046ded908fc8dba4fc
packet digest: f4b26934ab22b9c74feaf05f970b0ea96f529c59045703322b5f5b242d9428f9
```

已对一个使用上述 merged commit 的 fresh clean checkout 完成 packet、source/environment
binding、12-object HEAD 和 evidence-prefix-zero 的零副作用预检。该次服务器的用户名、
checkout 目录与本地 shell 配置只是 out-of-band operations 记录，不属于持久化 protocol 或
run identity；compute server 与这些路径均可替换。PR #63 的 approved head
`80d77e8ea249286be03f16b41f02912303820d67` 只作为历史审核身份，不得记录为后续正式
attempt 的 `executed_git_commit`。

该 operational binding 只收敛实际运行身份，不授予 Gate B，不创建 scratch，也不授权 candidate
truth、evaluator、COS publication、RunAttempt 或 `_SUCCESS`。

## 唯一正式入口（仅供 exact-head review）

reviewer 批准前不得运行。批准后由 operator 选择 fresh checkout、accepted Gate A root 和新空
scratch；不得在 formal entry 前 `source` 未跟踪的 shell 文件。命令中直接传入 approved
merged commit literal，并在进入 runtime 前再次验证 actual HEAD 与 clean worktree：

```bash
repository_root=/path/to/fresh-clean-reviewed-checkout
gate_a_root=/path/to/accepted-gate-a-scratch
scratch_root=/path/to/new-empty-gate-b-scratch

cd "$repository_root"
test "$(git rev-parse HEAD)" = \
  1e84448584c05f35f456041634757b4ae184869c
test -z "$(git status --porcelain=v1 --untracked-files=all)"

pcdb m2-gate-b \
  --packet manifests/experiments/m2/issue-58/gate-b-packet.v1.json \
  --packet-sha256 f4b26934ab22b9c74feaf05f970b0ea96f529c59045703322b5f5b242d9428f9 \
  --approval 'APPROVED FOR ALPHA + IRED THREE-MODEL M2 FORMAL EXECUTION' \
  --runtime-git-commit 1e84448584c05f35f456041634757b4ae184869c \
  --gate-a-root "$gate_a_root" \
  --repository-root "$repository_root" \
  --scratch-root "$scratch_root"
```

入口在任何 COS/truth side effect 前验证 packet bytes、显式 approval、当前 Git head、实际运行环境、
空 scratch、process caps 和 evidence exact prefix 为空。运行失败时不自动 retry、replacement、删除
或覆盖。

## 重建与验证

在保留的 Gate A scratch 可读时运行：

```bash
PYTHONPATH=src .venv/bin/python scripts/build_m2_gate_b_packet.py \
  --repository-root "$PWD" \
  --gate-a-root /path/to/accepted-gate-a-scratch \
  --output-dir manifests/experiments/m2/issue-58 \
  --evidence-bucket pcdb-benchmark-artifacts-1408226911 \
  --evidence-prefix m2/issue-58/alpha-ired-static-v1/9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5/
```

默认拒绝覆盖已有 review artifacts；只有在明确重新生成候选、随后重新审核全部 exact bytes 时才可
使用 `--overwrite`。验证命令：

```bash
.venv/bin/python -m pytest -q \
  tests/m2/test_gate_b_packet.py \
  tests/m2/test_gate_b_runtime.py
```

任何 packet、M3、operator、known-input、prefix、permission、environment 或 RunSpec identity
漂移，都必须保持 Gate B 关闭并回到 Issue #58 复审。
