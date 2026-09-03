# Issue tracker: GitHub

Repository-level Issues and PRDs that meet the independent-Issue threshold live in GitHub Issues at
`QUHONGYE-gif/protein-candidate-discovery-benchmark`. Multica issues carry execution ownership and
stage state; several Multica issues may share one GitHub source-of-truth Issue.

Use the GitHub connector for repository, Issue, label, comment, and PR operations. If the
connector is unavailable and the GitHub CLI is installed, `gh` may be used as a fallback.

## Conventions

- All changes entering `main` use a reviewed PR. Classify it as exactly one of
  `Major / semantic PR` or `Maintenance batch PR` using `CONTRIBUTING.md`.
- A Major/semantic change receives an independent GitHub Issue and enters a PR when its smallest
  reviewable semantic unit exists.
- Create another independent GitHub Issue only for independent value/acceptance/risk, cross-
  milestone or multi-PR/multi-owner coordination, external data/permission/cost/release risk, or
  when no suitable parent exists.
- Pure maintenance under a suitable parent is recorded as an atomic commit plus a parent Issue
  checklist/comment. Open its PR at the earliest of 10 logical changes, parent Issue/milestone
  closeout, or the next experiment needing the information.
- Git and the parent Issue are the ledger. Do not create `maintenance-batch-ledger.yaml`,
  `MulticaGithubLinkRegistry`, or another status registry.
- Read the current Multica issue and linked GitHub parent Issue body, labels and comments before
  starting work.
- Use comments for compact evidence, blockers, run IDs and artifact manifest references; keep full
  logs and large artifacts in their designated data plane.
- Use `Closes #<number>` only when all acceptance criteria are met.
- Do not close a scientific gate merely because implementation tests pass.
- Do not attach large datasets, embeddings, checkpoints, or benchmark outputs that belong in COS.

## Minimal cross-links

Use `[GH#123] <summary>` in a Multica title and
`Source of truth: https://github.com/OWNER/REPO/issues/123` in its description. If a GitHub Issue
needs the reverse link, add `Execution tracked in Multica: https://MULTICA-EXAMPLE/issues/MIRA-123`.
These are placeholder examples; never create a real external Issue just to demonstrate the format.

When a skill says "publish to the issue tracker", first apply the independent-Issue threshold. If
it is not met, update the existing parent checklist instead of creating a GitHub Issue. When it says
"fetch the relevant ticket", fetch both the current Multica issue and the linked GitHub Issue body,
labels, assignees and comments. Use the state mapping in `docs/agents/triage-labels.md` for triage
changes.
