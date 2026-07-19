# Issue tracker: GitHub

Issues and PRDs for this repository live in GitHub Issues at
`QUHONGYE-gif/protein-candidate-discovery-benchmark`.

Use the GitHub connector for repository, Issue, label, comment, and PR operations. If the
connector is unavailable and the GitHub CLI is installed, `gh` may be used as a fallback.

## Conventions

- Every implementation branch and PR references a GitHub Issue.
- Read the Issue body, labels, and comments before starting work.
- Create one Issue per bounded implementation or experiment objective.
- Use comments for evidence, blockers, run IDs, and COS manifest references.
- Use `Closes #<number>` only when all acceptance criteria are met.
- Do not close a scientific gate merely because implementation tests pass.
- Do not attach large datasets, embeddings, checkpoints, or benchmark outputs that belong in COS.

When a skill says "publish to the issue tracker", create a GitHub Issue. When it says "fetch the
relevant ticket", fetch the Issue body, labels, assignees, and comments. Use the state mapping in
`docs/agents/triage-labels.md` for triage changes.
