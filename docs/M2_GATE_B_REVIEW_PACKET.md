# M2 Gate B 审核包

本文说明 Issue #58 的 Gate B review packet candidate。它是执行前的只读审核材料，
不是 Gate B 授权、正式运行结果、COS publication、benchmark evidence 或 scientific gate。

## 当前状态

- Gate A evidence 已由 reviewer 接受，但带有已披露、逐次由用户授权的 replacement-start
  deviations。
- Gate B 仍是 `NOT GRANTED / CHANGES REQUIRED`。
- 本次准备过程保持 candidate truth read、evaluator call、COS write、RunAttempt 和
  `_SUCCESS` 全部为 0。
- 正式 source identity 仍是 Gate A 六份 RunSpec 绑定的
  `9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5`。本 PR 只增加 packet compiler 与
  validator，不修改或冒充该 scientific run identity。

## 审核材料

审核材料位于 `manifests/experiments/m2/issue-58/`：

| 文件 | bytes | SHA-256 | 用途 |
|---|---:|---|---|
| `gate-a-known-inputs.v1.json` | 16,873 | `783e338fd20396ea340d10140592c2752a30ac8fc7115e71b432c0796fe03ae2` | 已接受 Gate A scratch 的 compact exact identity index |
| `m3-anti-post-hoc-freeze.v1.json` | 2,548 | `89da42b38940fb60a65a4c18b1e8e6bf613229b27dfcb6efdb13d0afb07ee322` | 首次 candidate-truth read 前必须发布并 fresh validate 的 M3 freeze candidate |
| `gate-b-operator-plan.v1.json` | 105,496 | `19279631eab29fff0e8e322c3778d147e6aa31a723459aea066d160cc117a13d` | 113-step fail-fast publication/readback plan |
| `gate-b-packet.v1.json` | 171,410 | `b5bbba5a0debc01487cfdfe7a909d81d50a7627ca8dfcbbadd9f65b6ac3f7ebf` | Gate B canonical review packet candidate |

这些文件不包含 credential、candidate truth、high-value membership、服务器地址或本机绝对路径。
Known-input index 只保存 logical path、size 和 SHA-256；约 82 MB 的 feature/prediction payload
继续保留在已接受的 Gate A scratch，不进入 Git。

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

## 最小 CAM 边界

Packet 只记录非敏感权限摘要，不记录 SecretId/SecretKey：

- accepted-input bucket：只允许 exact object keys 的 fresh Get/Head 与 restricted inventory；
- new-evidence bucket：只允许上述 exact prefix 的 forbid-overwrite Put、fresh Get/Head 与
  restricted inventory；
- CAM actions 仅为对应 scope 的 `name/cos:GetObject`、`name/cos:GetBucket`，以及 evidence
  prefix 的 `name/cos:PutObject`；
- 显式禁止 DeleteObject、overwrite、其他 bucket 与其他 prefix。

权限配置完成后也不得用 smoke object 试写，因为 68 是 hard cap。Gate B reviewer 明确批准前，
只能进行 policy/identity 的零写入检查。

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
.venv/bin/python -m pytest -q tests/m2/test_gate_b_packet.py
```

任何 packet、M3、operator、known-input、prefix、permission、environment 或 RunSpec identity
漂移，都必须保持 Gate B 关闭并回到 Issue #58 复审。
