"""Read-only verification of fixed-count coverage aggregate results."""

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
DENOMINATORS = dict(zip(ASSAY_IDS, (378, 133, 86), strict=True))
PRIOR_SHA = "ba0de452db9012fe0b9960168ea5d9b5422eafbd0c2cd7a456170f327b5dcded"


def verify(result, prior):
    assert result["completed_states"] == 60 and result["new_fits"] == 360
    assert result["new_measurements"] == 0 and result["new_branches"] == 120
    for k in ("baseline_reconstruction", "fixed_training_count", "common_pool"):
        assert result[k] == "PASS"

    def key(r):
        return r["assay_id"], r["model_id"], r["run_seed"]

    expected = {(a, m, s) for a in ASSAY_IDS for m in MODELS for s in range(1729, 1739)}
    assert len(result["rows"]) == 60 and {key(r) for r in result["rows"]} == expected
    baseline = {key(r): r for r in prior["rows"]}
    count_checks = endpoint_checks = 0
    for row in result["rows"]:
        assert row["training_count"] == 32 and row["label_cost"] == 80
        denominator = DENOMINATORS[row["assay_id"]]
        assert denominator == row["high_value_denominator"]
        for metric, value in baseline[key(row)]["arms"]["new32"].items():
            assert row["random"][metric] == value
            endpoint_checks += 1
        for arm in ("random", "coverage"):
            values = row[arm]
            assert values["top16_high"] + values["top16_near"] + values["top16_far"] == 16
            assert values["top16_high"] == values["greedy_hits"]
            count_checks += 1
            for policy in ("greedy", "exploration"):
                hits = values[policy + "_hits"]
                assert isinstance(hits, int) and 0 <= hits <= 16
                assert math.isclose(values[policy + "_recall"], hits / denominator, abs_tol=1e-14)
                count_checks += 1
    interval_checks = 0
    seen = set()
    for summary in result["summaries"]:
        assay, model = summary["assay_id"], summary["model_id"]
        assert (assay, model) not in seen
        seen.add((assay, model))
        rows = sorted(
            [r for r in result["rows"] if r["assay_id"] == assay and r["model_id"] == model],
            key=lambda r: r["run_seed"],
        )
        assert len(rows) == summary["paired_states"] == 10
        for metric, value in summary["effects"].items():
            pairs = [(r["random"][metric], r["coverage"][metric]) for r in rows]
            if any(a is None or b is None for a, b in pairs):
                assert value["defined_pairs"] == sum(
                    a is not None and b is not None for a, b in pairs
                )
                continue
            for arm in ("random", "coverage"):
                assert math.isclose(
                    value[arm], math.fsum(r[arm][metric] for r in rows) / 10, abs_tol=1e-12
                )
            check_summary(
                value["change"], bootstrap([b - a for a, b in pairs], 11100 + MODELS.index(model))
            )
            interval_checks += 1
    assert seen == {(a, m) for a in ASSAY_IDS for m in MODELS}
    coverage_rows = result["coverage_rows"]
    assert len(coverage_rows) == 30
    assert {(r["assay_id"], r["run_seed"]) for r in coverage_rows} == {
        (a, s) for a in ASSAY_IDS for s in range(1729, 1739)
    }
    assert len(result["coverage_summaries"]) == 3
    for summary in result["coverage_summaries"]:
        rows = sorted(
            [r for r in coverage_rows if r["assay_id"] == summary["assay_id"]],
            key=lambda r: r["run_seed"],
        )
        for row in rows:
            assert 0 <= row["training_overlap"] <= 32
            assert all(row[k] >= 0 for k in row if k.endswith("_distance"))
        assert math.isclose(
            summary["mean_training_overlap"],
            np.mean([r["training_overlap"] for r in rows]),
            abs_tol=1e-12,
        )
        for m in ("mean", "p90"):
            changes = [
                r["coverage_" + m + "_distance"] - r["random_" + m + "_distance"] for r in rows
            ]
            check_summary(summary[m + "_distance_change"], bootstrap(changes, 11150))
            interval_checks += 1
    return {
        "status": "AGGREGATE_CHECKS_PASS",
        "states": 60,
        "count_checks": count_checks,
        "endpoint_metric_checks": endpoint_checks,
        "interval_checks": interval_checks,
        "coverage_summaries": result["coverage_summaries"],
        "summaries": result["summaries"],
        "limits": result["limits"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "terminal", "smoke", "prior", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    terminal = json.loads(args.terminal.read_text())
    assert terminal["status"] == "succeeded" and terminal["mode"] == "full"
    assert hashlib.sha256(raw).hexdigest() == terminal["artifacts"]["result.json.gz"]
    prior = args.prior.read_bytes()
    assert hashlib.sha256(prior).hexdigest() == PRIOR_SHA
    result = json.loads(gzip.decompress(raw))
    report = verify(result, json.loads(gzip.decompress(prior)))
    smoke = json.loads(gzip.decompress(args.smoke.read_bytes()))

    def key(r):
        return r["assay_id"], r["model_id"], r["run_seed"]

    indexed = {key(r): r for r in result["rows"]}
    assert len(smoke["rows"]) == 6 and all(r == indexed[key(r)] for r in smoke["rows"])
    report.update(
        smoke_full_exact_states=6,
        source_sha256=terminal["source_sha256"],
        input_sha256=hashlib.sha256(raw).hexdigest(),
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    with args.output.open("x") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("summaries", "coverage_summaries", "limits")
            }
        )
    )


if __name__ == "__main__":
    main()
