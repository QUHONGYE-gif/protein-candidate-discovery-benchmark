# M3 Gate B 审核包准备

本文定义 Issue #68 Gate B review packet 的最小实现边界。该 packet 只冻结真实执行所需的
身份、expected inventory、阶段顺序、能力分离和资源上限；生成、提交或审核 packet 均不授权
服务器、COS、truth read、formal execution 或 publication。

## 前置证据

Gate B packet 必须同时绑定：

- Gate A merged-source packet 的 exact bytes/SHA；
- Gate A immutable evidence 的 exact bytes/SHA；
- 独立 operator resource/billing attestation comment；
- 当前 Gate B implementation 的 exact merged commit/tree；
- Issue #66 冻结的 18 RunSpecs 与 Issue #67 ReplayEngine/publication contracts。

Gate A evidence 中 determinism、recovery、no-repeat、budget 和 four decision metrics 必须为
`PASS`；runtime 无法自行核验的 resource-cap 项只能由绑定的 operator attestation 关闭。

## 两阶段 publication gate

Gate B 采用三个彼此独立的授权阶段：

1. **writer sentinel**：只生成最低成本 IRED sentinel 的本地 payload，严格验证后冻结该 RunSpec
   的 9-key actual key/size/SHA inventory；只有它 exact-cover sentinel expected schema，才允许
   writer 发布。其余 17 RunSpecs 此时不得运行。
2. **independent reader sentinel**：writer 退出并切换到只读角色；reader 独立列举 sentinel exact
   keyset，拒绝 missing、extra、duplicate，并 fresh-read 验证 bytes、checksums 与 cross-links。
3. **remaining matrix**：只有 sentinel independent readback 经新的审核并明确继续授权后，才允许
   运行余下 17 RunSpecs；随后在其 first PUT 前另行冻结并验证 153-key actual inventory。

每个 RunSpec 固定 9 个对象：三个 checkpoint、三个 metric、trajectory、RunAttempt 和空
`_SUCCESS`。payloads 先写，RunAttempt 倒数第二，`_SUCCESS` 最后。所有 PUT 必须使用
`x-cos-forbid-overwrite=true`，每个 PUT 后立即 fresh readback。

## 能力边界

- writer：12 个 exact input objects 的 `HeadObject/GetObject`，以及一个 exact evidence prefix 的
  restricted list、`HeadObject/GetObject/PutObject`；PUT 必须带 forbid-overwrite 条件。
- independent reader：只允许 exact evidence prefix 的 restricted list、`HeadObject/GetObject`。
- 两个角色均不得拥有 `DeleteObject`、其他 bucket/prefix、overwrite 或未审核权限。

角色策略和 CVM binding 必须在 formal authorization 前另行审核。packet 中的 policy 声明不是实际
CAM attestation。

## 合并后物化

必须在本 PR 合并后使用 fresh clean checkout 物化 Gate B packet。Gate A packet/evidence 应使用
已接受的原始 bytes；若当前 checkout 已不再重建旧 Gate A commit，可通过单独的 clean
`--gate-a-repository-root` 指向 `4b36bbb3...` source checkout：

```bash
pcdb m3-gate-b-packet \
  --reviewed-git-commit "$GATE_B_COMMIT" \
  --reviewed-git-tree "$GATE_B_TREE" \
  --repository-root "$PWD" \
  --gate-a-repository-root /absolute/clean/gate-a-source \
  --gate-a-packet /absolute/gate-a-packet.json \
  --gate-a-packet-sha256 3801c9705fea26f87ac90de4604ed3b418bbb31ba17d74dc1ca9453ed35fc177 \
  --gate-a-evidence /absolute/gate-a-evidence.json \
  --gate-a-evidence-sha256 eb18373e39d2895f313c14a12af354fdb872b936e8640971cae412a508a58335 \
  --gate-a-operator-attestation-comment-id 5269603341 \
  > /tmp/m3-issue-68-gate-b-packet.json
shasum -a 256 /tmp/m3-issue-68-gate-b-packet.json
wc -c /tmp/m3-issue-68-gate-b-packet.json
```

packet 需经独立 exact-bytes review。随后仍须分别审核 actual roles/policies、fresh checkout、empty
evidence prefix、empty scratch 和正式启动命令。

## 当前声明边界

本实现可以证明 packet builder 和 publication consumer 对 synthetic/local fixture fail closed；不能
证明真实 18-RunSpec payload 已生成、COS prefix 为空、CAM 已配置、Gate B 已授权/执行、publication
已完成、Gate 2 PASS 或任一模型/策略具有科学优越性。
