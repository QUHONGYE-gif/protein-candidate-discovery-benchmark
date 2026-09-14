# M5C Stage 2 formal execution runbook

This runbook implements the authorized `m5c-proteingym-prospective-v1` version 6 contract for
[MIRA-11](https://multica-server.tail8b7421.ts.net/issues/MIRA-11). The authorized scientific
contract remains the manifest at
`manifests/experiments/m5c/gate-a-proposal-v3/milestone-contract-manifest.json`; this runbook does
not revise it.

## Review and start barrier

Do not launch an attempt until Mika has reviewed the exact execution commit and tree and reported
PASS for contract conformance, leakage boundaries, metrics, tests, Run identity and failure
handling. The execution checkout must then remain clean at that exact commit/tree. Immediately
before launch, inspect `protein-6000` for any M5C process, attempt directory, terminal or `_SUCCESS`.
Any drift stops the launch. Provide `GITHUB_TOKEN` to the service through its secure credential
channel so the production Contract Check can fresh-authenticate both private-repository decision
comments; an unauthenticated or unavailable comment fails closed.

The controller fresh-runs the production Gate A Contract Check and Governance v2 server Check. It
requires kernel hostname `ps`, the passing input/capability manifest SHA-256
`130d9f3d9086274c7209158cdcbb3a4054ff3742e3711220542adecae14828fc`, and scientific-contract
digest `abc16ed38664d63efede535de3ecd8b8552031e52b156c37023e7b7e717d5278`. It constructs the
Run-pinned identity in memory before its first `/srv/pcdb/runs/M5C/<attempt-id>` write, writes that
identity first, and runs the Contract Check again with the exact source/environment bindings before
opening hidden truth.

## Capability boundary

The worker reads each observable candidate artifact and creates the frozen
`sequence-composition-420-v1` matrix before opening truth. It forks the surrogate pool before the
truth file is opened. Surrogate processes receive only the observable matrix and the exact
branch-revealed observations; they never receive hidden truth or the static proxy. After all
surrogate processes exit, the evaluator alone reads `ESM1v_single` from its exact-bound raw score
source. Candidate selections, raw labels, high-value membership and boundary values remain in the
private attempt area or evaluator memory and are excluded from aggregate artifacts.

Each state uses the M4 RF/XGBoost config bytes, five exact PCG64 acquisition bootstrap members,
candidate-ID tie breaking, one G batch, one E batch and 64 exact-overlap Matched Random batches.
The aggregate applies the frozen 20,000-draw shared-seed assay bootstrap, 99.375% type-7 intervals,
sensitivity checks, per-assay taxonomy and cohort precedence. Static-proxy outputs are descriptive
and cannot change eligibility, taxonomy or stopping.

## Commands

Set `SOURCE` to the reviewed checkout and `PYTHON` to its installed Python. First run the same
controller in outcome-free mode:

```sh
"$PYTHON" "$SOURCE/scripts/run_m5c_proteingym.py" \
  --attempt-id attempt-001 \
  --contract-manifest "$SOURCE/manifests/experiments/m5c/gate-a-proposal-v3/milestone-contract-manifest.json" \
  --gate-a "$SOURCE/manifests/experiments/m5c/gate-a-proposal-v3/gate-a-decision.proposal.json" \
  --governance-config "$SOURCE/configs/governance/v2.json" \
  --input-manifest "$SOURCE/manifests/experiments/m5c/preflight-result-v5/input-capability-manifest.json" \
  --input-root /srv/pcdb/data/raw/proteingym/v1.3/four-assay-unified-v1 \
  --source-root "$SOURCE" --python "$PYTHON" \
  --expected-git-commit REVIEWED_COMMIT --expected-git-tree REVIEWED_TREE \
  --workers 4 --preflight-only
```

Only after that returns `status: PASS`, repeat the command without `--preflight-only`. The formal
attempt must run as `service:pcdb-runner`; ordinary execution performs zero COS writes. The worker
count is capped at 16 so 24 wall hours also bounds cumulative CPU at 384 hours. The controller
enforces the 24-hour wall, 48 GB process-tree RSS and 30 GiB scratch caps, records zero network/COS
writes by the scientific worker plus a 1 MiB upper bound for authenticated Gate checks, writes
`terminal.json` and the attempt ledger append-only, and creates `_SUCCESS` last.

## Stop and retry rules

The first complete valid terminal is canonical, including negative, heterogeneous or inconclusive
results. Never retry after inspecting a scientific result. `attempt-002` is accepted only after an
externally evidenced transient infrastructure failure with no complete terminal and unchanged
identity, or after a pre-result meaning-preserving repair at a new Mika-reviewed source identity
using `--reviewed-repair`. There is no automatic retry and no third attempt.

On a stop, retain the entire attempt directory. Do not overwrite or delete evidence. Revoke
capabilities and stop new writes; after the first append-only artifact there is no destructive
rollback. Do not publish to COS or promote any result before the later Gate B workflow.

## Aggregate-only reproduction

After the main terminal, Mika may mount only its sealed `aggregate/aggregate-input.json` and run
`scripts/reproduce_m5c_proteingym.py` with the exact aggregate SHA-256, reviewed commit/tree,
source root and a new `/srv/pcdb/runs/M5C/<review-attempt-id>` output root. The script rejects any
candidate-level identity token, recomputes all intervals, sensitivity inputs, assay taxonomies and
the cohort roll-up, records zero network/COS writes, and creates its `_SUCCESS` marker last. It does
not accept candidate, feature, proxy or truth paths.
