# M3 Gate A 审核包准备

本文定义 Issue #68 的 Gate A review packet 如何在不访问服务器、COS 或真实输入的情况下生成、
复核和消费。它是实现准备，不是 Gate A 执行授权，也不是 Gate 2 证据。

## 当前边界

review packet builder 只读取 Git 中已经审核的配置和 manifest：

- Issue #66 的 protocol freeze 与 run-spec matrix；
- Issue #67 合并后的 ReplayEngine contracts；
- 已接受的 M2 Gate B packet 中的 manifest/feature provenance；
- 已接受的 CPU environment candidate；
- 两份 M3 Experiment Config、三份 model config 和三份 acquisition config。

构建过程的边界固定为：

- candidate truth reads = 0；
- real input reads = 0；
- server accesses = 0；
- COS accesses/PUT = 0；
- formal RunAttempt 和 `_SUCCESS` = 0。

## 冻结内容

packet 由 reviewed Git commit 和 Git tree 共同绑定，并确定性生成：

- Alpha Amylase 与 IRED；
- Ridge、Random Forest 与 XGBoost；
- Random、Greedy 与 Uncertainty；
- seed `1729`、`initial=16`、`batch=8`、`budget=32`；
- 18 份完整 RunSpec 和对应 run ID；
- 12 个 Gate A exact read-only input objects；
- CPU environment digest 与 #66 resource caps；
- `COS PUT=0`、Delete/overwrite/retry/replacement=0；
- 最低成本 sentinel：`ired × ridge-sklearn-v1 × random-seeded-sha256-v1`。

sentinel 的选择只使用运行前已知成本代理：IRED 的 frozen candidate pool 更小，Random acquisition
不需要 acquisition-side model fitting，Ridge 使用最小的 deterministic model config。该选择不使用
M2 prediction results，也不表示模型或策略优越性。

12 个输入对象由以下内容组成：

- 两个任务各自的 dataset、processing 和 split manifest；
- 两个任务各自的 feature descriptor 与 candidate feature matrix；
- 两个任务各自的 hidden-truth object，且在 packet 的 read order 中最后出现。

只允许对这些 exact keys 使用 `GetBucket`、`HeadObject` 和 `GetObject`。任何 `PutObject`、
`DeleteObject`、更宽 bucket/prefix 或覆盖权限均不属于 Gate A。

## 合并后物化 exact packet

review packet 必须在实现 PR 合并后，使用合并提交重新生成。不得把 PR head packet 或历史 M2
packet 当作最终 Gate A packet：

```bash
git switch main
git pull --ff-only
COMMIT=$(git rev-parse HEAD)
TREE=$(git rev-parse 'HEAD^{tree}')
pcdb m3-gate-a-packet \
  --reviewed-git-commit "$COMMIT" \
  --reviewed-git-tree "$TREE" \
  --repository-root "$PWD" \
  > /tmp/m3-issue-68-gate-a-packet.json
shasum -a 256 /tmp/m3-issue-68-gate-a-packet.json
wc -c /tmp/m3-issue-68-gate-a-packet.json
```

输出必须经过独立 exact-bytes review。任何 commit/tree、environment、source artifact、RunSpec、
input key/size/SHA 或 policy drift 都必须重新生成并重新审核。

## Gate A entry validation

`validate_gate_a_entry()` 在任何真实输入读取前验证：

1. packet SHA-256 与 canonical bytes；
2. 通过 `git` 直接读取实际 `HEAD`、`HEAD^{tree}` 和完整 tracked/untracked status，拒绝 commit、
   tree 或 clean-state drift；
3. 实际导入的 Gate A validator、ReplayEngine 和 HiddenOracle module 必须来自该 repository；
4. packet 能由 exact repository bytes 重新生成；
5. 从实际 platform、Python/distribution versions 与 thread-policy environment 重建 environment
   digest，并与 packet 完全一致；
6. scratch 不在仓库的非忽略目录中，且开始时为空。

commit、tree 与 environment identity 均由 validator 直接派生，不接受调用方自报字符串。此
validator 本身不读取 input inventory，不创建 scratch，也不运行 replay。真实输入 staging、
same-RunSpec double-run 和 interrupted/recovered preflight 只有在该 exact packet 获得新的独立审核与
明确 Gate A 授权后才能执行。

## Gate A read-only runtime

Gate A runtime 是 Issue #68 的单一用途入口，不是通用实验调度器。它只允许：

1. 验证 exact approval、packet SHA、实际 commit/tree、clean checkout、运行模块、environment 与空
   scratch；
2. 对 packet 冻结的 12 个对象依次执行 `HEAD`，校验 exact size；
3. 仅对 IRED × Ridge × Random sentinel 的六个对象执行 fresh `GET`，hidden truth 必须最后读取；
4. 通过 `ReplayEngine.from_reviewed_inputs()` 在 truth-owning boundary 内构建 oracle/evaluator；
5. 对同一 RunSpec 做两次完整运行，并从排他创建且 fresh-read 的 round-0 checkpoint 恢复一次；
6. 校验 canonical trajectory、16/24/32 budget boundary、禁止重复购买、四项 metrics 与实际可观测
   resource caps；
7. 仅在本地 scratch 排他创建 `gate-a-evidence.json`，不创建 RunAttempt、`_SUCCESS` 或 COS 对象。

运行身份固定为 CVM role `PCDBIssue68GateAReader`。该角色只能具有 packet 内 12 个 exact object 的
`HeadObject`/`GetObject` 与受限 `GetBucket` 权限；不得授予 `PutObject`、`DeleteObject`、其他 bucket、
其他 prefix 或更宽 list 权限。入口不包含 PUT、Delete、automatic retry 或 replacement API。

只有最终实现合并后重新物化的 packet 完成独立 exact-bytes review，并由用户明确给出以下完整授权
字符串，才允许调用：

```text
APPROVED FOR M3 IRED SENTINEL GATE A READ-ONLY PREFLIGHT
```

授权后的服务器命令形状为：

```bash
pcdb m3-gate-a \
  --packet /absolute/reviewed/m3-gate-a-packet.json \
  --packet-sha256 '<reviewed-sha256>' \
  --approval 'APPROVED FOR M3 IRED SENTINEL GATE A READ-ONLY PREFLIGHT' \
  --repository-root /absolute/fresh-clean-checkout \
  --scratch-root /absolute/new-empty-scratch
```

必须先通过 `taskset`/CPU affinity 或等价服务器配置，将当前进程可见 CPU 限制在 packet 的
`max_vcpu` 内；runtime 会根据 affinity、peak RSS、wall time、fresh-GET bytes 和 checkpoint bytes
fail closed。云账单费用无法由进程内部可靠观测，evidence 只记录 reviewed ceiling，并要求 operator
独立核对实际费用。因此 runtime evidence 在费用核验前必须保持 `INCONCLUSIVE`；不能仅凭轨迹、
recovery 和资源测量成功自行声明 Gate A `PASS`。

任何失败均停止；不得在同一授权下 retry、替换 scratch、扩大 CAM 或复用部分 evidence。成功输出仍
只是 Gate A preflight evidence，Gate B 保持未授权，Gate 2 仍需最终独立证据复核。

## 审核结论范围

本准备完成后只能声明：

- Gate A packet implementation candidate 可供独立代码审核；
- 18 个 RunSpec 可以在合并提交上确定性物化；
- 非输入 entry conditions 可以 fail closed。

仍不能声明：

- Gate A 已授权或执行；
- candidate truth 已读取；
- server/COS preflight 已通过；
- Gate B publication 已开始；
- Gate 2 为 `PASS`；
- 任一模型或 acquisition strategy 具有科学优越性。
