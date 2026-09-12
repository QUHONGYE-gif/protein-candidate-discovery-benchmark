"""Independently verify new-state endpoints and histogram-based variance diagnostics."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from summarize_m5_e3_prediction import ASSAY_IDS, DENOMINATORS, check_summary

SEEDS = tuple(range(4000, 4060))
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
    cells = np.asarray(values, dtype=float).reshape(-1, 60)
    per_seed = np.array([math.fsum(cells[:, i]) / len(cells) for i in range(60)])
    draws = weights @ per_seed / 60
    return {
        "estimate": math.fsum(per_seed) / 60,
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
    rng = np.random.Generator(np.random.PCG64(20001))
    slots = rng.integers(0, 60, (20000, 60))
    weights = np.zeros((20000, 60), dtype=int)
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
        assert s["states"] == len(rows) and s["seed_slots"] == 60
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
            ) == (20001, 20000, 0.99375)
            intervals += 1
    return {
        "status": "PASS",
        "full_states": 360,
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


def hist_statistics(values, weights):
    n = weights.sum(axis=1)
    mean = (weights @ values) / n
    pos, neg, active = values > 0, values < 0, values != 0

    def gm(mask):
        mass = weights[:, mask].sum(axis=1)
        avg = (weights[:, mask] @ values[mask]) / np.maximum(mass, 1)
        return mass, avg

    cp, mp = gm(pos)
    cn, mn = gm(neg)
    ca, ma = gm(active)
    cz = n - ca
    zero = (ca * (ma - mean) ** 2 + cz * mean**2) / n
    direction = (cp * (mp - ma) ** 2 + cn * (mn - ma) ** 2) / n
    magnitude = np.zeros(len(weights))
    for mask, avg in ((pos, mp), (neg, mn)):
        magnitude += (weights[:, mask] * (values[mask] - avg[:, None]) ** 2).sum(axis=1) / n
    variance = (weights * (values - mean[:, None]) ** 2).sum(axis=1) / n
    return {
        "mean": mean,
        "variance": variance,
        "zero_component": zero,
        "direction_component": direction,
        "magnitude_component": magnitude,
        "direction_minus_zero": direction - zero,
        "within_active_minus_zero": direction + magnitude - zero,
    }


def verify_diagnostic(analysis, old, fresh):
    rows = old["rows"] + [{**r, "cohort": "new60"} for r in fresh["rows"]]
    assert len(rows) == analysis["unique_states"] == 540
    ci_checks, point_checks, binomial_checks = 0, 0, 0
    cells = {}
    for cell in analysis["cells"]:
        assay, cohort, size = cell["assay_id"], cell["cohort"], cell["initial_sample_size"]
        expected_seeds = {
            "original": range(1729, 1739),
            "replication": range(2000, 2020),
            "new60": range(4000, 4060),
            "new_first20": range(4000, 4020),
            "new_middle20": range(4020, 4040),
            "new_last20": range(4040, 4060),
        }[cohort]
        assert cell["seed_ids"] == list(expected_seeds)
        sizes = (32, 64) if size == "combined" else (size,)
        selected = [
            r
            for r in rows
            if r["assay_id"] == assay
            and r["run_seed"] in expected_seeds
            and r["initial_sample_size"] in sizes
        ]
        assert len(selected) == len(expected_seeds) * len(sizes)
        d = np.array(
            [
                math.fsum(
                    r["new"]["hits_16"] - r["old"]["hits_16"]
                    for r in selected
                    if r["run_seed"] == seed
                )
                / len(sizes)
                for seed in expected_seeds
            ]
        )
        assert d.tolist() == cell["seed_effects"]
        assert cell["sign_counts"] == {
            "positive": int((d > 0).sum()),
            "negative": int((d < 0).sum()),
            "zero": int((d == 0).sum()),
        }
        assert cell["both_arms_zero_states"] == sum(
            r["old"]["hits_16"] == r["new"]["hits_16"] == 0 for r in selected
        )
        values, inverse, counts = np.unique(d, return_inverse=True, return_counts=True)
        slots = np.random.Generator(np.random.PCG64(20001)).integers(0, len(d), (20000, len(d)))
        weights = np.zeros((20000, len(values)), dtype=np.int64)
        np.add.at(weights, (np.arange(20000)[:, None], inverse[slots]), 1)
        boot, point = hist_statistics(values, weights), hist_statistics(values, counts[None, :])
        for k, metric in cell["metrics"].items():
            assert math.isclose(metric["estimate"], point[k][0], abs_tol=1e-12)
            assert np.allclose(
                metric["interval"], np.percentile(boot[k], [0.3125, 99.6875]), atol=1e-12, rtol=0
            )
            ci_checks += 1
            point_checks += 1
        for name, numerator, denominator in (
            ("nonzero_fraction", int((d != 0).sum()), len(d)),
            ("positive_given_nonzero", int((d > 0).sum()), int((d != 0).sum())),
        ):
            rec = cell[name]
            if not denominator:
                assert rec["interval"] is rec["estimate"] is None
                continue
            assert math.isclose(rec["estimate"], numerator / denominator)
            for lower, prob in zip((True, False), rec["interval"], strict=True):
                if lower and numerator == 0:
                    assert prob == 0
                elif not lower and numerator == denominator:
                    assert prob == 1
                else:
                    xs = range(numerator, denominator + 1) if lower else range(numerator + 1)
                    tail = math.fsum(
                        math.comb(denominator, x) * prob**x * (1 - prob) ** (denominator - x)
                        for x in xs
                    )
                    assert math.isclose(tail, 0.003125, abs_tol=1e-12)
                binomial_checks += 1
        cells[assay, cohort, size] = d
    assert len(cells) == len(analysis["cells"]) == 54
    for gap in analysis["new60_minus_replication20"]:
        a = cells[gap["assay_id"], "new60", gap["initial_sample_size"]]
        b = cells[gap["assay_id"], "replication", gap["initial_sample_size"]]
        draws = []
        for x, seed in ((a, 20001), (b, 20002)):
            slots = np.random.Generator(np.random.PCG64(seed)).integers(0, len(x), (20000, len(x)))
            w = np.zeros((20000, len(x)), dtype=np.int64)
            np.add.at(w, (np.arange(20000)[:, None], slots), 1)
            draws.append(w @ x / len(x))
        check_summary(
            gap,
            {
                "estimate": float(a.mean() - b.mean()),
                "interval": np.percentile(draws[0] - draws[1], [0.3125, 99.6875]).tolist(),
            },
        )
        ci_checks += 1
    return {
        "diagnostic_cells": len(cells),
        "variance_and_gap_intervals": ci_checks,
        "variance_points": point_checks,
        "binomial_tail_checks": binomial_checks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    prior = []
    for path, expected in (
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
        assert hashlib.sha256(raw).hexdigest() == expected
        prior.append(json.loads(gzip.decompress(raw)))
    for mode in ("full", "smoke"):
        e = json.loads((args.directory / mode / "execution.json").read_text())
        assert (
            e["source_sha256"]
            == hashlib.sha256(
                (root / "scripts/run_m5_e3_state_precision.py").read_bytes()
            ).hexdigest()
        )
        assert (
            e["plan_sha256"]
            == hashlib.sha256(
                (root / "docs/experiments/M5_E3_STATE_PRECISION.md").read_bytes()
            ).hexdigest()
        )
    full, smoke = read(args.directory / "full"), read(args.directory / "smoke")
    result = verify(full, smoke, *prior)
    oldraw = (
        root / "docs/results/m5-e3-state-diagnosis-20260910/full-001/result.json"
    ).read_bytes()
    assert (
        hashlib.sha256(oldraw).hexdigest()
        == "5cbdedb4e35b2595345c992e1ba7d1573a12dd972ce2a30fc090f524d959cdec"
    )
    statroot = args.directory / "statistics"
    terminal = json.loads((statroot / "terminal.json").read_text())
    raw = (statroot / "result.json").read_bytes()
    assert (
        terminal["status"] == "succeeded"
        and hashlib.sha256(raw).hexdigest() == terminal["artifacts"]["result.json"]
    )
    analysis = json.loads(raw)
    assert (
        analysis["source_sha256"]
        == hashlib.sha256(
            (root / "scripts/analyze_m5_e3_state_precision.py").read_bytes()
        ).hexdigest()
    )
    result.update(verify_diagnostic(analysis, json.loads(oldraw), full))
    result["verifier_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
