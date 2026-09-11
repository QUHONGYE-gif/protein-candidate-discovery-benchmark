#!/usr/bin/env python3
"""Independently check aggregate identities and summarize paired region differences."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

SEED = 19001
DRAWS = 20000
QUANTILES = (0.3125, 99.6875)


def load(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def key(row):
    return tuple(
        row[k] for k in ("cohort", "assay_id", "model_id", "initial_sample_size", "run_seed")
    )


def estimate(values, cells):
    if any(v is None for v in values):
        return {
            "defined_states": sum(v is not None for v in values),
            "mean": None,
            "interval": None,
        }
    seeds = sorted({r["run_seed"] for r in cells})
    blocks = []
    for seed in seeds:
        blocks.append(
            np.mean([v for v, r in zip(values, cells, strict=True) if r["run_seed"] == seed])
        )
    blocks = np.asarray(blocks)
    indices = np.random.Generator(np.random.PCG64(SEED)).integers(
        0, len(blocks), (DRAWS, len(blocks))
    )
    # Count-weighted calculation is independent of the original per-draw indexing mean.
    counts = np.array([np.bincount(i, minlength=len(blocks)) for i in indices])
    samples = counts @ blocks / len(blocks)
    bounds = np.percentile(samples, QUANTILES).tolist()
    return {
        "defined_states": len(values),
        "seed_blocks": len(seeds),
        "mean": float(blocks.mean()),
        "interval": bounds,
    }


def metrics(row):
    out = {}
    for name in row["old"]:
        a, b = row["old"][name], row["new"][name]
        out["old_" + name] = a
        out["new_" + name] = b
        out["delta_" + name] = None if a is None or b is None else b - a
    out.update(row["effects"])
    out.update(row["movement"])
    for k, v in row["movement"].items():
        out[k + "_fraction_high"] = v / row["remaining_high"]
    up = row["movement"]["high_up"]
    out["outside_share_of_up"] = row["movement"]["up_outside_both"] / up if up else None
    for region, counts in row["regions"].items():
        out.update({region + "_" + k: v for k, v in counts.items()})
    return out


def verify(directory, repository):
    full = load(directory / "full/result.json.gz")
    smoke = load(directory / "smoke/result.json.gz")
    assert full["completed_states"] == len(full["rows"]) == 240
    assert smoke["completed_states"] == len(smoke["rows"]) == 9
    rows = {key(r): r for r in full["rows"]}
    assert len(rows) == 240
    input_data = load(repository / "docs/results/m5-e3-input-intervention-20260909/result.json.gz")
    replica = load(repository / "docs/results/m5-e3-replication-20260910/full/result.json.gz")
    prior = {}
    for cohort, data in (("original", input_data), ("replication", replica)):
        for r in data["rows"]:
            prior[
                (
                    cohort,
                    *(r[k] for k in ("assay_id", "model_id", "initial_sample_size", "run_seed")),
                )
            ] = r
    assert set(rows) == set(prior)
    checks = 0
    for k, r in rows.items():
        n, h = r["remaining_count"], r["remaining_high"]
        assert 0 < h <= r["high_denominator"] and 0 < h < n
        m = r["movement"]
        assert m["high_up"] + m["high_down"] + m["high_same"] == h
        assert m["up_outside_both"] + m["up_inside_either"] == m["high_up"]
        assert m["retained16"] + m["exited16"] == r["old"]["hits16"]
        assert m["retained16"] + m["entered16"] == r["new"]["hits16"]
        assert m["retained16"] + m["entered16"] + m["exited16"] + m["outside_both"] == h
        assert m["up_outside_both"] + m["down_outside_both"] <= m["outside_both"]
        checks += 7
        for label, counts in r["regions"].items():
            assert counts["head_negative"] + counts["other_negative"] == n - h
            assert counts["candidate_count"] == counts["head_negative"] + counts["high_count"]
            assert (
                (16 <= counts["candidate_count"] <= 32)
                if label == "union"
                else counts["candidate_count"] == 16
            )
            for version in ("old", "new"):
                a = r[version][label + "_head_auc"]
                b = r[version][label + "_other_auc"]
                if a is not None and b is not None:
                    assert 0 <= a <= 1 and 0 <= b <= 1
                    reconstructed = (a * counts["head_negative"] + b * counts["other_negative"]) / (
                        n - h
                    )
                    assert abs(reconstructed - r[version]["all_auc"]) < 1e-12
                    checks += 2
            vals = [
                r[v][label + "_" + region + "_auc"]
                for v in ("old", "new")
                for region in ("head", "other")
            ]
            d = None if any(x is None for x in vals) else vals[3] - vals[2] - vals[1] + vals[0]
            actual = r["effects"][label + "_other_minus_head"]
            assert d is None and actual is None or abs(d - actual) < 1e-12
            checks += 4
        for version in ("old", "new"):
            if k[0] == "original":
                a = prior[k][version + "_diagnostics"]["top1_auc"]
                hits = prior[k][version]["branches"]["greedy"]["high_value"]["0.01"]["count"]
            else:
                a, hits = prior[k][version]["top1_auc"], prior[k][version]["hits_16"]
            assert abs(r[version]["all_auc"] - a) < 1e-12
            assert r[version]["hits16"] == hits
            checks += 2
    for r in smoke["rows"]:
        assert r == rows[key(r)]
        checks += 1
    for mode in ("smoke", "full"):
        terminal = load(directory / mode / "terminal.json")
        assert terminal["status"] == "succeeded"
        assert (
            hashlib.sha256((directory / mode / "result.json.gz").read_bytes()).hexdigest()
            == terminal["result_sha256"]
        )
        progress = [
            json.loads(x) for x in (directory / mode / "progress.jsonl").read_text().splitlines()
        ]
        lock = next(
            i
            for i, e in enumerate(progress)
            if e["event"] == "all_orders_locked_all_workers_exited"
        )
        evaluators = [i for i, e in enumerate(progress) if e["event"] == "evaluator_constructed"]
        assert len(evaluators) == 3 and min(evaluators) > lock
        assert sum(e["event"] == "assay_worker_exited" for e in progress[:lock]) == 3
        checks += 4
    summaries = []
    groups = sorted({k[:3] for k in rows})
    for group in groups:
        for size in ("combined", 32, 64):
            cells = sorted(
                [
                    r
                    for k, r in rows.items()
                    if k[:3] == group and (size == "combined" or k[3] == size)
                ],
                key=lambda r: (r["initial_sample_size"], r["run_seed"]),
            )
            expected_seeds = 10 if group[0] == "original" else 20
            assert len(cells) == expected_seeds * (2 if size == "combined" else 1)
            values = [metrics(r) for r in cells]
            summary = {
                "cohort": group[0],
                "assay_id": group[1],
                "model_id": group[2],
                "initial_sample_size": size,
                "states": len(cells),
                "metrics": {},
            }
            for name in values[0]:
                summary["metrics"][name] = estimate([v[name] for v in values], cells)
            summaries.append(summary)
    return {
        "verification_checks": checks,
        "source": "independent aggregate-only reconstruction",
        "numpy_version": np.__version__,
        "bootstrap_seed": SEED,
        "draws": DRAWS,
        "quantiles": QUANTILES,
        "summaries": summaries,
        "limits": [
            "not independent raw-array reconstruction",
            "seed is within-assay variability",
            "prediction-defined regions",
            "no equivalence or upstream causal proof",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--repository", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = verify(args.directory, args.repository)
    with args.output.open("x") as f:
        json.dump(result, f, sort_keys=True, indent=2, allow_nan=False)
        f.write("\n")
    print(
        "PASS:",
        result["verification_checks"],
        "aggregate checks;",
        len(result["summaries"]),
        "cells",
    )


if __name__ == "__main__":
    main()
