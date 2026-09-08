# M5D formal external execution — 2026-09-08

Issue #104 · PR #105 · `m5d-skempi-external-v1`

## Result and status

The owner-authorized formal M5D attempt completed on `129.28.97.247` only.
All **360 states / 23,760 branches** completed; disk reconstruction passed in the
runner and again in a fresh verification process. Independent review/evaluation
is **PENDING**; the scientific Gate is **NOT_ASSESSED**. No COS writes occurred.

Neither fixed method has both primary contrast lower bounds above zero. RF has a
negative point estimate against Greedy and a positive one against matched Random;
XGBoost is near zero against Greedy and positive against matched Random. These
results do not establish a positive external discovery mechanism in this cohort.
All positive, zero and negative directions are retained below and in the full report.

The owner's prospective execution-order change is recorded in the
[contract](../../experiments/M5D_FORMAL_EXECUTION.md) and
[Issue decision comment](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/104#issuecomment-5583110386).
Execution proceeded before independent review and M5C completion, as instructed.
Earlier readiness-only restrictions remain historical records, not current blockers.

## Primary results

Immediate tie-inclusive top-1% recall increment differences, **percentage points**.
E is the frozen exploration method; G is model-matched Greedy; M is the mean of
64 exact-overlap-matched Random branches. Intervals are the predeclared 99.375%
whole-group bootstrap intervals (20,000 draws). Only four groups support these
descriptive intervals; ten seeds and 64 controls do not increase the group count.

| Method | Contrast | Estimate (pp) | Interval (pp) |
| --- | --- | ---: | --- |
| RF + Uncertainty | E−G | -7.6389 | [-16.3889, +3.3333] |
| RF + Uncertainty | E−M | +2.3698 | [-1.0938, +7.5260] |
| XGBoost + UCB | E−G | -0.1736 | [-0.6944, +0.0000] |
| XGBoost + UCB | E−M | +1.5310 | [-0.1562, +4.0625] |

## Secondary results

Recall differences are percentage points; best-improvement differences are kcal/mol.
Simple-regret reduction is algebraically identical to best-found improvement for
this single fixed-universe step and is also retained in the private branch records.
These secondary outcomes do not replace the primary decision.

| Method | Metric / contrast | Estimate | Interval |
| --- | --- | ---: | --- |
| RF | recall_0.005 E−G | -6.6667 | [-16.6667, +5.0000] |
| RF | recall_0.005 E−M | +3.8064 | [-1.0156, +14.1406] |
| RF | recall_0.02 E−G | -11.7550 | [-21.0198, -1.0000] |
| RF | recall_0.02 E−M | +1.4089 | [+0.0690, +3.2031] |
| RF | best_improvement E−G | -0.0962 | [-0.3221, +0.0000] |
| RF | best_improvement E−M | -0.0048 | [-0.0370, +0.0327] |
| XGBoost | recall_0.005 E−G | -0.1736 | [-0.6944, +0.0000] |
| XGBoost | recall_0.005 E−M | +1.7453 | [-0.1562, +4.0625] |
| XGBoost | recall_0.02 E−G | -0.4712 | [-2.8849, +1.0000] |
| XGBoost | recall_0.02 E−M | +1.5991 | [+1.0547, +1.9141] |
| XGBoost | best_improvement E−G | -0.0117 | [-0.0498, +0.0030] |
| XGBoost | best_improvement E−M | +0.0055 | [-0.0069, +0.0164] |

## Cohort and interpretation limits

Nine complexes / 1,774 candidates met the prospective >=80-candidate threshold.
Initial 32/64, seeds 1729–1738, batch 16 and the two fixed model/acquisition pairs
produce 40 states per complex. The 41 mutation-count features and stronger-binding
`-delta_delta_g` target retain the readiness identities. No outcome-based exclusions,
new feature engineering, model tuning or normalization were performed.

One conservative group contains six complexes; the other three contain one each.
Seeds and initial sizes are averaged within each complex/model, complexes are
averaged within group, and groups receive equal weight. The large six-complex group
therefore receives 25% rather than dominating by its row count. All group and
complex/initial-size estimates appear in [report.json](report.json); the full
observable cohort and pool hashes are in [provenance.json](provenance.json).

These are within-complex replay tasks with separately fitted surrogates, not a
cross-complex prediction experiment. Publisher/shared-protein grouping is conservative
but has no independent sequence-homology audit. Small pools give coarse/tied high-value
memberships. The common >=80 cohort omits smaller complexes; four groups limit
external generalization. This result does not select a new strategy or trigger retries.

## Execution and evidence

- Exact execution source: `ff9528ca5721c5ebb5397789681065d7af112625`.
- Attempt: `/srv/pcdb/runs/M5D/formal-20260908-001/`.
- Source checkout: `/srv/pcdb/scratch/m5d-source-ff9528c/`.
- Runtime: Python 3.11.2, NumPy 2.4.6, scikit-learn 1.9.0, xgboost-cpu 3.2.0.
- Start UTC: `2026-09-08T09:58:44.545077+00:00`.
- Elapsed: **159.020 s**; peak tree RSS **322,781,184 bytes** (~307.8 MiB).
- Attempt size: **32,729,461 bytes**; one worker and one native thread.
- Limits: 2 h, 1.5 GiB tree RSS, 1 GiB attempt bytes; all respected.
- Six targeted formal tests pass. Stable execution-source `make check`: **950 passed
  in 224.17 s**, with formatting, Ruff, mypy and governance checks passing.
- Execution-source [CI 34212555607](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/actions/runs/34212555607): SUCCESS.
- Fresh-process `--verify`: `{"readback": "PASS", "states": 360}`, exit 0.
- Downloaded report/terminal/success-marker hashes independently match the server
  terminal inventory. Publishing commits after the execution source contain compact
  evidence/documentation only and do not change the executed implementation.

[terminal.json](terminal.json) is the unchanged server completion record;
[server-success.json](server-success.json) is a byte copy of the server `_SUCCESS`.
[provenance.json](provenance.json) is a compact derivative of the private RunSpec,
with candidate IDs and full state inventory replaced by their hashes.
The server remains authoritative for candidates, features, truth, private selections,
all branch metrics and full RunSpec. Git contains only compact summaries.

```sh
cd /srv/pcdb/scratch/m5d-source-ff9528c
PYTHONPATH=src /home/siyuan/pcdb-env-issue76-m4-0994a73f/bin/python \
  scripts/run_m5d_skempi.py \
  --input-root /srv/pcdb/data/processed/skempi/skempi-readiness-72bc80dd1524f7815039160bc095045308f4d56c9e819a84203a6fe70e508ec6 \
  --output /srv/pcdb/runs/M5D/formal-20260908-001 \
  --verify
```

The existing attempt is immutable and must never be rerun without `--verify`.
The remaining work is independent code/scientific review and evaluation of the
existing result, followed by separately authorized acceptance/merge/archive decisions.
