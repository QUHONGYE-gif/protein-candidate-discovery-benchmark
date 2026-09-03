# Governance v2：科学契约、执行证据与归档治理 RFC

- 状态：Accepted for Stage 3（纳入 2026-09-02 审查定稿）
- 适用范围：本 RFC 获批后启动的新 milestone 与 Governance v2 实现
- 历史基线：MIRA-2 审计，`origin/main@188e9a7`（2026-09-02）
- 规范词：`必须`、`不得`、`应`为规范性要求；`可以`表示许可而非要求

## 1. 目的与边界

本 RFC 将科学定义、执行身份、机器校验、人工科学判断和结果归档拆成可独立判定的契约。目标是让 Gate A 授权后的 milestone 在既定科学边界内自主执行，并在 Gate B 后归档所有有效 terminal milestone，而不再把每个 hash、phase、CI 或 `_SUCCESS` 检查包装成人工 approval。

本阶段只定义规则和迁移边界：

- 不重构或删除 M1–M4 及其他既有 Issue-scoped COS/Gate/approval 入口；
- 不执行实验，不访问 hidden truth，不迁移数据，不写 COS；
- 不改变历史 config、protocol、manifest、artifact、terminal 或 verdict 的 bytes、路径和科学解释；
- 不修改 GitHub ruleset、CI required-check 设置或仓库代码；
- 不把 `@dataclass(frozen=True)` 等实现属性升级为科研治理概念。

## 2. 顶层不变量

1. 结果可见后，不得原地修改已授权的科学契约；需要变化时创建新版本并重新经过 Gate A。
2. 实现可以修 bug；任何新执行必须用新的 commit SHA 建立新的 `Run-pinned` 身份，旧 run 不覆盖、不冒充新 run。
3. Governance v2 中只有 Gate A 和 Gate B 是人工科学判断。Check 失败不产生 approval event。
4. Gate A 之后，合同范围内的实现、重试、批量 run 和证据生成可自主进行；超出合同、权限或成本边界时停止并走相应的新契约或操作授权。
5. 正常 milestone 执行不写 COS。Gate B 后，`ACCEPT`、`REJECT`、`INCONCLUSIVE` 三类有效 terminal milestone 默认且必须归档；invalid/debug/aborted/failed-implementation run 不作为 milestone archive。
6. M0–M4 保持历史 COS；M5+ 在新 milestone 开始时采用 server-first，服务器是 active data 与 run 的事实基准。
7. 历史 terminal、negative result 和 reject/inconclusive 记录不得追溯改成成功结果。
8. Governance v2 不授权自动删除 terminal milestone evidence，也不授权删除既有 COS raw object。

## 3. 术语与生命周期

| 术语 | 保护对象 | 生效与终止 | 允许变更 | 禁止变更 | 版本与迁移规则 |
|---|---|---|---|---|---|
| `Contract-frozen` | research question、dataset/split semantics、candidate/high-value definition、primary metrics、budget/cost model、comparison set、held-out boundary、statistical rule | Gate A `authorized` 时生效；对该 milestone 永久有效 | 结果读取前可修改 draft；授权后可新建 successor contract | 查看结果后原地改字段、阈值、比较集或统计规则 | 每个 milestone 使用一份 `Milestone Contract Manifest`，含稳定 ID、版本、canonical bytes 与 digest；语义变化必须升版并重新 Gate A，旧版保留 |
| `Input-pinned` | 一次执行实际读取的 config、protocol、manifest、data inventory 及其 exact bytes | run preflight 解析并记录 digest 时生效；随 run 永久保留 | 新 run 可选择已授权范围内的新 input revision | 同一 run 中静默替换、仅凭可变路径识别输入 | manifest 记录 URI/路径、size、hash、schema/version；迁移只新增映射，不改旧 digest |
| `Run-pinned` | commit SHA/tree、environment identity、contract/input digests、seed、budget、attempt 与输出关联 | preflight 完成、首个副作用前生效；随 attempt 永久保留 | 可以修 bug、升级依赖或实现优化，但必须形成新 commit/environment 和新 run/attempt 身份 | 用旧 run_id 覆盖新实现，或让不同实现共享不可区分的执行身份 | 新 commit SHA 即新执行身份；旧 run 仍可判为 valid/invalid，但不得被重写 |
| `Archive-immutable` | 已发布到 COS 的 milestone archive object、manifest、review verdict 与校验记录 | archive commit 成功并回读验证后生效 | 可发布新版本、新 prefix 或 superseding manifest | 覆盖、删除后伪装为原对象、修改原 manifest 指向不同 bytes | 新 archive 使用新 ID/prefix；supersession 通过追加关系表达，不原地替换 |
| `Historical-closed` | 已结束阶段的 contract、入口、artifact、terminal、verdict 和当时的 claim boundary | 历史 terminal 或 milestone closeout 时生效 | 可添加不改写原证据的勘误、索引、后续解释 | 追溯授权、重判 STOP/negative terminal、重写历史科学含义 | 勘误和解释作为带作者、时间和理由的新记录；原记录保持可定位 |
| `implementation immutability` | 如 frozen dataclass、只读对象、常量等代码实现性质 | 由代码设计决定 | 按普通代码审查修改或移除 | 无额外科研治理禁令 | 不进入 `Milestone Contract Manifest` 的 scientific freeze 字段，不触发 Gate A/B；若实际改变科学语义，按 `Contract-frozen` 判定而非按语法判定 |

生命周期关系：

```text
Contract draft
  -> Gate A authorized
  -> Contract-frozen
  -> Input-pinned + Run-pinned attempts
  -> Checks pass / evidence sealed
  -> Gate B accept | reject | inconclusive
  -> archive valid terminal milestone
  -> milestone Historical-closed
```

`Input-pinned` 与 `Run-pinned` 是执行身份，不等于禁止修复实现；`Archive-immutable` 是发布存储性质，不等于科学结论永远正确；`Historical-closed` 是禁止追溯改写，不禁止在新 milestone 中提出新问题。

## 4. Gate A — Scientific Contract Authorization

| 项目 | 规范 |
|---|---|
| 触发条件 | 从 Exploration 进入 Formal/Confirmatory，且在首次读取被隔离结果、正式 truth 或 held-out evidence 之前，只触发一次 |
| 必需输入 | research question；dataset 与 split semantics；candidate/high-value definition；primary metrics；budget/cost model；comparison set；held-out boundary；statistical unit、decision rule 与不确定性处理；contract canonical bytes/digest；已知风险与允许偏差 |
| 决策者 | 一位协作者提出正式 contract，另一位协作者明确批准；两人共同制定时记录 `jointly approved`。自动化、CI 或运行程序不得充当决策者 |
| 合法输出 | `authorized`、`revise`、`rejected`；均记录 decision ID、decider、timestamp、contract ID/version/digest、理由 |
| 状态效果 | 仅 `authorized` 产生 `Contract-frozen` 并允许正式执行；`revise` 返回 draft；`rejected` 终止该 proposal，不改写后续新 proposal |
| 证据位置 | `Milestone Contract Manifest`；记录 proposal/approval 角色、两位人员、时间、verdict 与 digest，不得只存在于未经绑定的 approval 字符串 |

Governance 文档只需维护一个简单的静态名单：

```text
Scientific authorities:
- @A
- @B
```

不建立 reviewer registry 服务。上述审查是内部协作审查；即使两人共同制定，也不得声称为 external review 或 independent review。涉及 held-out boundary、primary endpoint、dataset inclusion 或 cost semantics 时，两位 scientific authorities 都必须留下 explicit approval。

### Gate A 后的自主边界

授权后，执行方可以在不改变 contract 的前提下自主完成：实现、测试、preflight、机器检查、合同内的 run matrix、合同允许的 retry、失败记录、证据汇总和修 bug 后的新 run。以下情况必须停止，而不是请求一个临时“Gate”绕过：

- scientific contract 字段需要改变：创建新 contract version，重新 Gate A；
- Check 失败：修复原因后重新运行 Check；不得用人工 approval 把失败改成通过；
- 需要新增 held-out 访问、外部写入、付费、权限扩大或超过已授权 cap：取得独立的 operation authorization；
- 无法在原 contract 内解释的偏差：封存现有 attempt，进入 Gate B 的 deviation evidence，或新建 successor milestone。

Gate A 不逐 run、逐 seed、逐 phase 或逐 COS object 重复触发。

## 5. Gate B — Milestone Evidence Review

| 项目 | 规范 |
|---|---|
| 触发条件 | 计划执行已达到 terminal、证据已 sealed、必需 Checks 已有可核验结果之后；任何 COS milestone archive 之前 |
| 必需输入 | Gate A contract/digest；全部 RunSpec/RunAttempt 与失败/重试；input/source/environment identity；计划与实际 inventory；Check reports；统计结果与不确定性；偏差；claim boundary；复现限制；建议 archive inventory |
| 决策者 | 至少一位名单内的人类 scientific authority；优先由不是最终 evidence summary 主要整理者的人审。若两人都深度参与，记录 `internal non-independent review` 即可 |
| 唯一科学结论 | `accept`、`reject`、`inconclusive`；不得把工程 Checks 的 PASS 当作 `accept` |
| 双人条件 | 普通 terminal 一位 reviewer 足够；若审查涉及 held-out boundary、primary endpoint、dataset inclusion 或 cost semantics，两位 scientific authorities 都必须 explicit approve |
| 归档结果 | `accept`、`reject`、`inconclusive` 都是有效 terminal，默认且必须归档；不另设 archive approval |
| 证据记录 | decision ID、reviewer(s)、review relation、timestamp、contract/run/evidence digests、verdict、理由与 claim boundary |

Gate B 不修改原始 evidence。若材料不完整，应给出 `inconclusive` 或等待材料完备后再正式决策；若已发布 verdict 后出现新材料，创建带新 decision ID 的后续 review，并保留前一记录。Gate B 结束后，milestone archive 进入必需队列并通过归档 Checks；成功归档后 milestone 标记 `Historical-closed`。invalid run、debug run、aborted attempt、failed implementation run 是 run-level 工程记录，不进入 milestone archive，也不得冒充 `REJECT` 或 `INCONCLUSIVE` 科学 terminal。

## 6. Check 统一契约

Check 是对版本化输入执行的 machine-verifiable invariant。每个 Check 必须定义：

- 稳定名称与版本、受检对象和输入 digests；
- 确定性执行方法和环境身份；
- `pass`、`fail`、`error` 三态及 machine-readable reason；
- 证据输出、时间戳、工具 commit/version；
- 失败时阻断的副作用或后续状态；
- 是否可重跑，以及重跑如何保留前次结果。

统一失败语义：`fail` 表示不变量不成立，`error` 表示无法判定；二者都 fail closed，阻止其保护的正式执行、truth access、publication 或 archive。修复后可以创建新 Check result；不得删除失败结果，不得产生 approval event，也不得由人将结果强制改成 PASS。

### 最小 machine-check 清单

| Check 类别 | 最小要求 | 主要阻断点 |
|---|---|---|
| Schema/domain | schema、类型、范围、跨 artifact 引用、candidate universe 与 task binding | contract/input 接纳 |
| Hash/exact bytes | config、protocol、manifest、data、source tree 的 size/hash/canonical bytes | preflight 与读入 |
| Prefix/storage safety | 目标 prefix 合法且为空、禁止 overwrite/delete、对象 cap/byte cap | publication/archive 首个写入 |
| Resource/cost cap | wall time、CPU、RSS、scratch、network、对象/字节、费用及合同允许的 retry | run 启动和持续执行 |
| Environment identity | commit/tree、clean-source policy、依赖与关键线程/硬件环境摘要 | `Run-pinned` 建立 |
| Hidden-label leakage | capability separation、truth-last、held-out boundary、禁止 candidate-level 泄露与违规输出 | truth/held-out access 和报告生成 |
| Inventory/cross-links | planned 与 actual exact cover，RunSpec/Attempt/output/terminal 双向引用，失败记录完整 | evidence sealed |
| Completion marker | `_SUCCESS` 或等价 marker 最后写，且仅在 inventory/hash/readback 全部通过后生成 | run/archive completion |
| CI | format/lint/type/test 及 governance/contract validator；结果绑定当前 commit | PR 工程质量判断 |
| Archive readback | object count、size、hash、manifest、fresh readback 与 immutable write policy | `Archive-immutable` 生效 |

本 RFC 不要求把 governance/contract CI 设为 GitHub required status check；GitHub ruleset 和 required-check enforcement 明确 out of scope。CI 仍是 Check，不因此变成人工 Gate。

## 7. Operation authorization 不是 Gate

外部写入、付费资源、权限扩大、敏感数据、held-out access 或超过既有 cap 时，仍可能需要成员授权。此授权：

- 只批准具体操作、主体、资源、期限、cap 和回滚边界；
- 不批准科学结论，不改变 Contract/Check/Gate B 结果；
- 在 Gate A 已覆盖的 milestone 内应一次性按边界授权，不按 phase 重复制造 approval；
- 边界扩大时重新授权，边界内重试不重新授权；
- 必须在首个不可逆副作用前记录，并由 machine preflight 验证绑定关系。

## 8. COS、服务器数据与 Legacy Execution Surface

### 8.1 目标责任模型

| 平面 | 目标职责 | 明确禁止 |
|---|---|---|
| 服务器 | M5+ 的 raw/processed/embeddings/splits、普通 run 与 release candidate 的权威数据面 | 用未校验的 COS copy 静默替换服务器基准 |
| Git | 代码、schema、contract、config、manifest、小型 summary 与索引 | 大型 raw/result、secret、hidden truth |
| COS | M0–M4 历史资产；Gate B 后发布所有有效 terminal milestone 的 immutable archive | 普通 milestone 的 scratch、增量工作目录、M5+ active raw source of truth、逐 phase evidence write |

每个 COS milestone archive 至少包含：`Milestone Contract Manifest`、run identities、final tables/results、important logs、summary、checksums、provenance、terminal verdict 和最后生成的 completion marker。archive 不必复制所有巨大 raw data，也不包含 invalid/debug/aborted/failed-implementation run 的大体积中间产物；其必要审计引用留在 server manifest。发布必须使用新 prefix、forbid-overwrite、exact inventory 与 fresh readback。

有效 terminal milestone evidence 不自动删除，不预设 1/3/5 年 retention tier。若未来 COS 成本需要生命周期管理，必须另立 storage-lifecycle 决策，不能追溯视为本 RFC 已授权。

服务器最小约定为：

```text
/srv/pcdb/
├── data/
│   ├── raw/
│   ├── processed/
│   ├── embeddings/
│   └── splits/
├── runs/
│   ├── M5B/
│   ├── M5C/
│   └── ...
├── releases/
└── scratch/
```

- Checksum 统一使用 SHA-256 manifest；manifest 自身也记录 canonical SHA-256。
- 写权限由 project Unix group 管理，仅两位研究者和必要 service account 可写；其他主体按最小权限只读或无权限。
- 可重新下载的 public raw 保留 source URL + digest，允许较宽松 RPO；低成本可重算的 processed/embedding 不要求高强度备份。
- 高成本 trajectories 与 release candidate 至少 daily snapshot/rsync，目标 RPO 约 24 小时。
- 启动 milestone 前估算峰值：目标不超过当前可用空间的 50%，70% 为硬上限，并保留明显 headroom；不建设 capacity management service。

### 8.2 历史兼容政策

截至本 RFC 基线已存在的 Issue-scoped COS/Gate/approval 入口统一标记为 `Legacy Execution Surface`，其中 M1–M4 必须完整保留。政策如下：

- 保留原路径、命令、常量、packet、manifest、hash 关系、terminal 和文档定位；用外部索引标注，不修改历史 exact bytes；
- 允许只读审计、验证和明确标注的历史 replay；
- 不作为 Governance v2 新 milestone 的入口，不在新 active runbook 中推荐，不复制其逐 Issue approval 语义；
- 不做统一抽象或批量重命名。必要的安全/兼容补丁必须保持历史科学语义，并以新 commit 与测试记录；
- 历史 replay 若依赖 COS raw，可继续走原只读 capability；这不把 COS 重新定义为新治理的数据基准；
- 无法安全运行时保留为可定位证据并标记限制，不以“修复”为名重写历史结果。

### 8.3 Raw-data 迁移边界

Stage 2 不迁移任何数据，也不做全历史 big-bang cutover。边界为 `M0–M4 → 保持历史 COS`、`M5+ → 新 milestone 开始时 server-first`。M5+ 所需的既有 raw 可以按需 copy 到服务器，治理迁移期允许 COS legacy copy 与 server active copy 暂时并存。

每批 active-data copy 必须满足：

1. 完整 COS source inventory：bucket/key/version、size、hash、manifest/provenance、权限与保留策略；
2. 目标符合 `/srv/pcdb/` namespace、project Unix group ACL、容量规则、备份分层和 SHA-256 manifest；
3. 有可中断重启的 copy 计划、费用/网络/时间 cap，以及逐对象和汇总 reconciliation；
4. 先 copy、checksum verification、影子读取，再在新 milestone 开始时确认 server-first cutover；验证前不得 delete 或改变旧引用；
5. cutover record 明确服务器 URI/digest 为基准、旧 COS identity、未迁移项和 rollback window；
6. server migration、checksum verification、至少一个真实 milestone、restore/reproducibility check 全部完成后，才可另行讨论 keep/cold-storage/delete；本 RFC 不授权删除既有 COS raw object。

不可逆风险包括：对象提前删除、ETag 与 SHA-256 混淆、partial copy 被当成全量、权限/加密元数据丢失、旧 manifest URI 失效、双份对象身份混淆、未预算的流量费用，以及 hidden/sensitive 数据扩大可见范围。M5+ run 只有在所需数据已通过服务器 SHA-256 manifest 验收后才可将其作为 active input；M0–M4 的历史 COS identity 不追溯切换。

## 9. PR、Issue 与 STATUS 决策表

### 9.1 PR 分类

| 判定 | Major / semantic PR | Maintenance batch PR |
|---|---|---|
| 范围 | scientific protocol、dataset/split、metric、cost model、architecture、major runner、storage policy、governance、release | tests、docs、logging、small bugs、minor refactors、plots、CLI polish |
| 优先级规则 | 只要任一变更可能改变科学语义、数据身份、执行边界、存储责任或公开接口，整批按 Major | 只有不改变上述语义且可独立回滚的小变更才可进入 batch |
| 提交时点 | 形成可审阅的最小语义单元后立即 PR，不等待累计 | 下列三个条件中最早发生者：累计 10 个逻辑小变化；当前 Issue/milestone 结束；下一段实验需要其中信息 |
| 审查证据 | contract/ADR 影响、迁移/兼容、科学 claim boundary、测试 | atomic commits、父 Issue comment/checklist、逐项测试与无语义变化声明 |
| 混合变更 | 不与 maintenance 混批；无法拆分时整体按 Major | 发现 semantic impact 时移出并升级为 Major |

“一个小变化”指一个 atomic commit 所承载、可独立描述、验证和回滚的逻辑改动，不按文件数或代码行数计。当前 main ruleset 仍要求所有进入 `main` 的改动通过 PR；Maintenance batch 是聚合策略，不是绕过 PR 的 fast path。

### 9.2 独立 GitHub Issue 门槛

满足任一条件时建立独立 GitHub Issue：

- 属于 Major / semantic PR；
- 有独立用户/科研价值、独立验收标准或独立风险/授权边界；
- 跨 milestone、需要多个 PR/维护批次、多个责任人或外部协调；
- 涉及数据迁移、held-out、权限、付费、隐私、安全、release 或 GitHub/COS 外部配置；
- 没有合适的现有父 Issue 可以承载范围、证据和验收。

纯 maintenance 小变化若已有合法父 Issue，则用 atomic commit 加父 Issue comment/checklist 记录，不单独创建 GitHub Issue；达到自然 review point 或 PR 触发条件时由一个 maintenance PR 汇总。Git 本身和父 Issue 是 ledger，不创建 `maintenance-batch-ledger.yaml` 或其他第三套状态文件。

Multica issue 与 GitHub Issue 不要求一一对应，允许多个 Multica issues 对应一个 GitHub Issue。需要 cross-link 时只用以下约定，不建立 `MulticaGithubLinkRegistry`：

- Multica title 使用 `[GH#97] M5B truth subset fixes`；
- Multica description 写 `Source of truth: https://github.com/.../issues/97`；
- GitHub Issue 如需反向链接，写 `Execution tracked in Multica: <issue URL>`。

Multica issue 负责执行归属与阶段状态；达到独立 Issue 门槛时，GitHub Issue 才作为仓库侧长期 source of truth。

### 9.3 STATUS 更新

| 事件 | 是否更新 `docs/STATUS.md` | 规则 |
|---|---|---|
| Gate A 使 Exploration → Formal/Confirmatory | 是 | 记录 contract ID/version/digest、授权状态和 held-out boundary，不记录运行中噪声 |
| Gate B 产生 accept/reject/inconclusive | 是 | 若同时 closeout，只写一次；记录 verdict、claim boundary、evidence/manifest identity 与 archive 状态 |
| Milestone closeout | 是 | 记录最终 scientific state、terminal、基线 commit、archive/server manifest；必须由可验证记录生成或核对 |
| 普通 PR merge、CI PASS、单次 run、retry、日志、plot、维护批次 | 否 | 留在 PR/Issue/run evidence，不刷新 STATUS |
| 工程实现推进但 scientific state 未变化 | 否 | 不把工程活动冒充科学进展 |

STATUS 是低频科学状态快照，不是任务看板。GitHub/CI 提供工程事实，contract/manifest/Gate record 提供科学事实；生成器不得只根据 open/merged PR 推导 scientific state。

## 10. Stage 3 实现顺序

1. 以新增 RFC、词汇表和历史入口索引落地术语；不得先批量重命名旧 `freeze`/`gate` 文件。
2. 建立每个 milestone 的 `Milestone Contract Manifest` 和 Gate A/B decision schema；不建设中心 contract/reviewer registry，不回写历史 packet。
3. 为现有入口逐项分类为 scientific decision、operation authorization、machine Check 或 historical evidence；M1–M4 和所有既有 Issue-scoped entry point 保持 legacy 路径。
4. 建立通用 Check result schema/runner，组合现有 schema、hash、env、leakage、inventory、cap、CI 与 completion checks；保留旧 verifier 作为兼容层。
5. 落地 `/srv/pcdb/` namespace、project Unix group ACL、SHA-256 manifest、分层 backup/RPO 和 50% target/70% hard-cap 容量 preflight；不建设额外管理服务。
6. 实现 M5+ 普通 v2 run：server-first 输入、服务器 sealed working artifacts、零 COS write，并生成 `Input-pinned`/`Run-pinned` identity；M0–M4 保持历史 COS。
7. 单独实现 milestone archiver：Gate B 后自动接收所有有效 terminal milestone，使用新 prefix、不可覆盖、精简 bundle、exact inventory、fresh readback 和 completion marker。
8. 更新 active governance/closeout 文档与 STATUS 生成流程；历史文档只通过外部索引标注，不创建 cross-link registry 或 maintenance ledger 文件。
9. governance/contract CI required enforcement、GitHub ruleset、COS storage lifecycle 与删除操作均 out of scope；按需 raw copy 也不得追溯改写 M0–M4 identity。

若任何一步要求改变历史 hash、路径、terminal、STOP/negative verdict 或已发布 artifact，应停止并回到 RFC 审查，而不是实施兼容性破坏。

## 11. 例外与兼容性风险

- 紧急安全修复可以限制或隔离 legacy 执行能力，但不得删除历史证据；恢复执行需新 commit 和兼容验证。
- 只读历史 replay 不等于新科学执行；结果必须标记原 contract/run identity 和复现限制。
- Check 工具本身的 bug 需要新版本和新 Check result；旧结果保留，并在 Gate B 中披露影响。
- `Archive-immutable` 不禁止追加勘误或 superseding archive，但两者必须新 ID、新 prefix、显式关联。
- reject/inconclusive 不是失败日志：它们是合法科学 terminal，必须归档且不得被成功结果覆盖。
- 当前 GitHub 未硬性要求 CI status check；本 RFC 明确不改变这一设置，文档不得声称它已由 ruleset 硬阻断。
- 当前 `docs/STATUS.md` 已有漂移；在生成/核对机制完成前，人工更新必须同时核对 scientific record 与 GitHub commit，而非复制旧快照。

## 12. 审查定稿结论

本轮审查已关闭上一版的 7 个未决治理问题：reviewer 使用治理文档中的两人静态名单；contract 载体为 `Milestone Contract Manifest`；所有有效 terminal 默认归档且不自动删除；服务器 namespace/ACL/checksum/backup/capacity/cutover 已采用最小方案；COS raw 删除未获授权；governance/contract CI required enforcement out of scope；Multica/GitHub 用标题与描述互链，Git 和父 Issue checklist 充当 maintenance ledger。

Stage 3 开始时只需把两位 scientific authorities 的实际 handle、project Unix group 名和必要 service account 填入配置/治理文档。这些是部署值，不是未决科学语义；不需要再进行一轮 Stage 2 概念审查。

## 13. RFC 通过判据

- 六类术语均有对象、生命周期、允许/禁止变更和升版/迁移规则；
- `Run-pinned` 明确允许修 bug，并以新 commit SHA 建立新执行身份；
- 只有 Gate A/B 产生人工科学决定，所有列明 Checks 均不产生 approval event；
- Gate A 后 milestone 可在 contract 与 operation authorization 范围内自主执行；
- Gate B 对所有有效 `ACCEPT`/`REJECT`/`INCONCLUSIVE` terminal 触发精简 COS archive，invalid/debug/aborted/failed-implementation run 不归档；
- M0–M4 历史 COS、M5+ server-first、`/srv/pcdb/`、SHA-256 manifest、ACL、backup/RPO、capacity、cutover 与不可逆风险均明确，Stage 2 无数据迁移；
- 既有 Issue-scoped 入口，尤其 M1–M4，完整保留为 `Legacy Execution Surface`，不作为新流程入口；
- PR、独立 Issue 和 STATUS 的判定可由不同维护者得到一致结果；
- Stage 3 顺序先增量定义和验证、后切换 active path，且不改变历史科学含义。
