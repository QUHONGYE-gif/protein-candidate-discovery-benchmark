"""Verify training-information aggregates without accessing candidate truth."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from m5c_benign_scope import ASSAY_IDS
from summarize_m5_e3_prediction import bootstrap, check_summary

MODELS = ("random-forest-sklearn-v1", "xgboost-cpu-v1")
ARMS = ("old32", "old64", "new32", "new64")
DENOMINATORS = dict(zip(ASSAY_IDS, (378, 133, 86), strict=True))


def verify(result):
    assert result["paired_seed_blocks"] == 60 and result["scored_branches"] == 480
    assert result["new_fits"] == result["new_measurements"] == 0
    assert result["endpoint_reconstruction"] == result["common_pool"] == "PASS"
    rows = result["rows"]

    def key(r):
        return r["assay_id"], r["model_id"], r["run_seed"]

    expected = {(a, m, s) for a in ASSAY_IDS for m in MODELS for s in range(1729, 1739)}
    assert len(rows) == 60 and {key(r) for r in rows} == expected
    count_checks = 0
    for row in rows:
        assert row["label_cost_all_arms"] == 80
        assert row["high_value_denominator"] == DENOMINATORS[row["assay_id"]]
        coverage = row["initial_coverage"]
        assert 0 <= coverage["observed_high_32"] <= coverage["observed_high_64"] <= 64
        assert set(row["arms"]) == set(ARMS)
        for arm in ARMS:
            for policy in ("greedy", "exploration"):
                values = row["arms"][arm]
                count = values[policy + "_hits"]
                assert isinstance(count, int) and 0 <= count <= 16
                assert math.isclose(
                    values[policy + "_recall"], count / row["high_value_denominator"], abs_tol=1e-14
                )
                count_checks += 1
    interval_checks = 0
    seen = set()
    for summary in result["summaries"]:
        assay, model = summary["assay_id"], summary["model_id"]
        assert (assay, model) not in seen
        seen.add((assay, model))
        selected = sorted(
            [r for r in rows if r["assay_id"] == assay and r["model_id"] == model],
            key=lambda r: r["run_seed"],
        )
        assert len(selected) == summary["paired_seed_blocks"] == 10
        for metric, effect in summary["effects"].items():
            values = {a: [r["arms"][a][metric] for r in selected] for a in ARMS}
            if any(v is None for arr in values.values() for v in arr):
                assert effect["defined_pairs"] == sum(
                    all(r["arms"][a][metric] is not None for a in ARMS) for r in selected
                )
                continue
            for a in ARMS:
                assert math.isclose(
                    effect["arm_means"][a], math.fsum(values[a]) / 10, abs_tol=1e-12
                )
            old = np.asarray(values["old64"]) - values["old32"]
            new = np.asarray(values["new64"]) - values["new32"]
            for name, changes in (
                ("information_effect_old", old),
                ("information_effect_new", new),
                ("effect_interaction", new - old),
            ):
                check_summary(effect[name], bootstrap(changes, 11000 + MODELS.index(model)))
                interval_checks += 1
    assert seen == {(a, m) for a in ASSAY_IDS for m in MODELS}
    return {
        "status": "AGGREGATE_CHECKS_PASS",
        "paired_seed_blocks": 60,
        "count_recall_checks": count_checks,
        "interval_checks": interval_checks,
        "summaries": result["summaries"],
        "limits": result["limits"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "terminal", "smoke", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    terminal = json.loads(args.terminal.read_text())
    sha = hashlib.sha256(raw).hexdigest()
    assert terminal["status"] == "succeeded"
    assert sha == terminal["artifacts"]["result.json.gz"]
    result = json.loads(gzip.decompress(raw))
    report = verify(result)
    smoke = json.loads(gzip.decompress(args.smoke.read_bytes()))

    def key(r):
        return r["assay_id"], r["model_id"], r["run_seed"]

    indexed = {key(r): r for r in result["rows"]}
    assert len(smoke["rows"]) == 6
    assert all(r == indexed[key(r)] for r in smoke["rows"])
    report.update(
        smoke_full_exact_pairs=6,
        input_sha256=sha,
        source_sha256=terminal["source_sha256"],
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    with args.output.open("x") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("summaries", "limits")}))


if __name__ == "__main__":
    main()
