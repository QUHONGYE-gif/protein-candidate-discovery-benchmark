"""Independently check compact composition results; never read candidate truth."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

ASSAYS = {
    "PABP_YEAST_Melamed_2013": 378,
    "HSP82_YEAST_Flynn_2019": 133,
    "HXK4_HUMAN_Gersing_2022_activity": 86,
}
ARMS = ("random", "upper", "spread")
CONTRASTS = (("random", "upper"), ("random", "spread"), ("spread", "upper"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a, b):
    if b is None:
        assert a is None
    else:
        assert a is not None and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12), (a, b)


def read(folder, root):
    terminal = json.loads((folder / "terminal.json").read_text())
    assert terminal["status"] == "succeeded" and terminal["failure_class"] is None
    for name, sha in terminal["artifacts"].items():
        assert digest(folder / name) == sha
    ex = json.loads((folder / "execution.json").read_text())
    for key, name in (
        ("source_sha256", "scripts/run_m5_e3_information_composition.py"),
        ("plan_sha256", "docs/experiments/M5_E3_INFORMATION_COMPOSITION.md"),
        ("helper_sha256", "scripts/run_m5_e3_prediction.py"),
        ("coverage_helper_sha256", "scripts/run_m5_e3_fixed_count_coverage.py"),
    ):
        assert ex[key] == digest(root / name)
    assert (ex["training_count"], ex["allowed_observation_count"], ex["main_label_cost"]) == (
        32,
        64,
        80,
    )
    events = [json.loads(line) for line in (folder / "progress.jsonl").read_text().splitlines()]
    n = 1 if ex["mode"] == "smoke" else 10
    for assay in ASSAYS:
        own = [e for e in events if e.get("assay_id") == assay]
        names = [e["event"] for e in own]
        assert names == ["predictions_and_selection_saved"] * n + [
            "fit_workers_exited",
            "assay_scored",
        ]
    return json.loads(gzip.decompress((folder / "result.json.gz").read_bytes()))


def key(row):
    return row["assay_id"], row["run_seed"]


def verify(full, smoke, prior):
    checks = baseline = means = intervals = undefined = 0
    original = {
        key(r): r["arms"]["new32"] for r in prior["rows"] if r["model_id"] == "xgboost-cpu-v1"
    }
    for data, seeds in ((full, range(1729, 1739)), (smoke, (1729,))):
        expected = {(a, s) for a in ASSAYS for s in seeds}
        assert len(data["rows"]) == data["completed_states"] == len(expected)
        assert {key(r) for r in data["rows"]} == expected
        assert data["point_fits"] == 3 * len(expected) and data["new_measurements"] == 0
        for r in data["rows"]:
            assert r["high_denominator"] == ASSAYS[r["assay_id"]]
            assert r["baseline_reconstruction"] == "EXACT"
            assert (r["training_count"], r["allowed_observation_count"], r["label_cost"]) == (
                32,
                64,
                80,
            )
            assert set(r["metrics"]) == set(r["training_info"]) == set(ARMS)
            assert 64 <= r["frontier_count"] <= 192
            assert 0 <= r["frontier_high_count"] <= r["frontier_count"]
            for arm in ARMS:
                m, info = r["metrics"][arm], r["training_info"][arm]
                previous = 0
                for k in (8, 16, 32, 64):
                    hits = m[f"hits_{k}"]
                    assert type(hits) is int and previous <= hits <= k
                    previous = hits
                    checks += 1
                assert m["greedy_hits"] == m["hits_16"] == m["top16_high"]
                assert m["top16_high"] + m["top16_near"] + m["top16_far"] == 16
                close(m["greedy_recall"], m["hits_16"] / r["high_denominator"])
                assert (m["common_frontier_auc"] is None) == (
                    r["frontier_high_count"] in (0, r["frontier_count"])
                )
                assert 0 <= info["reservoir_upper_quartile_fraction"] <= 1
                assert 1 <= info["unique_labels"] <= 32
                assert info["label_range"] >= info["label_iqr"] >= 0
                if m["hits_16"]:
                    assert 1 <= m["first_high_rank"] <= 16
                else:
                    assert m["first_high_rank"] is None or m["first_high_rank"] > 16
                checks += 8
            info = r["training_info"]
            assert info["upper"]["label_mean"] >= info["random"]["label_mean"]
            assert info["upper"]["reservoir_upper_quartile_fraction"] >= 0.5
            assert info["spread"]["label_range"] >= info["random"]["label_range"]
            checks += 3
            for metric, expected_value in original[key(r)].items():
                if metric in r["metrics"]["random"]:
                    close(r["metrics"]["random"][metric], expected_value)
                    baseline += 1
    full_keyed = {key(r): r for r in full["rows"]}
    for r in smoke["rows"]:
        # File container timestamps may vary; compare actual compact scientific content.
        for section in ("metrics", "training_info", "training_overlaps"):
            assert r[section] == full_keyed[key(r)][section]
    rng = np.random.Generator(np.random.PCG64(12301))
    slots = rng.integers(0, 10, (20000, 10))
    weights = np.zeros((20000, 10), dtype=int)
    np.add.at(weights, (np.arange(20000)[:, None], slots), 1)
    assert len(full["summaries"]) == 9
    assert {(s["assay_id"], s["before"], s["after"]) for s in full["summaries"]} == {
        (a, x, y) for a in ASSAYS for x, y in CONTRASTS
    }
    for summary in full["summaries"]:
        rows = sorted([r for r in full["rows"] if r["assay_id"] == summary["assay_id"]], key=key)
        x, y = summary["before"], summary["after"]
        for section in ("metrics", "training_info"):
            assert set(summary[section]) == set(rows[0][section][x])
            for metric, item in summary[section].items():
                pairs = [(r[section][x][metric], r[section][y][metric]) for r in rows]
                defined = sum(a is not None and b is not None for a, b in pairs)
                assert item["defined_pairs"] == defined
                if defined != 10:
                    assert set(item) == {"defined_pairs"}
                    undefined += 1
                    continue
                close(item["before"], math.fsum(a for a, b in pairs) / 10)
                close(item["after"], math.fsum(b for a, b in pairs) / 10)
                delta = [b - a for a, b in pairs]
                change = item["change"]
                close(change["estimate"], math.fsum(delta) / 10)
                boot = weights @ np.asarray(delta) / 10
                for actual, expected_value in zip(
                    change["interval"], np.percentile(boot, [0.3125, 99.6875]), strict=True
                ):
                    close(actual, expected_value)
                assert (change["seed"], change["draws"], change["coverage"]) == (
                    12301,
                    20000,
                    0.99375,
                )
                means += 2
                intervals += 1
    return {
        "status": "PASS",
        "full_states": 30,
        "smoke_states": 3,
        "count_rate_and_intervention_checks": checks,
        "baseline_aggregate_checks": baseline,
        "smoke_full_exact_scientific_states": 3,
        "independent_intervals": intervals,
        "arm_mean_checks": means,
        "undefined_summary_metrics": undefined,
        "raw_truth_or_candidate_access": False,
        "boundary": "compact arithmetic and identities; not independent raw-data reconstruction",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    prior_path = root / "docs/results/m5-e3-training-information-20260909/result.json.gz"
    assert digest(prior_path) == "ba0de452db9012fe0b9960168ea5d9b5422eafbd0c2cd7a456170f327b5dcded"
    result = verify(
        read(args.directory / "full", root),
        read(args.directory / "smoke", root),
        json.loads(gzip.decompress(prior_path.read_bytes())),
    )
    result.update(
        source_sha256=digest(Path(__file__)),
        full_sha256=digest(args.directory / "full/result.json.gz"),
        wall_seconds=time.monotonic() - started,
    )
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True, allow_nan=False)
        f.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
