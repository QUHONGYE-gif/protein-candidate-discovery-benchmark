"""Aggregate-only, fixed sparse-change and direction-variation diagnostic."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import resource
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

OLD = "docs/results/m5-e3-state-diagnosis-20260910/full-001/result.json"
OLD_SHA = "5cbdedb4e35b2595345c992e1ba7d1573a12dd972ce2a30fc090f524d959cdec"
ASSAYS = (
    "PABP_YEAST_Melamed_2013",
    "HSP82_YEAST_Flynn_2019",
    "HXK4_HUMAN_Gersing_2022_activity",
)
DENOMINATORS = dict(zip(ASSAYS, (378, 133, 86), strict=True))
SEEDS = {
    "original": tuple(range(1729, 1739)),
    "replication": tuple(range(2000, 2020)),
    "new60": tuple(range(4000, 4060)),
    "new_first20": tuple(range(4000, 4020)),
    "new_middle20": tuple(range(4020, 4040)),
    "new_last20": tuple(range(4040, 4060)),
}
QUANTILES = (0.3125, 99.6875)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open("x") as f:
        json.dump(value, f, sort_keys=True, indent=2, allow_nan=False)
        f.write("\n")


def clopper_pearson(k, n):
    if n == 0:
        return None
    assert 0 <= k <= n
    alpha = 0.00625

    def tail(p, lo, hi):
        return sum(math.comb(n, x) * p**x * (1 - p) ** (n - x) for x in range(lo, hi))

    bounds = []
    for lower in (True, False):
        if lower and k == 0:
            bounds.append(0.0)
            continue
        if not lower and k == n:
            bounds.append(1.0)
            continue
        a, b = 0.0, 1.0
        for _ in range(70):
            m = (a + b) / 2
            val = tail(m, k, n + 1) if lower else tail(m, 0, k + 1)
            if (val < alpha / 2) == lower:
                a = m
            else:
                b = m
        bounds.append((a + b) / 2)
    return bounds


def components(d):
    """Population variance identities, vectorized along the last seed axis."""
    d = np.asarray(d, dtype=float)
    n = d.shape[-1]
    pos, neg = d > 0, d < 0
    np_, nn = pos.sum(axis=-1), neg.sum(axis=-1)
    active = np_ + nn
    q = active / n
    p = np_ / np.maximum(active, 1)
    mp = np.where(pos, d, 0).sum(axis=-1) / np.maximum(np_, 1)
    mn = np.where(neg, d, 0).sum(axis=-1) / np.maximum(nn, 1)
    vp = np.where(pos, (d - mp[..., None]) ** 2, 0).sum(axis=-1) / np.maximum(np_, 1)
    vn = np.where(neg, (d - mn[..., None]) ** 2, 0).sum(axis=-1) / np.maximum(nn, 1)
    ma = d.sum(axis=-1) / np.maximum(active, 1)
    vz = q * (1 - q) * ma**2
    vd = q * p * (1 - p) * (mp - mn) ** 2
    vm = q * (p * vp + (1 - p) * vn)
    return {
        "mean": d.mean(axis=-1),
        "variance": d.var(axis=-1),
        "zero_component": vz,
        "direction_component": vd,
        "magnitude_component": vm,
        "direction_minus_zero": vd - vz,
        "within_active_minus_zero": vd + vm - vz,
    }


def interval(samples):
    return np.percentile(samples, QUANTILES).tolist()


def selftest():
    for values in ([0] * 10, [2] * 10, [-2] * 10, [0, 0, 1, 2], [-3, -1, 0, 1, 4]):
        d = np.array(values, dtype=float)
        c = components(d)
        assert math.isclose(
            c["variance"],
            c["zero_component"] + c["direction_component"] + c["magnitude_component"],
            abs_tol=1e-12,
        )
        mirrored = components(-d)
        for key in c:
            if key != "mean":
                assert math.isclose(c[key], mirrored[key], abs_tol=1e-12)
    c = components(np.array([-1, 0, 0, 1]))
    assert c["direction_component"] == 0.5 and c["zero_component"] == 0
    assert math.isclose(clopper_pearson(0, 10)[1], 1 - 0.003125 ** (1 / 10))
    assert math.isclose(clopper_pearson(10, 10)[0], 0.003125 ** (1 / 10))
    for k in range(11):
        a, b = clopper_pearson(k, 10)
        lo, hi = clopper_pearson(10 - k, 10)
        assert math.isclose(a, 1 - hi, abs_tol=1e-12)
        assert math.isclose(b, 1 - lo, abs_tol=1e-12)
    assert clopper_pearson(0, 0) is None
    print("PASS variance/empty-group/sign-symmetry/exact-binomial synthetic checks")


def cell(rows, assay, cohort, size):
    seeds = SEEDS[cohort]
    sizes = (32, 64) if size == "combined" else (size,)
    lookup = {(r["run_seed"], r["initial_sample_size"]): r for r in rows}
    selected = [[lookup[(s, z)] for s in seeds] for z in sizes]
    old = np.array([[r["old"]["hits_16"] for r in a] for a in selected], dtype=float)
    new = np.array([[r["new"]["hits_16"] for r in a] for a in selected], dtype=float)
    d = (new - old).mean(axis=0)
    n = len(d)
    slots = np.random.Generator(np.random.PCG64(20001)).integers(0, n, (20000, n))
    point, boot = components(d), components(d[slots])
    if not np.allclose(
        boot["variance"],
        boot["zero_component"] + boot["direction_component"] + boot["magnitude_component"],
        rtol=1e-12,
        atol=1e-12,
    ):
        raise ValueError("bootstrap variance identity mismatch")
    npos, nneg = int((d > 0).sum()), int((d < 0).sum())
    active = npos + nneg
    gross = float(np.abs(d).sum())
    return {
        "assay_id": assay,
        "cohort": cohort,
        "initial_sample_size": size,
        "seed_slots": n,
        "states": old.size,
        "sign_counts": {"positive": npos, "negative": nneg, "zero": n - active},
        "both_arms_zero_states": int(((old == 0) & (new == 0)).sum()),
        "old_mean_hits": float(old.mean()),
        "new_mean_hits": float(new.mean()),
        "nonzero_fraction": {"estimate": active / n, "interval": clopper_pearson(active, n)},
        "positive_given_nonzero": {
            "estimate": npos / active if active else None,
            "interval": clopper_pearson(npos, active),
            "denominator": active,
        },
        "nonzero_mean": float(d[d != 0].mean()) if active else None,
        "positive_mean": float(d[d > 0].mean()) if npos else None,
        "negative_mean": float(d[d < 0].mean()) if nneg else None,
        "net_over_gross": float(d.sum() / gross) if gross else None,
        "sample_sd": float(d.std(ddof=1)),
        "standard_error": float(d.std(ddof=1) / math.sqrt(n)),
        "metrics": {
            k: {"estimate": float(v), "interval": interval(boot[k])} for k, v in point.items()
        },
        "seed_effects": d.tolist(),
        "seed_ids": list(seeds),
        "variance_component_fraction": {
            k: float(point[k] / point["variance"]) if point["variance"] else None
            for k in ("zero_component", "direction_component", "magnitude_component")
        },
    }


def analyze(args):
    oldpath = args.repository / OLD
    assert sha(oldpath) == OLD_SHA and sha(args.full) == args.full_sha
    old = json.loads(oldpath.read_text())
    fresh = json.loads(gzip.decompress(args.full.read_bytes()))
    assert fresh["protocol"] == "m5-e3-state-precision-development-v1"
    assert fresh["completed_states"] == 360 and fresh["point_fits"] == 720
    assert old["completed_states"] == 180
    newrows = [{**r, "cohort": "new60"} for r in fresh["rows"]]
    allrows = old["rows"] + newrows
    identities = [
        (r["assay_id"], r["cohort"], r["initial_sample_size"], r["run_seed"]) for r in allrows
    ]
    assert len(set(identities)) == len(allrows) == 540
    for r in allrows:
        assert r["assay_id"] in ASSAYS and r["initial_sample_size"] in (32, 64)
        for arm in ("old", "new"):
            assert 0 <= r[arm]["hits_16"] <= 16
        if r["cohort"] == "new60":
            assert r["model_id"] == "xgboost-cpu-v1"
            assert r["high_denominator"] == DENOMINATORS[r["assay_id"]]
            for arm in ("old", "new"):
                for k in (8, 16, 32, 64):
                    m = r[arm]
                    assert 0 <= m[f"hits_{k}"] <= k
                    assert math.isclose(m[f"recall_{k}"], m[f"hits_{k}"] / r["high_denominator"])
    cells = []
    for assay in ASSAYS:
        for cohort in SEEDS:
            rows = [
                r
                for r in allrows
                if r["assay_id"] == assay
                and r["cohort"] == ("new60" if cohort.startswith("new") else cohort)
            ]
            expected = SEEDS["new60"] if cohort.startswith("new") else SEEDS[cohort]
            assert sorted((r["initial_sample_size"], r["run_seed"]) for r in rows) == [
                (z, s) for z in (32, 64) for s in expected
            ]
            for size in (32, 64, "combined"):
                cells.append(cell(rows, assay, cohort, size))
    bykey = {(c["assay_id"], c["cohort"], c["initial_sample_size"]): c for c in cells}
    gaps = []
    for assay in ASSAYS:
        for size in (32, 64, "combined"):
            a = np.array(bykey[(assay, "new60", size)]["seed_effects"])
            b = np.array(bykey[(assay, "replication", size)]["seed_effects"])
            ia = np.random.Generator(np.random.PCG64(20001)).integers(0, len(a), (20000, len(a)))
            ib = np.random.Generator(np.random.PCG64(20002)).integers(0, len(b), (20000, len(b)))
            gaps.append(
                {
                    "assay_id": assay,
                    "initial_sample_size": size,
                    "estimate": float(a.mean() - b.mean()),
                    "interval": interval(a[ia].mean(axis=1) - b[ib].mean(axis=1)),
                }
            )
    # The new execution's main mean/CI must reproduce, not silently change the endpoint.
    for s in fresh["summaries"]:
        c = bykey[(s["assay_id"], "new60", s["initial_sample_size"])]
        assert np.allclose(
            s["metrics"]["hits_16"]["change"]["interval"], c["metrics"]["mean"]["interval"]
        )
        assert math.isclose(
            s["metrics"]["hits_16"]["change"]["estimate"],
            c["metrics"]["mean"]["estimate"],
            abs_tol=1e-12,
        )
    write(
        args.output_dir / "result.json",
        {
            "protocol": "m5-e3-state-precision-analysis-v1",
            "inputs": {OLD: OLD_SHA, "new_full_result": args.full_sha},
            "source_sha256": sha(__file__),
            "plan_sha256": sha(args.plan),
            "new_fits_in_this_aggregation": 0,
            "candidate_or_raw_truth_access": False,
            "unique_states": len(allrows),
            "bootstrap": {
                "draws": 20000,
                "seed": 20001,
                "old_gap_seed": 20002,
                "quantiles": QUANTILES,
            },
            "cells": cells,
            "new60_minus_replication20": gaps,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--full", type=Path)
    parser.add_argument("--full-sha")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        return
    assert all((args.full, args.full_sha, args.plan, args.output_dir))
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    ledger = args.output_dir / "attempts.jsonl"

    def event(status, **values):
        with ledger.open("a") as f:
            f.write(
                json.dumps({"status": status, "time": datetime.now(UTC).isoformat(), **values})
                + "\n"
            )

    event("started", source_sha256=sha(__file__), plan_sha256=sha(args.plan))
    status = "failed"
    try:
        analyze(args)
        status = "succeeded"
    finally:
        event(status, wall_seconds=time.monotonic() - started)
        write(
            args.output_dir / "terminal.json",
            {
                "status": status,
                "wall_seconds": time.monotonic() - started,
                "raw_ru_maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "artifacts": {p.name: sha(p) for p in args.output_dir.iterdir() if p.is_file()},
            },
        )
    print("PASS fixed state-precision aggregation")


if __name__ == "__main__":
    main()
