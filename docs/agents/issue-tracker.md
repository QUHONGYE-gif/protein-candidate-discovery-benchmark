# Issue tracking authority

Repository-level work that meets the independent-tracking threshold selects exactly one authority:
a `github_issue` at `QUHONGYE-gif/protein-candidate-discovery-benchmark`, or a `multica` workspace
object. Multica issues also carry execution ownership and stage state; several Multica issues may
share one PR or, when GitHub is the authority, one GitHub source-of-truth Issue.

Use the matching tracker client. For GitHub repository, Issue, label, comment, and PR operations,
use the GitHub connector or `gh`; for Multica authority records, use the Multica CLI.

## Conventions

- All changes entering `main` use a reviewed PR. Classify it as exactly one of
  `Major / semantic PR` or `Maintenance batch PR` using `CONTRIBUTING.md`.
- A Major/semantic change declares either `github_issue` or `multica` authority and enters a PR when
  its smallest reviewable semantic unit exists.
- Select an independent authority for independent value/acceptance/risk, cross-milestone or multi-
  PR/multi-owner coordination, external data/permission/cost/release risk, or when no suitable
  parent exists. Do not create a formal GitHub parent when `multica` was selected.
- Every authority record states `authorization_and_acceptance`. GitHub records add `repository`,
  `issue_number` and `url`; Multica records add `workspace_id` and per-object `object_id`, MIRA key
  and `url`.
- Pure maintenance under a suitable authority is recorded as an atomic commit plus its authority
  checklist/comment. Open its PR at the earliest of 10 logical changes, authority/milestone
  closeout, or the next experiment needing the information.
- Git and the authority record are the ledger. Do not create `maintenance-batch-ledger.yaml`,
  `MulticaGithubLinkRegistry`, or another status registry.
- Read the current Multica issue and the complete selected authority record, including relevant
  comments, before starting work.
- Use comments for compact evidence, blockers, run IDs and artifact manifest references; keep full
  logs and large artifacts in their designated data plane.
- Use `Closes #<number>` only when all acceptance criteria are met.
- Do not close a scientific gate merely because implementation tests pass.
- Do not attach large datasets, embeddings, checkpoints, or benchmark outputs that belong in COS.

## Minimal cross-links

When `github_issue` is authority, use `[GH#123] <summary>` in a Multica title and
`Source of truth: https://github.com/OWNER/REPO/issues/123` in its description. If a GitHub Issue
needs the reverse link, add `Execution tracked in Multica: https://MULTICA-EXAMPLE/issues/MIRA-123`.
These are placeholder examples; never create a real external Issue just to demonstrate the format.

When a skill says "publish to the issue tracker", first apply the tracking-authority threshold. If
it is not met, update the existing authority checklist. If it is met, use the selected
`github_issue` or `multica` path and never manufacture the other object. When it says "fetch the
relevant ticket", fetch the selected authority record and comments. Use the state mapping in
`docs/agents/triage-labels.md` for GitHub triage changes.
