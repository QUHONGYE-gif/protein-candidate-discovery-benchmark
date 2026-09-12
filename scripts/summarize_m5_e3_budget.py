"""Independent arithmetic verification of budget effects from identity-bound aggregates."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from summarize_m5_e3_prediction import ASSAY_IDS, DENOMINATORS, MODELS, bootstrap, check_summary


def metrics(row, version):
    f = row[version + "_frontier"]
    values = {}
    for k in (8, 16, 32, 64):
        values[f"hits_{k}"] = f[f"hits_{k}"]
        values[f"precision_{k}"] = f[f"hits_{k}"] / k
        values[f"recall_{k}"] = f[f"hits_{k}"] / DENOMINATORS[row["assay_id"]][1]
    cumulative = [0, f["hits_16"], f["hits_32"], f["hits_64"]]
    for i, (name, width) in enumerate((("1_16", 16), ("17_32", 16), ("33_64", 32))):
        values[f"window_{name}_hits"] = cumulative[i + 1] - cumulative[i]
        values[f"window_{name}_rate"] = (cumulative[i + 1] - cumulative[i]) / width
    values["precision64_minus_precision16"] = (f["hits_64"] - 4 * f["hits_16"]) / 64
    return values


def verify(result, source):
    wanted = {
        (a, m, n, s) for a in ASSAY_IDS for m in MODELS for n in (32, 64) for s in range(1729, 1739)
    }

    def identity(row):
        return tuple(row[k] for k in ("assay_id", "model_id", "initial_sample_size", "run_seed"))

    assert {identity(r) for r in source["rows"]} == wanted and len(source["rows"]) == 120
    expected_summaries = {
        (a, m, n) for a in ASSAY_IDS for m in MODELS for n in (32, 64, "combined")
    }
    assert {
        (r["assay_id"], r["model_id"], r["initial_sample_size"]) for r in result["summaries"]
    } == expected_summaries
    assert len(result["summaries"]) == 18
    intervals, old_endpoints, arm_means = 0, 0, 0
    for summary in result["summaries"]:
        assay, model, initial = (
            summary[k] for k in ("assay_id", "model_id", "initial_sample_size")
        )
        selected = sorted(
            [
                r
                for r in source["rows"]
                if identity(r)[:2] == (assay, model)
                and (initial == "combined" or r["initial_sample_size"] == initial)
            ],
            key=identity,
        )
        assert len(selected) == summary["paired_states"] == (20 if initial == "combined" else 10)
        assert summary["independent_seed_slots"] == 10
        seed = 10800 + MODELS.index(model)
        arms = [[metrics(r, v) for r in selected] for v in ("old", "new")]
        assert set(summary["metrics"]) == set(arms[0][0])
        for metric, record in summary["metrics"].items():
            old, new = ([a[metric] for a in arm] for arm in arms)
            for v, values in (("old", old), ("new", new)):
                assert math.isclose(record[v], math.fsum(values) / len(values), abs_tol=1e-12)
                arm_means += 1
            delta = [b - a for a, b in zip(old, new, strict=True)]
            check_summary(record["change"], bootstrap(delta, seed))
            assert record["change"]["draws"] == 20000 and record["change"]["coverage"] == 0.99375
            assert record["change"]["seed"] == seed
            intervals += 1
            if initial == "combined" and metric.startswith(("hits_", "recall_", "precision_")):
                prior = next(
                    s
                    for s in source["summaries"]
                    if (s["assay_id"], s["model_id"]) == (assay, model)
                )["frontier"][metric]
                check_summary(record["change"], prior["change"])
                for version in ("old", "new"):
                    assert math.isclose(record[version], prior[version], abs_tol=1e-12)
                old_endpoints += 1
    assert len(result["initial_size_consistency"]) == 6
    for item in result["initial_size_consistency"]:
        expected = {}
        for size in (32, 64):
            cell = sorted(
                [
                    r
                    for r in source["rows"]
                    if identity(r)[:3] == (item["assay_id"], item["model_id"], size)
                ],
                key=identity,
            )
            expected[size] = [
                metrics(r, "new")["precision64_minus_precision16"]
                - metrics(r, "old")["precision64_minus_precision16"]
                for r in cell
            ]
            assert item[f"D_initial{size}"] == expected[size]
        assert item["seed_order"] == list(range(1729, 1739))
        check_summary(
            item["D_initial64_minus_initial32"],
            bootstrap(
                np.subtract(expected[64], expected[32]), 10800 + MODELS.index(item["model_id"])
            ),
        )
        intervals += 1
    return {
        "status": "PASS",
        "states": 120,
        "summary_cells": 18,
        "independent_intervals": intervals,
        "old_endpoint_reconstructions": old_endpoints,
        "arm_mean_checks": arm_means,
        "candidate_or_raw_truth_access": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    source_path = root / "docs/results/m5-e3-frontier-20260909/result.json.gz"
    source_raw = source_path.read_bytes()
    assert (
        hashlib.sha256(source_raw).hexdigest()
        == "67e98445f6c4fe3f6fd46a966b2af321122ea4c1cc8371a7eac81fef164715ba"
    )
    terminal = json.loads((args.attempt_dir / "terminal.json").read_text())
    execution = json.loads((args.attempt_dir / "execution.json").read_text())
    raw = (args.attempt_dir / "result.json.gz").read_bytes()
    assert terminal["status"] == "succeeded"
    assert hashlib.sha256(raw).hexdigest() == terminal["artifacts"]["result.json.gz"]
    for relative, sha in execution["source_sha256"].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == sha
    result = verify(json.loads(gzip.decompress(raw)), json.loads(gzip.decompress(source_raw)))
    result.update(
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        result_sha256=hashlib.sha256(raw).hexdigest(),
        input_sha256=hashlib.sha256(source_raw).hexdigest(),
        wall_seconds=time.monotonic() - started,
    )
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
