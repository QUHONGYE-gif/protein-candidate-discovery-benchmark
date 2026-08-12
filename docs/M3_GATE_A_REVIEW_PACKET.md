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
2. packet 能由 exact repository bytes 重新生成；
3. runtime commit/tree 与 packet 完全一致；
4. runtime environment digest 与 packet 完全一致；
5. scratch 不在仓库的非忽略目录中，且开始时为空。

此 validator 本身不读取 input inventory，不创建 scratch，也不运行 replay。真实输入 staging、
same-RunSpec double-run 和 interrupted/recovered preflight 只有在该 exact packet 获得新的独立审核与
明确 Gate A 授权后才能执行。

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
