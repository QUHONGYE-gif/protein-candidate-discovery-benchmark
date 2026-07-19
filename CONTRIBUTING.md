# 贡献指南

本项目采用 GitHub Issue → short-lived branch → Pull Request → squash merge 的协作流程。

## 开始工作

1. 阅读关联 Issue、`AGENTS.md`、`CONTEXT.md` 和相关 ADR；
2. 明确 acceptance criteria、out-of-scope 和 scientific claim boundary；
3. 从最新 `main` 创建带 Issue 编号的短期分支；
4. 对非平凡修改尽早创建 Draft PR。

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

不建立长期个人分支、`dev` 分支或 `test` 分支。

## 本地检查

```bash
python -m pip install -e ".[dev]"
make check
```

修改行为前优先写测试。先运行 targeted test，再运行完整 `make check`。

## Pull Request

PR 必须说明：

- 关联 Issue；
- scope 与 out-of-scope；
- 已执行的 tests；
- data/privacy/protocol 影响；
- COS artifacts 或 schema migration；
- engineering evidence 与 scientific evidence 的边界。

不得在 PR 中提交公开数据全集、embedding、checkpoint、trajectory 或秘密凭据。

## 文档语言

- `AGENTS.md`、agent-facing 文档、代码接口说明和技术 ADR 使用英文；
- README、贡献指南、科学协议、数据/隐私/复现说明以中文为主；
- 类名、接口、指标、数据集名称和无法准确翻译的术语保留英文。
