# 贡献指南

所有进入 `main` 的变更都通过 short-lived branch、Pull Request 和 squash merge。先按下表判断
PR 类型，再判断是否需要独立 tracking authority；Maintenance batch 不是绕过 PR 的 fast path。

## PR 分类

| 判定 | Major / semantic PR | Maintenance batch PR |
|---|---|---|
| 典型范围 | scientific protocol、dataset/split、metric、cost model、architecture、major runner、storage policy、governance、release | tests、docs、logging、small bugs、minor refactors、plots、CLI polish |
| 硬边界 | 只要可能改变科学语义、数据身份、执行边界、存储责任或公开接口，就属于 Major | 仅容纳不改变这些边界、可独立验证和回滚的小变化 |
| 最早提交点 | 形成可审阅的最小语义单元后立即提交 PR | 以下三者最早发生时提交：累计 10 个逻辑小变化；当前 Issue/milestone 结束；下一段实验需要其中信息 |
| 必需证据 | contract/ADR 影响、迁移/兼容、claim boundary 和测试 | atomic commits、父 Issue checklist、逐项测试和无语义变化声明 |

一个 logical small change 是一个可独立描述、验证和回滚的 atomic commit，不按文件数或代码
行数计。Maintenance 中一旦发现 semantic impact，应拆出并升级为 Major；无法安全拆分时，整个
PR 都按 Major 处理。

典型分类：

- 调整 primary metric、split membership、cost ratio、archive prefix 或 CLI 输出契约：
  `Major / semantic PR`；
- 只修正文档错别字、补充不改变行为的测试、改善日志或 plot 标签：
  `Maintenance batch PR`；
- 名为“bug fix”但会改变 candidate identity、执行边界或公开接口：
  `Major / semantic PR`，不能因改动小进入 maintenance；
- 同时包含日志修正和 schema 语义变化：拆分；无法拆分则整体按 Major。

## Tracking authority 门槛

满足任一条件时，PR 必须从 `github_issue` 或 `multica` 中选择且只选择一个权威来源：

- 属于 Major / semantic PR；
- 有独立用户或科研价值、独立验收标准，或独立风险/授权边界；
- 跨 milestone、需要多个 PR/维护批次、多个责任人或外部协调；
- 涉及数据迁移、held-out、权限、付费、隐私、安全、release 或外部配置；
- 没有合适的现有父 Issue 可以承载范围、证据和验收。

选择 `github_issue` 时，PR 记录 `repository`、`issue_number`、`url` 和
`authorization_and_acceptance`。选择 `multica` 时，PR 记录稳定的 `workspace_id`，并为每个对象
记录 `object_id`、MIRA `key`、`url`，以及统一的 `authorization_and_acceptance` 关系。URL 必须是
可读取的 `https://` 链接，不能只写 `MIRA-123` 简称。已有合适 authority record 的纯 maintenance
小变化不另建 Issue。Git 与所选 authority 的 comment/checklist 就是 ledger；不得创建
`maintenance-batch-ledger.yaml`、`MulticaGithubLinkRegistry` 或其他平行状态文件。

选择 `github_issue` 时，Multica issue 与 GitHub Issue 不要求一一对应；多个执行用 Multica issues
可以归入一个 GitHub 父 Issue。此时需要互链，使用最小约定，例如：

```text
Multica title: [GH#123] Improve repository workflow checks
Multica description: Source of truth: https://github.com/OWNER/REPO/issues/123
GitHub Issue comment: Execution tracked in Multica: https://MULTICA-EXAMPLE/issues/MIRA-123
```

这些 URL 都是占位示例，不应为了示例创建真实 Issue。

选择 `multica` 时不创建形式性的 GitHub parent，也不要求上述 cross-link；PR 自身携带完整的
Multica authority declaration。迁移审核对这一二选一规则的授权记录见
[PR #102 tracking decision](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/pull/102#issuecomment-5536839481)。

所选 authority 中的 maintenance checklist 可以直接记录 atomic commit 和验证：

```text
Maintenance batch (3/10 logical changes)
- [x] abc1234 docs: clarify retry wording — docs check
- [x] def5678 test: cover empty log field — targeted pytest
- [x] 012abcd cli: clarify help text only — CLI snapshot test
Trigger: current milestone closeout
Semantic/data/interface impact: none
```

## 开始工作

1. 阅读当前 Multica issue、所选 tracking authority、`AGENTS.md`、`CONTEXT.md` 和相关 accepted
   ADR；
2. 明确 acceptance criteria、out-of-scope、scientific claim boundary 和 PR 分类；
3. 核对 tracking authority 门槛；达到时选择 `github_issue` 或 `multica` 并补齐对应身份字段；
4. 从最新 `main` 创建短期分支；Major 形成最小语义单元后提交 PR，Maintenance 到最早触发点
   提交 batch PR。

推荐分支名称：

```text
feat/<issue>-<slug>
fix/<issue>-<slug>
data/<issue>-<slug>
exp/<issue>-<slug>
infra/<issue>-<slug>
docs/<issue>-<slug>
codex/<issue>-<slug>
```

若没有 GitHub Issue，`<issue>` 可使用 MIRA key、现有 authority 编号或明确的 maintenance batch
标识。不建立长期个人分支、`dev` 分支或 `test` 分支。

## 本地检查

```bash
python -m pip install -e ".[dev]"
make governance-check
make check
```

修改行为前优先写测试。先运行 targeted test，再运行完整 `make check`。

## Pull Request

PR 必须说明：

- `Major / semantic PR` 或 `Maintenance batch PR`，且只能选一个；
- 所选 `github_issue` 或 `multica` authority 及其全部必填身份、URL、授权/验收关系；
- scope 与 out-of-scope；
- 已执行的 tests；
- data/privacy/protocol/public-interface 影响；
- COS artifacts 或 schema migration；
- engineering evidence 与 scientific evidence 的边界；
- Maintenance batch 的触发条件、atomic commit checklist 和无语义变化声明。

只有在确实希望 PR 合并后关闭 GitHub Issue 且所有验收标准满足时，才写
`Closes #<number>`；普通关联使用 `Related #<number>`。不得在 PR 中提交公开数据全集、
embedding、checkpoint、trajectory 或秘密凭据。

## STATUS 更新

`docs/STATUS.md` 只在 Gate A 触发科学阶段迁移、Gate B 产生 terminal，或 milestone closeout
时更新。普通 PR/CI、单次 run、retry、日志、plot、维护批次和没有科学状态变化的工程推进都不
更新 STATUS。

## 文档语言

- `AGENTS.md`、agent-facing 文档、代码接口说明和技术 ADR 使用英文；
- README、贡献指南、科学协议、数据/隐私/复现说明以中文为主；
- 类名、接口、指标、数据集名称和无法准确翻译的术语保留英文。
