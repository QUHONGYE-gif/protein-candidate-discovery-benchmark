# Current project status

> Snapshot last verified: 2026-09-09 (M5D update; other milestone facts retained from the prior snapshot). Update this file only when one of the nine project-level
> fields below changes materially. PID, individual attempts, routine Issue comments and code-review
> events do not trigger an update. Prefer lifecycle closeout before taking a new snapshot, but do
> not require this file to be transactionally synchronized with every GitHub event.

## Live state

| Field | Current fact |
|---|---|
| Verified upstream base | [`main@9f332c2`](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/commit/9f332c2935b53ab1697688d0024944bc12a99161); Governance v2 migration rehearsal PR #102 is merged; PRs #103 and #105 remain open Drafts |
| Current scientific stage | M4/M5A/M5B 保持既有验收状态；M5C GFP-only 与四-assay 状态沿用 #103。正式 M5D 外部执行已完成、待独立审核；新增 M5D 特征消融开发实验完成，整体 unstable，未晋级证据 |
| Latest accepted scientific result | M5B `attempt-002` is the first complete terminal over the frozen 80 Alpha/IRED development same-states: all four primary contrasts are inside their frozen 98.75% equivalence intervals, while both task taxonomies and the overall taxonomy are `INCONCLUSIVE` |
| Current decision question | 四臂开发比较未出现 positive：F2改善平均预测但探索/Greedy收益不一致，F3收益集中。建议 revise，优先研究固定F2的不确定性目标对齐，不继续盲目增加特征 |
| Active implementation PR | [M5C #103](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/103) and [M5D #105](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/105); M5D readiness, execution code and compact results are proposed for review |
| Active experiment | 无运行中实验。原 formal-20260908-001 冻结；feature-iteration-20260909 的 smoke-001/full-001 完成，分别16/1,440臂状态，原F0全部360选择精确复现 |
| Next promotion gate | 独立审核原正式结果与新开发结果；开发队列已参与优化，后续独立外部确认须另行审计隔离边界。当前不授权证据晋级或COS归档 |
| Known blockers | 无执行阻塞；本轮停止于完整开发终态，独立审核待补充。没有组合达到预声明positive条件；后续策略实验需另立限定契约 |
| Superseded plans | [`M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`](M4_TO_M7_REVISED_EXPERIMENT_PLAN.md) remains the scientific roadmap. The owner explicitly superseded waiting for M5C/prior review for this M5D attempt in [#104](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/104#issuecomment-5583110386); [the prospective M5D contract](experiments/M5D_FORMAL_EXECUTION.md) records the bounded change |

Stage summary: M0–M3 and accepted M4/M5A/M5B results retain their historical scopes.
The current unreviewed M5D result is recorded in
[the formal execution report](results/m5d-formal-20260908/README.md).
The GFP-only result in #103 does not complete the M5C four-assay cohort. M6–M7 have not started.

新增开发结果见 [特征消融报告](results/m5d-feature-iteration-20260909/README.md)，
整体 `unstable / revise`；原正式结果仍保持其执行身份与待审核状态。

## Next bounded queue

1. Review and evaluate the existing M5D result in #104/#105; keep positive, negative
   and zero directions and the four-group limitation visible. No rerun is implied.
2. Continue M5C under its existing #103 authorities and scope; do not infer completion
   or new execution permission from M5D's separate owner instruction.
3. Consider later milestone acceptance/promotion only through a separate decision.

The queue records the order visible at the snapshot date. Routine progress within it does not require
transactional STATUS synchronization. No Issue, PR, experiment, credential, server or COS state is
changed by this file.

## Evidence boundaries

- `Gate 3 = STOP` is not a runtime failure and is not `FROZEN/PASS`.
- The accepted M5A artifact token `RECONSTRUCTION_VALID` records same-implementation,
  same-environment repeatability and 800/800 historical selection matching. It is not an
  independent implementation replication, statistical significance result or causal mechanism
  claim.
- M5B `attempt-002` is accepted only as an L1 exploratory ITERATION terminal with the actual-source
  deviation permanently disclosed: authorization named `a797efa` / `e8a01ce`, while the attempt
  used the pre-attempt feature-root correction `2aefbe0` / `00aaf06`. This is not retrospective
  exact-source authorization and does not permit a rerun.
- On the frozen 80 development same-states, the four primary immediate recall contrasts are inside
  their frozen 98.75% equivalence intervals. The registered task and overall classifications remain
  `INCONCLUSIVE`; this is not global equivalence, behavioral redundancy, multi-round benefit,
  causal mechanism, cross-assay generalization or benchmark evidence.
- M5D was prospectively authorized by the owner for execution before review, exact-bound
  to source `ff9528ca5721c5ebb5397789681065d7af112625`. It is a completed formal external
  attempt pending independent review, not a newly accepted scientific result.
- No experiment is currently active. This status record grants no rerun, new COS publication,
  multi-fidelity run or evidence-promotion authority.
