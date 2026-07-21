# ProteinGym v1.3 四 assay transfer preflight

## 当前结论

截至 2026-07-21，本 preflight 的结论为 **NO-GO / blocked**。本次只完成官方 release、四个
assay 的 provenance/license matrix，以及本机中转、网络和 COS 容量门禁；没有下载
`DMS_ProteinGym_substitutions.zip` 或 `zero_shot_substitutions_scores.zip`，也没有创建任何
ProteinGym COS object。

阻断原因不是空间不足，而是 `SPG1_STRSG_Olson_2014` 尚无可确认的独立 dataset license。
ProteinGym v1.3 的 Zenodo Dataset record 声明 MIT，但该汇总许可不能被假定为取代上游
assay 数据条款。除非获得上游许可证据或由 maintainer 明确修改四-assay pilot 范围，否则不得
启动约 1.95 GB 的 source-bundle transfer。

本文件是工程与治理 preflight，不构成法律意见，也不授权 processing、split、held-out、
multi-fidelity protocol 或 scientific use。

## 冻结的 release 与 HTTP metadata

- ProteinGym release：v1.3；Zenodo DOI
  [`10.5281/zenodo.15293562`](https://doi.org/10.5281/zenodo.15293562)，published
  2025-04-27；Zenodo resource type 为 Dataset，rights metadata 为 MIT。
- 官方 GitHub repository：[`OATML-Markslab/ProteinGym`](https://github.com/OATML-Markslab/ProteinGym)；
  本次审计的 `main` commit 为
  [`144fe22b07dfaeec2b366f2346203a9838a55b4c`](https://github.com/OATML-Markslab/ProteinGym/commit/144fe22b07dfaeec2b366f2346203a9838a55b4c)。
  该 commit 的 README 明确 v1.3 为最新 release，并列出下表中的两个 archive。
- HTTP metadata 观测时间：2026-07-21；只执行 `HEAD`，没有下载 response body。

| Archive | Canonical URL | Content-Length | Last-Modified | ETag | Zenodo MD5 |
|---|---|---:|---|---|---|
| DMS truth | `https://marks.hms.harvard.edu/proteingym/ProteinGym_v1.3/DMS_ProteinGym_substitutions.zip` | 43,021,128 B | 2025-04-13 02:47:58 GMT | `2907348-6329ff6970941` | `ca1a4d46941ef33cc972245347118c7d` |
| zero-shot scores | `https://marks.hms.harvard.edu/proteingym/ProteinGym_v1.3/zero_shot_substitutions_scores.zip` | 1,911,045,703 B | 2025-04-28 02:26:40 GMT | `71e83e47-633cd6a03f0f6` | `7912b6a1f6c80b0b78c12a15c84d96fc` |

压缩总量为 `1,954,066,831 B`（约 `1.82 GiB`）。ETag 只作为 HTTP validator；Zenodo MD5
用于 publisher checksum 对照。正式 ingest 仍须计算 SHA-256，并把 exact bytes、source
metadata 与 SHA-256 绑定到 Dataset Manifest。

若正式 transfer 前任一 Content-Length、Last-Modified、ETag 或 Zenodo MD5 变化，必须停止，
重新审计 release identity，不得沿用本 preflight 自动继续。

## 四-assay provenance、语义与许可矩阵

ProteinGym 官方 `reference_files/DMS_substitutions.csv` 中四条记录均来自上述冻结 commit。
`raw_DMS_directionality=1` 表示 ProteinGym reference 中 higher raw score is better；这只是待后续
processing 验证的 source metadata，不是本 Issue 对 label direction 或 high-value threshold 的
科学冻结。

| Assay | 官方 source facts | 上游许可证据 | Private COS retention | Public redistribution | Preflight decision |
|---|---|---|---|---|---|
| `BLAT_ECOLX_Firnberg_2014` | `BLAT_ECOLX_Firnberg_2014.csv`；4,783 rows；growth under ampicillin；raw phenotype `linear`；DOI [`10.1093/molbev/msu081`](https://doi.org/10.1093/molbev/msu081) | 原论文及 online supplementary material 声明 CC BY-NC 3.0；须引用 Firnberg, Labonte, Gray and Ostermeier (2014)，仅限 non-commercial reuse | **Conditional**：仅非商业研究、保留 attribution，且先证明 ProteinGym member 与受许可 supplementary data 的 provenance 对应 | **Blocked pending mapping**：只有确认对应关系并满足 BY-NC 后才可考虑；不得用于商业再分发 | **Not approved**，等待 maintainer 确认用途与 provenance mapping |
| `CAPSD_AAV2S_Sinai_2021` | `CAPSD_AAV2S_Sinai_2021.csv`；42,328 rows；AAV capsid production viability；raw phenotype `viral_selection`；DOI [`10.1101/2021.04.16.440236`](https://doi.org/10.1101/2021.04.16.440236) | bioRxiv preprint 为 CC BY 4.0；作者的 [`churchlab/Generative_AAV_design`](https://github.com/churchlab/Generative_AAV_design) 明示 “Code and Data” 且 repository license 为 MIT；须引用 Sinai et al. (2021) 并保留相应 notices | **Conditional**：确认 ProteinGym member 追溯到 MIT repository data 后可保存在 private COS | **Conditional**：确认对应关系后，按 MIT/CC BY notices 与 attribution 执行；不得把 patent statement解释为数据许可 | **Not approved**，等待 provenance mapping 与 maintainer 确认 |
| `GFP_AEQVI_Sarkisyan_2016` | `GFP_AEQVI_Sarkisyan_2016.csv`；51,714 rows；fluorescence/FACS；raw phenotype `mean_medianBrightness_per_aaseq`；DOI [`10.1038/nature17995`](https://doi.org/10.1038/nature17995) | 作者发布的 Figshare Dataset [`10.6084/m9.figshare.3102154`](https://doi.org/10.6084/m9.figshare.3102154) 为 CC BY 4.0；须引用 Sarkisyan et al. (2016) 与 Figshare dataset | **Conditional**：确认 ProteinGym member 与 Figshare data 的 provenance 对应后允许 | **Conditional**：确认对应关系并保留 CC BY attribution 后允许 | **Not approved**，等待 provenance mapping 与 maintainer 确认 |
| `SPG1_STRSG_Olson_2014` | `SPG1_STRSG_Olson_2014.csv`；536,962 rows；IgG binding；raw phenotype `lnW`；DOI [`10.1016/j.cub.2014.09.072`](https://doi.org/10.1016/j.cub.2014.09.072) | 可核验的 [author manuscript](https://pmc.ncbi.nlm.nih.gov/articles/PMC4254498/) 标注 “© 2014 Elsevier Ltd. All rights reserved”；未找到该 assay 数据的独立开放 license | **Blocked**：公开可读或被 ProteinGym 收录不等于已确认 private-copy/derivative-data 权限 | **Blocked** | **Blocked**；这是当前四-assay transfer 的决定性 blocker |

矩阵中的 `Conditional` 不等于 `approved`。ProteinGym Zenodo Dataset 的 MIT rights 是重要的
archive-level 证据，但在本项目的 fail-closed policy 下，只有 assay-level provenance 与上游条款
相容且 maintainer 明确确认后，才可把对应行改为 `approved`。

由于两个 archive 是全量 bundle，不能在 transfer 之前从 HTTP response 中排除 SPG1。若要绕过
该 blocker，必须先由 maintainer 在 Issue #11 中明确变更 scope（例如改为可独立从已许可上游
获取的三-assay source），并重新冻结 source inventory；不得把这种 scope change 作为本文件的
隐含授权。

## 可审批的传输预算

本次只读审计显示，本机 staging volume 当前可用 `42,958,592 KiB`（约 `40.97 GiB`），
`cos://data/raw/` 当前为 21 objects / 34.51 MB。容量值会变化，因此正式运行前必须再次检查。

| Gate | 冻结值 | Stop condition |
|---|---:|---|
| transfer 开始前本机最低可用空间 | `12 GiB` | 任一步开始前低于 12 GiB，停止 |
| official compressed archives | `1,954,066,831 B` | exact size 不符或 archive integrity 失败，停止 |
| selective extraction scratch hard cap | `4 GiB` | ZIP inventory 预测的选定 member 总量超过 4 GiB，先重审 scope，不解压 |
| 正常网络 traffic budget | `5,862,200,493 B`（约 `5.46 GiB`） | upstream download + COS upload + fresh COS readback 超出预算，停止 |
| 含一次完整 upstream retry 的 hard cap | `7,816,267,324 B`（约 `7.28 GiB`） | 需要第二次完整重新下载时停止并人工复核 |
| COS raw prefix 新增容量 hard cap | `2.05 GB` decimal | 两个 raw archive 加 manifest/evidence 超过 2.05 GB，停止；extracted members 不得写入 raw prefix |

执行策略：

1. 两个压缩 archive 在 COS fresh readback 和 SHA-256 验证完成前同时保留在 local staging；
   不进行 full unzip。
2. 先读取 ZIP central directory，验证四个 DMS member 和对应 zero-shot member 的唯一精确路径，
   再选择性解压到独立 scratch。derived files 不进入 archive-level raw prefix。
3. HTTP resume 仅在稳定 ETag 或 Last-Modified validator 存在、`If-Range` 生效且
   `Content-Range` 与 local partial size 连续时允许；否则丢弃无效 partial 并计入一次完整 retry。
4. COS publication 使用 immutable key 与 overwrite forbidden；`coscli` 使用
   `--err-retry-num 0`，由 ingest layer 控制唯一一次 bounded retry。回读必须使用新的 temporary
   directory，不能对上传源文件原地校验。
5. 失败后先保留日志与对象清单。只清理未完成的 local partial、selective-extraction scratch
   和经列举确认的 incomplete multipart upload；不得删除已验证的 immutable object，也不得用
   overwrite 修复失败。
6. 正式运行前必须重新记录 local free space、COS prefix inventory 和 HTTP metadata。若当前
   COS service 或网络代理不稳定，停止而不是扩大 retry budget。

`12 GiB` 门禁覆盖约 `1.82 GiB` 的双 archive 共存与最多 `4 GiB` 的选择性解压 scratch，并保留
超过 `6 GiB` 的额外 headroom。它不是对整盘空间的长期保证，正式运行仍需避免与其他任务共用
staging directory。

## 解除 blocker 所需的 maintainer decision

Issue #11 只有在以下事项均有明确评论后才能进入 transfer：

- 为 SPG1 提供独立 dataset license / permission，或明确批准改变 pilot scope，并重新审核来源；
- 对 BLAT、CAPSD、GFP 的 provenance mapping 和条件性许可逐项改为 `approved` 或 `blocked`；
- 接受两个 archive、`1,954,066,831 B`、`12 GiB` free-space gate、`7,816,267,324 B`
  network hard cap 与 `2.05 GB` COS hard cap；
- 确认正式 transfer 仍只代表 raw archive ingest，不授权 extraction-derived assay onboarding、
  Processing Manifest、Split Manifest、held-out use、replay 或 scientific gate。

在该 comment 出现前，Issue #11 保持 `ready-for-human` 和 blocked。
