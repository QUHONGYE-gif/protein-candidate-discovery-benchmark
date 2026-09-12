"""Verify new-state replication aggregates without raw candidate data or truth."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from summarize_m5_e3_prediction import ASSAY_IDS, DENOMINATORS, check_summary

SEEDS = tuple(range(2000, 2020))
MODEL = "xgboost-cpu-v1"


def key(row):
    return row["assay_id"], row["initial_sample_size"], row["run_seed"]


def read(directory):
    raw = (directory / "result.json.gz").read_bytes()
    terminal = json.loads((directory / "terminal.json").read_text())
    assert terminal["status"] == "succeeded"
    assert hashlib.sha256(raw).hexdigest() == terminal["artifacts"]["result.json.gz"]
    return json.loads(gzip.decompress(raw))


def weighted(values, weights):
    cells = np.asarray(values, dtype=float).reshape(-1, 20)
    per_seed = np.array([math.fsum(cells[:, i]) / len(cells) for i in range(20)])
    draws = weights @ per_seed / 20
    return {
        "estimate": math.fsum(per_seed) / 20,
        "interval": np.percentile(draws, [0.3125, 99.6875]).tolist(),
    }


def verify(full, smoke, frontier, source):
    checks = 0
    for data, seeds in ((full, SEEDS), (smoke, (1729,))):
        expected = {(a, n, s) for a in ASSAY_IDS for n in (32, 64) for s in seeds}
        assert len(data["rows"]) == data["completed_states"] == len(expected)
        assert len({key(r) for r in data["rows"]}) == len(expected)
        assert {key(r) for r in data["rows"]} == expected
        assert data["point_fits"] == 2 * len(expected) and data["new_measurements"] == 0
        for r in data["rows"]:
            assert r["model_id"] == MODEL
            assert r["high_denominator"] == DENOMINATORS[r["assay_id"]][1]
            assert 0 < r["remaining_high"] <= r["high_denominator"]
            assert r["historical_point_reconstruction"] == (
                "PASS" if seeds == (1729,) else "not_applicable"
            )
            for arm in ("old", "new"):
                previous = 0
                for k in (8, 16, 32, 64):
                    m = r[arm]
                    assert type(m[f"hits_{k}"]) is int
                    assert previous <= m[f"hits_{k}"] <= min(k, r["remaining_high"])
                    assert math.isclose(m[f"precision_{k}"], m[f"hits_{k}"] / k, abs_tol=1e-14)
                    assert math.isclose(
                        m[f"recall_{k}"], m[f"hits_{k}"] / r["high_denominator"], abs_tol=1e-14
                    )
                    previous = m[f"hits_{k}"]
                    checks += 3
    old_checks = 0
    for r in smoke["rows"]:
        f = next(x for x in frontier["rows"] if key(x) == key(r) and x["model_id"] == MODEL)
        original = next(x for x in source["rows"] if key(x) == key(r) and x["model_id"] == MODEL)
        for arm in ("old", "new"):
            for metric, actual in r[arm].items():
                expected = (
                    original[arm + "_diagnostics"][metric]
                    if metric in ("spearman", "top1_auc", "mae")
                    else f[arm + "_frontier"][metric]
                )
                assert (
                    actual is None
                    if expected is None
                    else math.isclose(actual, expected, abs_tol=1e-12)
                )
                old_checks += 1
    rng = np.random.Generator(np.random.PCG64(11001))
    slots = rng.integers(0, 20, (20000, 20))
    weights = np.zeros((20000, 20), dtype=int)
    np.add.at(weights, (np.arange(20000)[:, None], slots), 1)
    expected_cells = {(a, n) for a in ASSAY_IDS for n in (32, 64, "combined")}
    assert len(full["summaries"]) == 9
    assert {(s["assay_id"], s["initial_sample_size"]) for s in full["summaries"]} == expected_cells
    intervals, means, undefined = 0, 0, 0
    for s in full["summaries"]:
        rows = sorted(
            [
                r
                for r in full["rows"]
                if r["assay_id"] == s["assay_id"]
                and (
                    s["initial_sample_size"] == "combined"
                    or r["initial_sample_size"] == s["initial_sample_size"]
                )
            ],
            key=key,
        )
        assert s["states"] == len(rows) and s["seed_slots"] == 20
        assert set(s["metrics"]) == set(rows[0]["old"]) == set(rows[0]["new"])
        for metric, record in s["metrics"].items():
            pairs = [(r["old"][metric], r["new"][metric]) for r in rows]
            defined = sum(a is not None and b is not None for a, b in pairs)
            assert record["defined_pairs"] == defined
            if defined != len(rows):
                assert record["change"] is record["old"] is record["new"] is None
                undefined += 1
                continue
            for i, arm in enumerate(("old", "new")):
                assert math.isclose(
                    record[arm], math.fsum(p[i] for p in pairs) / len(pairs), abs_tol=1e-12
                )
                means += 1
            check_summary(record["change"], weighted([b - a for a, b in pairs], weights))
            assert (
                record["change"]["seed"],
                record["change"]["draws"],
                record["change"]["coverage"],
            ) == (11001, 20000, 0.99375)
            intervals += 1
    return {
        "status": "PASS",
        "full_states": 120,
        "smoke_states": 6,
        "count_rate_checks": checks,
        "old_metric_reconstructions": old_checks,
        "reported_exact_historical_point_arrays": 12,
        "independent_intervals": intervals,
        "arm_mean_checks": means,
        "undefined_summary_metrics": undefined,
        "raw_truth_or_candidate_access": False,
        "scope": "independent arithmetic implementation, not independent scientific review",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    prior = []
    for path, sha in (
        (
            "docs/results/m5-e3-frontier-20260909/result.json.gz",
            "67e98445f6c4fe3f6fd46a966b2af321122ea4c1cc8371a7eac81fef164715ba",
        ),
        (
            "docs/results/m5-e3-input-intervention-20260909/result.json.gz",
            "937febfe04c7f45ff490249fdce1f33b0944d54b7a9a9a9939f3a0776d51eb08",
        ),
    ):
        raw = (root / path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sha
        prior.append(json.loads(gzip.decompress(raw)))
    for folder in (args.full, args.smoke):
        execution = json.loads((folder / "execution.json").read_text())
        assert (
            hashlib.sha256((root / "scripts/run_m5_e3_replication.py").read_bytes()).hexdigest()
            == execution["source_sha256"]
        )
        assert (
            hashlib.sha256(
                (root / "docs/experiments/M5_E3_REPLICATION.md").read_bytes()
            ).hexdigest()
            == execution["plan_sha256"]
        )
    result = verify(read(args.full), read(args.smoke), *prior)
    result.update(
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        full_sha256=hashlib.sha256((args.full / "result.json.gz").read_bytes()).hexdigest(),
        wall_seconds=time.monotonic() - started,
    )
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True, allow_nan=False)
        f.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
