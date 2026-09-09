"""Independent aggregate verification of the fixed-data ranking contrast."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

from m5c_benign_scope import ASSAY_IDS
from summarize_m5_e3_prediction import bootstrap, check_summary

MODEL = "xgboost-cpu-v1"
DENOMINATORS = dict(zip(ASSAY_IDS, (378, 133, 86), strict=True))
PRIOR_SHA = "ba0de452db9012fe0b9960168ea5d9b5422eafbd0c2cd7a456170f327b5dcded"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(result, prior):
    assert result["completed_states"] == 60 and result["new_fits"] == 120
    assert result["new_measurements"] == 0 and result["scored_branches"] == 120
    assert result["baseline_refit"] == "EXACT"
    assert result["fixed_observations"] == result["common_pool"] == "PASS"

    def key(r):
        return r["assay_id"], r["training_count"], r["run_seed"]

    expected = {(a, n, s) for a in ASSAY_IDS for n in (32, 64) for s in range(1729, 1739)}
    assert len(result["rows"]) == 60 and {key(r) for r in result["rows"]} == expected
    old = {(r["assay_id"], r["run_seed"]): r for r in prior["rows"] if r["model_id"] == MODEL}
    count_checks = endpoint_checks = 0
    for row in result["rows"]:
        assay, size, seed = key(row)
        assert row["model_id"] == MODEL and row["label_cost"] == 80
        denominator = DENOMINATORS[assay]
        assert row["high_value_denominator"] == denominator
        assert 64 <= row["frontier_count"] <= 128 and 0 <= row["top16_overlap"] <= 16
        assert 0 <= row["frontier_high_count"] <= row["frontier_count"]
        before = old[(assay, seed)]["arms"][f"new{size}"]
        for k, v in before.items():
            if k in row["regression"]:
                assert row["regression"][k] == v
                endpoint_checks += 1
        assert set(row["regression"]) == set(row["ranking"])
        assert "mae" not in row["ranking"] and "exploration_hits" not in row["ranking"]
        for arm in ("regression", "ranking"):
            m = row[arm]
            assert m["top16_high"] + m["top16_near"] + m["top16_far"] == 16
            assert m["top16_high"] == m["greedy_hits"]
            assert math.isclose(m["greedy_recall"], m["greedy_hits"] / denominator, abs_tol=1e-14)
            assert 0 <= m["greedy_hits"] <= m["hits_32"] <= m["hits_64"] <= 64
            assert m["hits_64"] <= row["frontier_high_count"]
            defined = 0 < row["frontier_high_count"] < row["frontier_count"]
            assert (m["common_frontier_auc"] is not None) == defined
            for k, v in m.items():
                if v is not None:
                    assert math.isfinite(v)
                    if k.endswith("auc"):
                        assert 0 <= v <= 1
            count_checks += 6
    seen, interval_checks, undefined = set(), 0, []
    for summary in result["summaries"]:
        assay, size = summary["assay_id"], summary["training_count"]
        assert (assay, size) not in seen
        seen.add((assay, size))
        rows = sorted(
            [r for r in result["rows"] if r["assay_id"] == assay and r["training_count"] == size],
            key=lambda r: r["run_seed"],
        )
        assert len(rows) == summary["paired_states"] == 10
        assert set(summary["effects"]) == set(rows[0]["regression"])
        for metric, value in summary["effects"].items():
            pairs = [(r["regression"][metric], r["ranking"][metric]) for r in rows]
            defined = sum(a is not None and b is not None for a, b in pairs)
            if defined != 10:
                assert value == {"defined_pairs": defined}
                undefined.append(
                    {
                        "assay_id": assay,
                        "training_count": size,
                        "metric": metric,
                        "defined_pairs": defined,
                    }
                )
                continue
            for arm in ("regression", "ranking"):
                assert math.isclose(
                    value[arm], math.fsum(r[arm][metric] for r in rows) / 10, abs_tol=1e-12
                )
            check_summary(value["change"], bootstrap([b - a for a, b in pairs], 11200 + size))
            interval_checks += 1
    assert seen == {(a, n) for a in ASSAY_IDS for n in (32, 64)}
    return {
        "states": 60,
        "count_checks": count_checks,
        "endpoint_checks": endpoint_checks,
        "interval_checks": interval_checks,
        "undefined_cells": undefined,
        "summaries": result["summaries"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("input", "prior", "smoke", "terminal", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    assert digest(args.prior) == PRIOR_SHA
    result = json.loads(gzip.decompress(args.input.read_bytes()))
    prior = json.loads(gzip.decompress(args.prior.read_bytes()))
    terminal = json.loads(args.terminal.read_text())
    assert terminal["status"] == "succeeded"
    assert terminal["artifacts"]["result.json.gz"] == digest(args.input)
    report = verify(result, prior)
    smoke = json.loads(gzip.decompress(args.smoke.read_bytes()))
    assert smoke["completed_states"] == 6
    keyed = {(r["assay_id"], r["training_count"], r["run_seed"]): r for r in result["rows"]}
    for row in smoke["rows"]:
        assert row == keyed[(row["assay_id"], row["training_count"], row["run_seed"])]
    report.update(
        {
            "status": "AGGREGATE_CHECKS_PASS",
            "smoke_full_exact_states": 6,
            "input_sha256": digest(args.input),
            "source_sha256": terminal["source_sha256"],
            "verifier_sha256": digest(Path(__file__)),
            "limits": result["limits"],
        }
    )
    with args.output.open("x") as handle:
        json.dump(report, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "states",
                    "count_checks",
                    "endpoint_checks",
                    "interval_checks",
                    "smoke_full_exact_states",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
