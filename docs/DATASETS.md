# 数据集登记

FLIP2 v3 Alpha Amylase、Imine Reductase、Nuclease B 与 Hydrophobic Core 已分别完成官方
raw source 下载、checksum 冻结和 COS 回读验证。Alpha Amylase 与 Imine Reductase 已分别在
reviewed commit 上完成正式 immutable processing publication、权限恢复和 fresh readback。Alpha
Amylase 还已在 reviewed exact commit 上完成正式 topology-isolation split publication、fresh
readback 和不可覆盖验证。Imine Reductase 的 Issue #22 split 已有 implementation、synthetic
fixture 与 CI evidence，并在 Draft PR #23 接受审查；尚无真实 observable-input preflight、
server execution、COS publication 或 fresh readback。Nuclease B 的 Issue #25 已完成正式
topology-extrapolation split publication、fresh readback 和独立确定性重建验证；Hydrophobic Core
也已完成非 primary、非 M2 必需的 within-WT topology stress tracer 正式 split publication、
fresh readback 和独立确定性重建验证。四个 task 均未进入 replay。其他候选数据源包括
ProteinGym 和 SKEMPI 2.0；每个具体 task/assay 必须通过 dataset onboarding Issue 后才能进入
开发或主实验。

## 进行中的 onboarding

- FLIP2 v3 Alpha Amylase：GitHub Issue #3，raw ingest runtime 与 DOI manifest revision 已完成，
  machine-readable `DatasetIngestAttempt` 已回读验证；官方 source inventory、下载校验与 COS
  对象布局见
  [FLIP2 Amylase ingest](data/FLIP2_AMYLASE_INGEST.md)。Issue #15 负责 candidate identity、
  hidden-label separation、duplicate/near-duplicate audit、Processing Manifest 与正式运行证据，见
  [FLIP2 Amylase processing](data/FLIP2_AMYLASE_PROCESSING.md)。正式 processing revision
  `flip2-amylase-4e6de256fa84b6c237828e58b244b75b6da304be1a496444dc2896241f1fc12b`
  已发布四个 processed payload；Processing Manifest SHA-256 为
  `cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec`。首次 publication
  因 payload PUT 权限返回 403 而不完整；恢复操作只补齐四个缺失 payload，禁止覆盖且未改写已发布
  manifest，随后完成 fresh readback。该证据只建立 processing server-execution，不建立 split、
  replay、benchmark 或 scientific gate。
- FLIP2 v3 Imine Reductase：GitHub Issue #8，3/3 source objects、Dataset Manifest 和
  machine-readable `DatasetIngestAttempt` 均已完成 COS exact-byte/readback 验证及不可覆盖
  检查，见 [FLIP2 IRED ingest](data/FLIP2_IRED_INGEST.md)；PR #9 已合并。Issue #16 processing
  implementation 复用共享 processing core，冻结 8,586 个 canonical candidates、hidden-label
  separation、source relationship、WT/mutation 与 duplicate/near-duplicate audit，见
  [FLIP2 IRED processing](data/FLIP2_IRED_PROCESSING.md)。Reviewed commit
  `703247cb001e063c78bd8d34d66b094a9514dc3a` 的正式 processing revision
  `flip2-ired-8f32dfa11a52d3ce96da4eb4ebb305210ccf027cf812d277f2c9c25f254c4f5d`
  已发布四个 processed payload；Processing Manifest SHA-256 为
  `929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba`。4+1 对象均通过
  fresh checksum/size/row-count/Schema/domain/exact-byte readback，重复 manifest PUT 以
  `409 FileAlreadyExists` 失败且对象未变。该证据只建立 processing server-execution，不建立 split、
  replay、benchmark 或 scientific gate。Issue #22 已冻结
  `flip2-ired-topology-extrapolation-v1`：唯一 4,448-candidate Hamming-1 component 为
  development，4,138 个 singleton 按 mutation count 和全局整数 deficit schedule 分配为
  candidate-pool/validation/held-out = 2,070/1,034/1,034，见
  [FLIP2 IRED split](data/FLIP2_IRED_SPLIT.md)。development 占 4,448/8,586（51.8%），candidate
  pool 占 2,070/8,586（24.1%），是必须持久报告的 topology/distribution-shift 限制。当前已有
  implementation、synthetic fixture 与 CI evidence；尚无真实 observable-input preflight、server
  execution、COS publication 或 fresh readback。
- FLIP2 v3 Nuclease B：GitHub Issue #10，3/3 source objects、独立 Dataset Manifest 与
  `DatasetIngestAttempt` 已完成 COS exact-byte/readback 和不可覆盖验证，见
  [FLIP2 NucB ingest](data/FLIP2_NUCB_INGEST.md)。Issue #24 已冻结 55,759 个 target-free
  candidates 与 audit-only upstream metadata；Issue #25 已冻结
  `flip2-nucb-topology-extrapolation-v1`：全部 3,350 个 non-singleton components / 32,776
  candidates 为 development，22,983 个 singleton 按 mutation count 和全局 integer-deficit
  schedule 分配为 candidate-pool/validation/held-out = 11,493/5,745/5,745，见
  [FLIP2 NucB split](data/FLIP2_NUCB_SPLIT.md)。Reviewed generation commit
  `de2280adfd522c6917329070565f742c8e6553cc` 的正式 split revision 已按 payload-first /
  manifest-last 与禁止覆盖约束发布 2 个 payload 和 1 个 Split Manifest，并通过 fresh
  exact-byte/checksum/size/row-count/Schema/domain readback。独立 reviewer 随后从 COS fresh
  读取 accepted observable chain，重建结果与三个正式对象 3/3 exact-byte identical。该证据只建立
  split server-execution，不授权 held-out、hidden truth、replay、benchmark evidence 或 scientific
  validation。
- FLIP2 v3 Hydrophobic Core：GitHub Issue #10，7/7 source objects、独立 Dataset Manifest 与
  `DatasetIngestAttempt` 已完成 COS exact-byte/readback 和不可覆盖验证，见
  [FLIP2 Hydro ingest](data/FLIP2_HYDRO_INGEST.md)。Issue #26 已完成 fresh read-only
  source/semantics audit，并 prospectively 冻结 24,935-candidate multi-WT processing contract：
  P06241/P01053/P0A9X9 分别为 9,972/6,463/8,500 candidates，observable candidates 显式包含
  `wild_type_id`，target 保持独立 hidden truth，88,664 个 within-WT Hamming-1 pairs 延后由
  Split Manifest 处理，见 [FLIP2 Hydro processing](data/FLIP2_HYDRO_PROCESSING.md)。Issue #27
  已在 merged commit `cda72d5ac96268cbb9a41432d73adafe628e7761` 上发布
  `flip2-hydro-within-wt-topology-stress-tracer-v1`；protocol hash 为
  `6ef77e40e56d079959430d086eddde4b74360ca7c4cddd6af33bbc0cf52bacbe`。正式 inventory 为
  2 个 payload 和 1 个 Split Manifest，membership 按
  development/candidate-pool/validation/held-out = 23,775/580/290/290 分配，1,346 个
  within-WT components 均不跨 role。非发布方随后 fresh 读取 accepted observable chain，
  从 exact merged commit 重建并确认正式 bundle 3/3 exact-byte identical，见
  [FLIP2 Hydro split](data/FLIP2_HYDRO_SPLIT.md)。该 tracer 是非 primary、非 M2 entry
  requirement；其 server-execution evidence 不授权 hidden truth、held-out/WT-holdout、
  feature/model/replay、benchmark conclusion 或 scientific gate。
- Alpha Amylase 与 IRED processing implementation、正式 immutable publication/recovery 与 fresh
  readback 已完成。Issue #19 冻结的 primary topology-isolation split 已在 reviewed commit
  `b540bfb3ed65066cb74418d5a4336452bf837af1` 上正式发布；protocol hash 为
  `244111b3eb4507563602851dfa730e7f5f297eea27447a0e22b59d1dc40fb685`。3,706-candidate
  membership 按 2,262/722/361/361 精确分配且 cross-role Hamming-1 edge 为 0，2 个 split payload
  与 1 个 Split Manifest 均通过 fresh exact-byte/readback 和不可覆盖验证，见
  [FLIP2 Amylase split](data/FLIP2_AMYLASE_SPLIT.md)。尚无 held-out 授权、replay 或 scientific
  validation。

## Onboarding checklist

- [ ] 来源页面和下载 URL 已确认；
- [ ] task-level license、record-level provenance 与再分发限制已记录；
- [ ] Dataset Manifest 已记录 DOI、下载时间、原始文件 SHA-256 和已知限制；
- [ ] Dataset ingest attempt 已绑定 Git commit、环境、命令、COS 回读与不可覆盖证据；
- [ ] label direction 和 missing-value 规则已确认；
- [ ] candidate ID 唯一；
- [ ] Processing Manifest 已绑定处理代码 commit、配置 hash、字段可见性和处理输出；
- [ ] duplicate/near-duplicate audit 与处理规则已写入 Processing Manifest；
- [ ] development/validation/held-out 边界与 leakage control 已写入 Split Manifest；
- [ ] preprocessing 只在允许的数据范围内拟合；
- [ ] 完整 manifest chain 通过 Schema 与领域语义校验；
- [ ] 数据只写入批准的 COS prefix。

不得仅因为数据公开就假设其可以重新分发。若许可证不允许，应只发布下载与处理脚本、manifest
和合法的索引信息。
