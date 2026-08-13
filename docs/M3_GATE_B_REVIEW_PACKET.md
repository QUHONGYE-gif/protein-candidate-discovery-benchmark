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

若 Gate A 的技术 evidence 已存在，但 Issue 可见记录不能独立重建两次历史执行授权，packet 不得把
技术 `PASS` 写成治理闭环。默认状态必须为 `PENDING-USER-ADJUDICATION`，且 Gate B execution
ineligible。用户若决定接受这个已披露的治理偏差，只能记录新的 evidence-acceptance comment ID；
这既不是追溯授权，也不改变原始 Gate A evidence bytes，仍不授权 Gate B。

Gate A evidence 中 determinism、recovery、no-repeat、budget 和 four decision metrics 必须为
`PASS`；runtime 无法自行核验的 resource-cap 项只能由绑定的 operator attestation 关闭。

## 单一 Gate B 授权与两阶段 publication

Gate A 与 Gate B 是两个独立授权点。一次 exact Gate B authorization 覆盖已冻结的完整
sentinel-first sequence；writer 和 independent reader 仍使用能力分离的身份，但不各自产生新的
人工授权 Gate。Gate B 内部按以下机械停止点执行：

1. **writer sentinel**：只生成最低成本 IRED sentinel 的本地 payload，严格验证后冻结该 RunSpec
   的 9-key actual key/size/SHA inventory；只有它 exact-cover sentinel expected schema，才允许
   writer 发布。其余 17 RunSpecs 此时不得运行。
2. **independent reader sentinel**：writer 退出并切换到只读角色；reader 独立列举 sentinel exact
   keyset，拒绝 missing、extra、duplicate，并 fresh-read 验证 bytes、checksums 与 cross-links。
3. **remaining matrix**：只有 sentinel independent readback PASS，才允许运行余下 17 RunSpecs；
   随后在其 first PUT 前另行冻结并验证 153-key actual inventory。失败时立即停止，不请求扩大权限。

每个 RunSpec 固定 9 个对象：三个 checkpoint、三个 metric、trajectory、RunAttempt 和空
`_SUCCESS`。payloads 先写，RunAttempt 倒数第二，`_SUCCESS` 最后。所有 PUT 必须使用
`x-cos-forbid-overwrite=true`，每个 PUT 后立即 fresh readback。

packet 还必须冻结实际 Gate B runtime commit 与 environment digest。`validate_actual_inventory()`
不得允许调用方省略二者；remaining-matrix 校验必须带入 sentinel 已接受的 object count 与 bytes，
使 162-object / 2 GiB 上限按一次 Gate B publication 累计计算，而不是分段重置。

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
  --gate-a-evidence-acceptance-comment-id "$ACCEPTANCE_COMMENT_ID" \
  > /tmp/m3-issue-68-gate-b-packet.json
shasum -a 256 /tmp/m3-issue-68-gate-b-packet.json
wc -c /tmp/m3-issue-68-gate-b-packet.json
```

只有用户明确接受上述 Gate A 治理偏差后才能提供
`--gate-a-evidence-acceptance-comment-id`；未提供时生成的 packet 保持 reviewable 但 Gate B
ineligible。packet 需经独立 exact-bytes review。随后仍须审核 actual roles/policies、fresh checkout、
empty evidence prefix、empty scratch 和正式启动命令，并单独取得一次 exact Gate B authorization。

## 最小正式执行入口

execution surface 合并后，最终 packet 必须重新绑定该 merged commit/tree。正式入口只有
`pcdb m3-gate-b`，同一 exact packet、approval text 和 approval comment ID 贯穿四个机械 phase：

1. `sentinel-write`：writer role 对 12 个 reviewed inputs 先做 HEAD，仅 GET sentinel 所属 task 的
   6 个对象且 hidden truth 最后；只生成 sentinel trajectory，在本地冻结 9-key actual inventory，
   再以 forbid-overwrite 发布并逐对象 fresh readback；
2. `sentinel-read`：writer 退出后切换 independent reader role，exact-cover/fresh-read sentinel，
   生成 payload-free `sentinel-readback.json`；
3. `remaining-write`：切回 writer role，只有 readback checkpoint 与 packet/sentinel actual inventory
   完全一致才生成余下 17 RunSpecs；冻结 153-key actual inventory 后逐 RunSpec 发布；
4. `final-read`：writer 再次退出并切换 reader role，exact-cover/fresh-read 全部 162 对象，随后生成
   `final-readback.json`。该文件只声明五项 Gate 2 engineering conditions，不支持科学优越性结论。

示意命令（仍需把路径、SHA 和 comment ID 换成最终独立审核并获授权的 exact 值）：

```bash
pcdb m3-gate-b \
  --phase sentinel-write \
  --packet /absolute/reviewed-gate-b-packet.json \
  --packet-sha256 "$GATE_B_PACKET_SHA256" \
  --approval 'APPROVED FOR M3 ALPHA + IRED GATE B FORMAL EXECUTION AND PUBLICATION' \
  --approval-comment-id "$GATE_B_APPROVAL_COMMENT_ID" \
  --hourly-cost-cny "$CVM_HOURLY_COST_CNY" \
  --repository-root /absolute/final-clean-checkout \
  --scratch-root /absolute/fresh-gate-b-scratch
```

其余三个 phase 只修改 `--phase`。scratch checkpoint 使用排他创建且不得覆盖；任一步失败均不得
重跑、替换、删除或扩权。正式启动前必须单独验收 writer/reader role binding、empty prefix、fresh
scratch、environment 与 operator cost/resource caps。

四个进程不是四份独立资源额度。`sentinel-write` 会记录一次 sequence start，之后每个 phase 只能从
前一 phase 的 packet/approval/hourly-cost-bound resource checkpoint 继续；checkpoint 累计已用 network
bytes，并以同一 sequence start 计算 wall time 与 cost，以整个 scratch root 计算 scratch bytes。phase
start、每个 RunSpec 前后以及下一次 HEAD/GET/PUT/LIST 前均须检查剩余额度。任何 cap 失败后不得继续
下一个 RunSpec 或远程操作，也不得通过重新启动 phase 重置额度。

## 当前声明边界

本实现可以证明 packet builder、four-phase execution surface 和 publication consumer 对
synthetic/local fixture fail closed；不能证明真实 18-RunSpec payload 已生成、COS prefix 为空、CAM
已配置、Gate B 已授权/执行、publication 已完成、Gate 2 PASS 或任一模型/策略具有科学优越性。
