"""Diagnose state variation using published benign aggregates, without candidate access."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import resource
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from summarize_m5_e3_prediction import ASSAY_IDS, DENOMINATORS

ROOT = Path(__file__).resolve().parents[1]
NOTE = ROOT / "docs/experiments/M5_E3_STATE_DIAGNOSIS.md"
MODEL = "xgboost-cpu-v1"
KS = (8, 16, 32, 64)
INPUTS = {
    "input-intervention-20260909/result.json.gz": (
        "937febfe04c7f45ff490249fdce1f33b0944d54b7a9a9a9939f3a0776d51eb08"
    ),
    "frontier-20260909/result.json.gz": (
        "67e98445f6c4fe3f6fd46a966b2af321122ea4c1cc8371a7eac81fef164715ba"
    ),
    "replication-20260910/full/result.json.gz": (
        "4f8063f48e5671f9d3008751a05fde7072f26feafe932a7722f6c766209481e8"
    ),
}
SEEDS = {"original": tuple(range(1729, 1739)), "replication": tuple(range(2000, 2020))}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    with path.open("x") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        f.write("\n")


def key(r):
    return r["assay_id"], r["initial_sample_size"], r["run_seed"]


def load():
    inputs = []
    for relative, digest in INPUTS.items():
        path = ROOT / ("docs/results/m5-e3-" + relative)
        assert sha(path) == digest, relative
        inputs.append(json.loads(gzip.decompress(path.read_bytes())))
    original, frontier, replication = inputs
    previous = {key(r): r for r in original["rows"] if r["model_id"] == MODEL}
    rows = []
    for cohort, data in (("original", frontier), ("replication", replication)):
        selected = [r for r in data["rows"] if r["model_id"] == MODEL]
        expected = {(a, n, s) for a in ASSAY_IDS for n in (32, 64) for s in SEEDS[cohort]}
        assert len(selected) == len({key(r) for r in selected}) == len(expected)
        assert {key(r) for r in selected} == expected
        for r in selected:
            den = DENOMINATORS[r["assay_id"]][1]
            if cohort == "original":
                old, new = r["old_frontier"], r["new_frontier"]
                assert old["remaining_high"] == new["remaining_high"]
                remaining = old["remaining_high"]
                metrics = []
                for arm, f in (("old", old), ("new", new)):
                    diag = previous[key(r)][arm + "_diagnostics"]
                    metrics.append(
                        {
                            **{f"hits_{k}": f[f"hits_{k}"] for k in KS},
                            **{m: diag[m] for m in ("spearman", "top1_auc", "mae")},
                        }
                    )
                    assert abs(f["auc_all"] - diag["top1_auc"]) < 1e-12
                    assert (
                        f["hits_16"]
                        == previous[key(r)][arm]["branches"]["greedy"]["high_value"]["0.01"][
                            "count"
                        ]
                    )
            else:
                assert r["high_denominator"] == den
                remaining = r["remaining_high"]
                old, new = r["old"], r["new"]
                metrics = [
                    {m: f[m] for m in (*[f"hits_{k}" for k in KS], "spearman", "top1_auc", "mae")}
                    for f in (old, new)
                ]
            for f in (old, new):
                last = 0
                for k in KS:
                    h = f[f"hits_{k}"]
                    assert type(h) is int and last <= h <= min(k, remaining)
                    assert abs(f[f"recall_{k}"] - h / den) < 1e-12
                    assert abs(f[f"precision_{k}"] - h / k) < 1e-12
                    last = h
            observed = den - remaining
            assert type(observed) is int and 0 <= observed <= r["initial_sample_size"]
            rows.append(
                {
                    "cohort": cohort,
                    "assay_id": r["assay_id"],
                    "initial_sample_size": r["initial_sample_size"],
                    "run_seed": r["run_seed"],
                    "observed_high": observed,
                    "old": metrics[0],
                    "new": metrics[1],
                }
            )
        for assay in ASSAY_IDS:
            for seed in SEEDS[cohort]:
                pair = sorted(
                    [
                        r
                        for r in rows
                        if r["cohort"] == cohort
                        and r["assay_id"] == assay
                        and r["run_seed"] == seed
                    ],
                    key=key,
                )
                assert len(pair) == 2
                assert 0 <= pair[1]["observed_high"] - pair[0]["observed_high"] <= 32
    return rows


def slots(n, seed):
    return np.random.Generator(np.random.PCG64(seed)).integers(0, n, (20000, n))


def estimate(values, draws):
    return {
        "estimate": float(np.mean(values)),
        "interval": np.percentile(draws, [0.3125, 99.6875]).tolist(),
        "empirical_degenerate": bool(np.ptp(draws) == 0),
    }


def frequency(values):
    values = np.asarray(values)
    return {
        "positive": int((values > 0).sum()),
        "zero": int((values == 0).sum()),
        "negative": int((values < 0).sum()),
    }


def distribution(a, b):
    d = b - a
    positive = d[d > 0]
    loo = (d.sum() - d) / (len(d) - 1)
    return {
        "old_mean": float(a.mean()),
        "new_mean": float(b.mean()),
        "delta_by_seed": d.tolist(),
        "sign_counts": frequency(d),
        "both_arms_zero": int(((a == 0) & (b == 0)).sum()),
        "largest_positive_share": float(positive.max() / positive.sum()) if len(positive) else None,
        "leave_one_seed_out_mean_range": [float(loo.min()), float(loo.max())],
        "sd_between_seed_effects": float(d.std(ddof=1)),
    }


def decompose(groups):
    if any(g[c]["count"] == 0 for g in groups for c in SEEDS):
        return {"status": "undefined: missing observed stratum in a cohort"}
    composition = within = gap = 0.0
    for g in groups:
        a, b = g["original"], g["replication"]
        p, q, x, y = a["fraction"], b["fraction"], a["mean_delta"], b["mean_delta"]
        composition += (q - p) * (y + x) / 2
        within += (y - x) * (q + p) / 2
        gap += q * y - p * x
    assert abs(composition + within - gap) < 1e-12
    return {
        "status": "descriptive_only",
        "cohort_gap": gap,
        "coverage_composition_term": composition,
        "within_stratum_term": within,
    }


def analyze(rows):
    summaries, contrasts, coverage = [], [], []
    sampled = {c: slots(len(s), 12201 + i) for i, (c, s) in enumerate(SEEDS.items())}
    for assay in ASSAY_IDS:
        for size in (32, 64, "combined"):
            arms, selected = {}, {}
            for c, seeds in SEEDS.items():
                selected[c] = sorted(
                    [
                        r
                        for r in rows
                        if r["cohort"] == c
                        and r["assay_id"] == assay
                        and (size == "combined" or r["initial_sample_size"] == size)
                    ],
                    key=key,
                )
                arms[c] = {
                    arm: {
                        m: np.array([r[arm][m] for r in selected[c]], dtype=float)
                        .reshape(-1, len(seeds))
                        .mean(axis=0)
                        for m in selected[c][0][arm]
                    }
                    for arm in ("old", "new")
                }
                metrics = {}
                for k in KS:
                    a, b = [arms[c][arm][f"hits_{k}"] for arm in ("old", "new")]
                    d = b - a
                    joint = [
                        r
                        for r in selected[c]
                        if r["new"]["spearman"] > r["old"]["spearman"]
                        and r["new"]["top1_auc"] > r["old"]["top1_auc"]
                    ]
                    raw = [r["new"][f"hits_{k}"] - r["old"][f"hits_{k}"] for r in selected[c]]
                    metrics[str(k)] = {
                        **distribution(a, b),
                        "effect": estimate(d, d[sampled[c]].mean(axis=1)),
                        "state_sign_counts": frequency(raw),
                        "rho_and_auc_improved_states": len(joint),
                        "gain_signs_when_both_prediction_metrics_improved": frequency(
                            [r["new"][f"hits_{k}"] - r["old"][f"hits_{k}"] for r in joint]
                        ),
                    }
                summaries.append(
                    {
                        "cohort": c,
                        "assay_id": assay,
                        "initial_sample_size": size,
                        "seed_slots": len(seeds),
                        "seed_order": list(seeds),
                        "metrics": metrics,
                    }
                )
            for k in KS:
                changes = {}
                for arm in ("old", "new"):
                    a, b = [arms[c][arm][f"hits_{k}"] for c in SEEDS]
                    draws = b[sampled["replication"]].mean(axis=1) - a[sampled["original"]].mean(
                        axis=1
                    )
                    changes[arm] = estimate([b.mean() - a.mean()], draws)
                d = {c: arms[c]["new"][f"hits_{k}"] - arms[c]["old"][f"hits_{k}"] for c in SEEDS}
                draws = d["replication"][sampled["replication"]].mean(axis=1) - d["original"][
                    sampled["original"]
                ].mean(axis=1)
                gap = d["replication"].mean() - d["original"].mean()
                assert abs(gap - changes["new"]["estimate"] + changes["old"]["estimate"]) < 1e-12
                contrasts.append(
                    {
                        "assay_id": assay,
                        "initial_sample_size": size,
                        "k": k,
                        "effect_gap": estimate([gap], draws),
                        "arm_cohort_changes": changes,
                    }
                )
                if size == "combined":
                    continue
                groups = []
                for present in (False, True):
                    g = {"has_initial_high": present}
                    for c in SEEDS:
                        subset = [r for r in selected[c] if (r["observed_high"] > 0) == present]
                        ds = [r["new"][f"hits_{k}"] - r["old"][f"hits_{k}"] for r in subset]
                        g[c] = {
                            "count": len(subset),
                            "fraction": len(subset) / len(selected[c]),
                            "mean_delta": float(np.mean(ds)) if ds else None,
                        }
                    groups.append(g)
                coverage.append(
                    {
                        "assay_id": assay,
                        "initial_sample_size": size,
                        "k": k,
                        "groups": groups,
                        "decomposition": decompose(groups),
                    }
                )
    return {
        "protocol": "m5-e3-state-diagnosis-development-v1",
        "rows": rows,
        "completed_states": len(rows),
        "new_fits": 0,
        "new_labels": 0,
        "candidate_or_raw_truth_access": False,
        "summaries": summaries,
        "cohort_contrasts": contrasts,
        "coverage_diagnostics": coverage,
        "bootstrap": {
            "draws": 20000,
            "coverage": 0.99375,
            "seeds": [12201, 12202],
            "unit": "within-assay seed blocks, independent between cohorts",
        },
    }


def self_test():
    # Opposing nested states cancel per seed; independent-row bootstrap would be wrong.
    nested = np.array([[0.0, 2.0, 4.0], [4.0, 2.0, 0.0]]).mean(axis=0)
    assert estimate(nested, nested[slots(3, 1)].mean(axis=1))["interval"] == [2.0, 2.0]
    d = distribution(np.array([0.0, 0.0, 0.0, 0.0]), np.array([4.0, 0.0, 0.0, 0.0]))
    assert d["largest_positive_share"] == 1 and d["leave_one_seed_out_mean_range"] == [0.0, 4 / 3]
    groups = [
        {
            "original": {"count": 8, "fraction": 0.8, "mean_delta": 1.0},
            "replication": {"count": 4, "fraction": 0.4, "mean_delta": 1.0},
        },
        {
            "original": {"count": 2, "fraction": 0.2, "mean_delta": 3.0},
            "replication": {"count": 6, "fraction": 0.6, "mean_delta": 3.0},
        },
    ]
    v = decompose(groups)
    assert abs(v["coverage_composition_term"] - 0.8) < 1e-12 and v["within_stratum_term"] == 0
    a, b = np.zeros(10), np.ones(20)
    draws = b[slots(20, 12202)].mean(axis=1) - a[slots(10, 12201)].mean(axis=1)
    assert estimate([1.0], draws)["interval"] == [1.0, 1.0]
    print("PASS: nested seeds, independent cohort contrast, influence and coverage identity")


def timeout_handler(signum, frame):
    raise TimeoutError("120-second analysis cap reached")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.output_dir is None:
        parser.error("--output-dir required")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    execution = {
        "started_utc": datetime.now(UTC).isoformat(),
        "source_sha256": sha(Path(__file__)),
        "note_sha256": sha(NOTE),
        "inputs": INPUTS,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "limits": {"seconds": 120, "rss_mib": 512, "output_mib": 10},
    }
    write(output / "execution.json", execution)
    ledger = output / "attempts.jsonl"
    with ledger.open("x") as f:
        f.write(json.dumps({"event": "started", **execution}) + "\n")
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(120)
    try:
        data = analyze(load())
        data["inputs"] = INPUTS
        write(output / "result.json", data)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = peak if platform.system() == "Darwin" else peak * 1024
        assert peak <= 512 * 1024**2
        assert sum(p.stat().st_size for p in output.iterdir()) <= 10 * 1024**2
        terminal = {
            "status": "succeeded",
            "wall_seconds": time.monotonic() - started,
            "peak_rss_bytes": peak,
            "result_sha256": sha(output / "result.json"),
        }
    except Exception as e:
        terminal = {"status": "failed", "exception": type(e).__name__, "message": str(e)}
        raise
    finally:
        signal.alarm(0)
        write(output / "terminal.json", terminal)
        with ledger.open("a") as f:
            f.write(json.dumps({"event": "finished", **terminal}) + "\n")
    print(json.dumps(terminal))


if __name__ == "__main__":
    main()
