"""Independent compact arithmetic checks; never imports the experiment runner."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
import numpy as np  # noqa: E402

COUNTS = {}
CHECKS = {}


def check(condition, name):
    if not condition:
        raise ValueError(name)
    CHECKS[name] = CHECKS.get(name, 0) + 1


def close(a, b, name):
    check(bool(np.allclose(a, b, rtol=1e-10, atol=1e-10)), name)


def read(path):
    b = path.read_bytes()
    return json.loads(gzip.decompress(b) if path.suffix == ".gz" else b)


def resample(a, seed=14001):
    a = np.asarray(a, dtype=float)
    n = len(a)
    key = (n, seed)
    if key not in COUNTS:
        slots = np.random.Generator(np.random.PCG64(seed)).integers(0, n, (20000, n))
        counts = np.zeros((20000, n), dtype=int)
        np.add.at(counts, (np.arange(20000)[:, None], slots), 1)
        COUNTS[key] = counts
    return COUNTS[key] @ a / n


def interval(a, actual):
    close(np.mean(a), actual["estimate"], "means")
    close(
        np.percentile(resample(a), [0.3125, 99.6875]),
        actual["interval"],
        "independent_weighted_intervals",
    )
    check(
        actual["n"] == len(a)
        and actual["seed"] == 14001
        and actual["draws"] == 20000
        and actual["coverage"] == 0.99375,
        "interval_units",
    )


def group_gap(a, b, actual):
    rng = np.random.Generator(np.random.PCG64(14002))
    draws = []
    for v in (a, b):
        v = np.asarray(v, dtype=float)
        n = len(v)
        slots = rng.integers(0, n, (20000, n))
        counts = np.zeros((20000, n), dtype=int)
        np.add.at(counts, (np.arange(20000)[:, None], slots), 1)
        draws.append(counts @ v / n)
    close(np.mean(a) - np.mean(b), actual["estimate"], "independent_group_mean")
    close(
        np.quantile(draws[0] - draws[1], [0.003125, 0.996875]),
        actual["interval"],
        "independent_group_interval",
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    base = a.directory
    source_root = Path(__file__).resolve().parents[1]
    prior = read(
        source_root / "docs/results/m5-e3-generalization-precision-20260910/full/result.json.gz"
    )
    refs = {
        r["run_seed"]: r
        for r in prior["rows"]
        if r["assay_id"] == "HXK4_HUMAN_Gersing_2022_activity" and r["initial_sample_size"] == 64
    }
    full = read(base / "full/result.json.gz")
    smoke = read(base / "smoke/result.json.gz")
    rows = full["rows"]
    check(
        sorted(r["run_seed"] for r in rows) == list(range(2000, 2020)) + list(range(3000, 3040)),
        "matrix",
    )
    for mode in ("smoke", "full"):
        d = base / mode
        t = read(d / "terminal.json")
        ex = read(d / "execution.json")
        check(t["status"] == "succeeded", "attempt_status")
        for name in ("execution.json", "progress.jsonl", "result.json.gz"):
            check(
                hashlib.sha256((d / name).read_bytes()).hexdigest() == t["artifacts"][name],
                "terminal_identities",
            )
        check(
            ex["source_sha256"]
            == hashlib.sha256(
                (source_root / "scripts/run_m5_e3_hxk4_followup.py").read_bytes()
            ).hexdigest(),
            "source_identity",
        )
        check(
            ex["plan_sha256"]
            == hashlib.sha256(
                (source_root / "docs/experiments/M5_E3_HXK4_FOLLOWUP.md").read_bytes()
            ).hexdigest(),
            "plan_identity",
        )
        events = [json.loads(line) for line in (d / "progress.jsonl").read_text().splitlines()]
        kinds = [r["event"] for r in events]
        check(
            kinds.index("workers_exited") < kinds.index("scoring_complete"),
            "lock_exit_before_score",
        )
        check(
            max(i for i, v in enumerate(kinds) if v == "predictions_saved")
            < kinds.index("workers_exited"),
            "predictions_before_exit",
        )
        ids = ex["initial_state_set_sha256"]
        check(len(ids) == 60 and len(set(ids.values())) == 60, "unique_initial_sets")
    for r in rows + smoke["rows"]:
        c, f, flow = r["cells"], r["frontier"], r["flow"]
        check(
            r["high_denominator"] == 86
            and r["label_cost"] == 80
            and r["initial_sample_size"] == 64,
            "metric_units",
        )
        close(
            f["pool_component"] + f["ranking_component"],
            c["rr"] - c["xx"],
            "exact_total_decomposition",
        )
        close(f["pool_component"], (c["rx"] + c["rr"] - c["xx"] - c["xr"]) / 2, "pool_formula")
        close(f["ranking_component"], (c["xr"] + c["rr"] - c["xx"] - c["rx"]) / 2, "rank_formula")
        close(f["interaction"], c["rr"] - c["rx"] - c["xr"] + c["xx"], "interaction_formula")
        for prefix in ("x", "r"):
            arm = "xgb" if prefix == "x" else "ridge"
            m = r["metrics"][arm]
            check(
                f[arm + "_pool_oracle_bound"] == min(16, f[arm + "_pool_high"]), "diagnostic_bound"
            )
            for rank in ("x", "r"):
                check(0 <= c[prefix + rank] <= f[arm + "_pool_oracle_bound"], "cross_count_bound")
            check(c[prefix * 2] == m["hits_16"], "primary_cross_equivalence")
            check(m["top16_high"] + m["top16_near"] + m["top16_far"] == 16, "top16_partition")
            for k in (8, 16, 32, 64):
                close(m[f"hits_{k}"] / 86, m[f"recall_{k}"], "recall_denominator")
                close(m[f"hits_{k}"] / k, m[f"precision_{k}"], "precision_denominator")
            close(m["greedy_recall"], m["recall_16"], "greedy_recall")
            gained = sum(
                flow[arm + "_exclusive_hits_" + b] for b in ("17_64", "65_256", "beyond256")
            )
            count = sum(
                flow[arm + "_exclusive_count_" + b] for b in ("17_64", "65_256", "beyond256")
            )
            check(
                gained + flow["shared_high"] == m["hits_16"] and count + flow["overlap"] == 16,
                "rank_flow_partition",
            )
        if r["run_seed"] in refs:
            old = refs[r["run_seed"]]
            for arm in ("xgb", "ridge"):
                for k, v in old["metrics"][arm].items():
                    got = r["metrics"][arm][k]
                    if v is None:
                        check(got is None, "old_missing")
                    else:
                        close(got, v, "historical_metrics")
            check(
                r["allowed_observation_sha256"] == old["allowed_observation_sha256"],
                "old_observation_identity",
            )
    for s in full["summaries"]:
        seeds = {
            "old20": range(2000, 2020),
            "new40": range(3000, 3040),
            "new_first20": range(3000, 3020),
            "new_second20": range(3020, 3040),
        }[s["cohort"]]
        rr = sorted([r for r in rows if r["run_seed"] in seeds], key=lambda r: r["run_seed"])
        check(s["states"] == len(rr), "summary_units")
        for k, v in s["paired_metrics"].items():
            pairs = [(r["metrics"]["xgb"][k], r["metrics"]["ridge"][k]) for r in rr]
            defined = sum(x is not None and y is not None for x, y in pairs)
            check(defined == v["defined_pairs"], "missing_pairs")
            if defined == len(rr):
                close(np.mean([x for x, y in pairs]), v["xgb"], "arm_means")
                close(np.mean([y for x, y in pairs]), v["ridge"], "arm_means")
                interval([y - x for x, y in pairs], v["change"])
            else:
                check("change" not in v, "no_complete_case_interval")
        for section in ("cells", "frontier", "flow"):
            for k, v in s[section].items():
                interval([r[section][k] for r in rr], v)
        d = np.array([r["cells"]["rr"] - r["cells"]["xx"] for r in rr])
        sp = s["sparse"]
        close(d.mean(), sp["mean"], "sparse_mean")
        close(d.std(ddof=1), sp["sample_sd"], "state_sd")
        loo = [np.delete(d, i).mean() for i in range(len(d))]
        close([min(loo), max(loo)], sp["leave_one_out_range"], "independent_loo")
        x = np.array([r["cells"]["xx"] for r in rr])
        y = np.array([r["cells"]["rr"] for r in rr])
        for key, mask in [
            ("both_zero", (x == 0) & (y == 0)),
            ("ridge_only_hits", (x == 0) & (y > 0)),
            ("xgb_only_hits", (x > 0) & (y == 0)),
            ("both_hit", (x > 0) & (y > 0)),
            ("positive", d > 0),
            ("negative", d < 0),
            ("zero", d == 0),
        ]:
            check(sp[key] == int(mask.sum()), "state_counts")
        pos = sorted(np.flatnonzero(d > 0), key=lambda i: -d[i])
        for item in sp["largest_positive_states"]:
            indices = pos[: item["k"]]
            close(sum(d[i] for i in indices), item["sum"], "influence_sum")
            close(np.delete(d, indices).mean(), item["mean_without"], "influence_mean")
        close(np.maximum(d, 0).mean(), sp["positive_part_mean"], "signed_parts")
        close(np.minimum(d, 0).mean(), sp["negative_part_mean"], "signed_parts")
        groups = {
            str(k): [
                float(v) for v, r in zip(d, rr, strict=True) if bool(r["initial_high_count"]) == k
            ]
            for k in (False, True)
        }
        for k, v in groups.items():
            check(len(v) == sp["initial_high_groups"][k]["n"], "group_support")
            if v:
                close(np.mean(v), sp["initial_high_groups"][k]["mean"], "group_mean")
        if min(map(len, groups.values())) >= 8:
            group_gap(groups["True"], groups["False"], sp["initial_high_interaction"])
        else:
            check(sp["initial_high_interaction"] is None, "unsupported_group_no_interval")
    new = [r["cells"]["rr"] - r["cells"]["xx"] for r in rows if r["cohort"] == "new"]
    old = [r["cells"]["rr"] - r["cells"]["xx"] for r in rows if r["cohort"] == "old"]
    group_gap(new, old, full["new_minus_old"])
    report = {
        "status": "PASS",
        "checks": CHECKS,
        "runner_imported": False,
        "interval_method": "multinomial counts from frozen slots; matrix weighted means",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "result_sha256": hashlib.sha256((base / "full/result.json.gz").read_bytes()).hexdigest(),
    }
    with a.output.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
