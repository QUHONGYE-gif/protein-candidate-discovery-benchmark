# Alpha＋IRED M1 readiness ledger

> **历史快照与 successor：** 本 ledger 冻结的是
> `origin/main@8db51b8bc5d7c97a7753bcc462790ac947c8611f` 时点的 Gate 1 输入，以下“未判定/未完成”
> 文字不得解释为 2026-08-10 的当前状态。后续 Issue #42 已发布
> `GATE 1 PASS — ALPHA + IRED INITIAL M2 ENTRY COHORT`；Issue #58 已完成冻结
> `2 tasks × 3 models × seed 1729` 的正式 M2 static-baseline execution、68-object publication、
> 独立 readback 与 final evidence review，并按 completed 关闭。整个 M1 portfolio、M3/Gate 2、
> held-out、external validation 与最终 scientific validation 仍未完成或授权。当前阶段状态见
> [Execution Roadmap](../EXECUTION_ROADMAP.md) 与
> [M2 Gate B review packet](../M2_GATE_B_REVIEW_PACKET.md)。

## 结论与判定边界

截至 `origin/main@8db51b8bc5d7c97a7753bcc462790ac947c8611f`，Alpha Amylase 与 IRED
均已形成可供独立 Gate 1 审核的工程输入：冻结的 Dataset、Processing 与 Split Manifest
identity，正式 immutable publication/readback 记录，以及可审计的数据语义和 leakage boundary。

这只表示 **Alpha＋IRED ready for Gate 1 review**。它不表示 Gate 1 已通过，不表示整个 M1
dataset portfolio 已完成，也不表示项目已进入 M2。Gate 1 verdict 只由
[Issue #42](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/42)
给出；未经授权的 held-out 与真实 candidate-pool target 继续关闭。

本文只对账已有 Git/GitHub 与历史 COS evidence，没有执行新的数据下载、COS 操作、credential
probe、truth/held-out 读取、feature/model/replay 或正式 M2 run。历史 COS 成功不证明当前
credentials 已配置、仍有效或已获新操作授权；当前 credential availability 在本次文档 slice 中
**未核验**。

## 精确 manifest chain

| Task | Dataset Manifest SHA-256 | Processing Manifest SHA-256 | Split Manifest SHA-256 | Candidate pool |
|---|---|---|---|---:|
| Alpha Amylase | `c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac` | `cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec` | `c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a` | 2,262 |
| IRED | `37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643` | `929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba` | `9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e` | 2,070 |

这些 identity 必须作为完整 checksum-linked chain 使用，不能用 Issue 状态、checklist 数或 CI
green 替代。

## Alpha Amylase 对账

| Gate 1 输入 | 冻结事实与边界 |
|---|---|
| Source / license | FLIP2 v3 Zenodo record `18433203`；task-level raw files 为 MIT，record metadata 为 CC-BY-4.0。外部 release 必须同时保留 DOI、task README、record attribution 与两层 notice。 |
| Label direction | `target` 是三次去污实验平均 released-soil absorbance 减 blank；冻结为 `higher_is_better`，不得解释成浓度归一化酶活。 |
| Candidate identity | 3,706 个唯一 length-425 canonical sequences；`candidate_id` 为 namespace 与完整 sequence 的 SHA-256，独立于 source filename 和 row order。 |
| Duplicate policy | 单文件 exact duplicate fail closed；跨五个文件仅在 target 一致时聚合。4,311 个 Hamming-1 pairs 在 processing 保留、在 split 按 connected component 分组。 |
| Observable / hidden | observable candidates 与 hidden truth 为独立 artifact；`target` 不进入 candidate table，publisher split 字段仅进入 audit metadata。split CLI 无 hidden-truth 输入。 |
| Split / leakage | 943 个 Hamming-1 components 不跨 role，cross-role edge 为 0；角色计数为 candidate-pool/development/validation/held-out = 2,262/722/361/361。 |
| Known limitations | 最大 component 的 2,262 candidates 全部进入 candidate pool，占完整 universe 61.04%，形成显著 topology/distribution shift。held-out 仅有 identity，未授权读取。 |

证据链：

- [raw ingest 与 license 说明](FLIP2_AMYLASE_INGEST.md)；
- [processing contract、publication recovery 与证据角色边界](FLIP2_AMYLASE_PROCESSING.md)；
- [split contract 与正式 artifact identity](FLIP2_AMYLASE_SPLIT.md)；
- [Issue #19 正式 publication 记录](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/19#issuecomment-5058908448)
  与 [lifecycle completion](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/19#issuecomment-5059118723)。

Alpha processing 的 evidence 角色必须保持区分：recovery operator 对 4 个 payload 与 Processing
Manifest 完成 fresh exact-byte readback；最终 reviewer 独立确认远端 inventory、大小并下载验证
Processing Manifest，但其身份对 4 个 payload 的 HEAD/Get 仍为 403。因此不能写成 reviewer
独立下载并验证了全部 processing payload。

## IRED 对账

| Gate 1 输入 | 冻结事实与边界 |
|---|---|
| Source / license | FLIP2 v3 Zenodo record `18433203` 与上游 SrIRED Zenodo `11357236`；CC-BY-4.0，并保留 task README、上游论文和 first-hand provenance attribution。 |
| Label direction | `target` 是相对 WT 的无量纲 log-enrichment fitness，冻结为 `higher_is_better`；第一方 metadata 未说明 logarithm base。 |
| Candidate identity | 8,586 个唯一 length-290 canonical sequences；`candidate_id` 为 dataset/task namespace 与完整 sequence 的 SHA-256，独立于 row order 和 source enumeration。 |
| Duplicate policy | 单文件 duplicate fail closed；两个 upstream split 文件仅在 target 一致时聚合。9,279 个 Hamming-1 pairs 在 split 形成 4,139 个 components。 |
| Observable / hidden | `target` 只进入 hidden-truth artifact；publisher split 字段仅作 audit metadata。正式 split 构建与独立 review 均未读取 `hidden-truth.csv`。 |
| Split / leakage | 4,448-candidate giant component 全部为 development；4,138 个 singleton 分配为 candidate-pool/validation/held-out = 2,070/1,034/1,034；cross-role edge 为 0。 |
| Known limitations | development 占 universe 51.8%，candidate pool 占 24.1%，是显著 topology/distribution shift；上游 16,278-row files 与论文 17,143 variants 相差 865 且未获第一方解释。held-out 未授权。 |

证据链：

- [raw ingest 与 license 说明](FLIP2_IRED_INGEST.md)；
- [processing contract 与正式 publication/readback](FLIP2_IRED_PROCESSING.md)；
- [split contract 与正式 artifact identity](FLIP2_IRED_SPLIT.md)；
- [Issue #22 正式 publication 记录](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/22#issuecomment-5077267677)
  与 [independent fresh-readback PASS](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/22#issuecomment-5078199435)。

IRED split 的 duplicate probe 只证明 overwrite-forbidden request 非零失败，且随后独立 inventory
仍为 `2 + 1`、三个对象 bytes/SHA-256 均未变化。该次 COSCLI invocation 没有暴露可解析的
HTTP `409` / `FileAlreadyExists`，因此不得声称观察到了精确 409。

## Experiment Config v2 与 M2 边界

[PR #39](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/39) 已以 merge
commit `8db51b8bc5d7c97a7753bcc462790ac947c8611f` 将 production Experiment Config v2、metric/result
aggregate contracts 和 synthetic evaluator conformance 进入 `main`。它把以上两个 manifest chain
与 candidate-pool universe 绑定到生产契约，但 synthetic fixtures 不读取真实 Alpha/IRED target。

尚未完成或授权的 M2 工作包括 feature artifact Schema 与 canonical feature hash、prediction artifact
contract、feature builder、surrogate baseline、真实 prediction、正式 evaluator input 以及任何正式
M2 execution。契约完成不能替代 Gate 1 verdict 或模型运行证据。

## Portfolio 与 lifecycle bookkeeping

- ProteinGym [Issue #11](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11)
  继续保持 archive-level retention/license `NO-GO`；是否阻塞首批 M2 cohort 只由 #42 判定。
- SKEMPI 2.0 raw ingest 已完成并合并；processing、mutation/chain normalization、structure mapping、
  split 与 external-validation use 均未授权。
- NucB 已有 processing/split engineering 与历史 publication evidence；Hydrophobic Core 已有
  processing 与非 primary、非 M2 entry requirement 的 topology stress tracer。它们不使整个 M1
  portfolio 自动完成。
- 已完成的 Issue #12、#24、#25、#26 仍带 `ready-for-human`，属于已知的 remote lifecycle-label
  drift。本 slice 不修改这些 labels；在 #7 reconciliation 与 #41 merge 后 closure ledger 中逐项
  记录，后续另行授权清理。
- PR #17 保持 open Draft、exact head `76c95d0ab5844c3489c981e492bd0dccd421793e`，且 licensing
  review thread 仍未解决。只有 replacement documentation reviewed/merged 后才能留下 superseded
  说明并关闭 Draft；不得合入其 commit 或删除 branch。

## 历史状态矩阵（截至 `origin/main@8db51b8`）

| 状态 | Alpha＋IRED | 项目整体 |
|---|---|---|
| Engineering inputs for Gate 1 review | 已具备 | 仅限首批候选 cohort |
| Gate 1 verdict | 未判定，等待 #42 | 未判定 |
| M1 portfolio onboarding | 两项主候选 task 已对账 | 未完成 |
| M2 contracts | PR #39 production contracts 已进入 `main` | feature/prediction/model/run 未完成 |
| Scientific validation | 未开始 | 未开始 |

## 当时的 repository-wide stale-status reconciliation

本 slice 对 `尚无`、`尚未`、`仍需`、`等待`、`未开始`、`未完成`、`未配置`、Draft PR、Gate/M1/M2
与 HTTP 409 等状态措辞执行了全仓搜索。处理结果如下：

- 五份 required current-state documents 中的陈旧断言已修正：IRED split 已从本地 implementation
  推进到正式 publication/readback；SKEMPI raw ingest PR 已合并；README credential 状态改为本
  slice 未核验；roadmap 与 dataset registry 已对齐 PR #39 和当前 Gate 边界。
- Alpha/IRED raw-ingest 与 processing 文档中的旧阶段措辞改为“该层 evidence 不建立后续状态”，并
  显式链接后续已完成的 processing/split 记录；NucB/Hydro ingest 文档同样避免把 raw 层边界写成
  当前 portfolio 状态。
- Issue #28 proposal 目录继续是 non-production historical evidence，但已补充 PR #39 production
  successor，避免把当时未决定的 canonicalization 误报成当前决策。
- 剩余“尚未/未完成”命中均是仍准确的 scope boundary：Gate 1 未判定、M1 portfolio 未完成、
  feature/model/replay/M2 execution 与 scientific validation 未完成、held-out 未授权。
- 其他文档中的明确 `409 FileAlreadyExists` 记录属于各自 ingest/processing/split 已实际捕获的历史
  证据；只有 IRED split duplicate probe 保留“未捕获可解析 409”的窄限制，二者不冲突。
