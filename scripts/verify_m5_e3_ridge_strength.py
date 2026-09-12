"""Independent compact-result arithmetic and provenance checks; no runner import."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
import numpy as np  # noqa: E402

CHECKS = {}
COUNTS = {}
ARMS = ("lambda0p1", "lambda1", "lambda10")
CONTEXTS = ("ridge_pool", "xgb_pool", "remaining")


def check(condition, name):
    if not condition:
        raise ValueError(name)
    CHECKS[name] = CHECKS.get(name, 0) + 1


def close(a, b, name):
    check(bool(np.allclose(a, b, rtol=1e-10, atol=1e-10)), name)


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interval(values, actual):
    a = np.asarray(values, dtype=float)
    n = len(a)
    if n not in COUNTS:
        draws = np.random.Generator(np.random.PCG64(15001)).integers(0, n, (20000, n))
        counts = np.zeros((20000, n), dtype=int)
        np.add.at(counts, (np.arange(20000)[:, None], draws), 1)
        COUNTS[n] = counts
    independent = COUNTS[n] @ a / n
    close(a.mean(), actual["estimate"], "mean")
    close(
        np.quantile(independent, [0.003125, 0.996875]),
        actual["interval"],
        "independent_weighted_interval",
    )
    check(
        actual["n"] == n
        and actual["draws"] == 20000
        and actual["seed"] == 15001
        and actual["coverage"] == 0.99375,
        "interval_units",
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    base = args.directory
    root = Path(__file__).resolve().parents[1]
    prior_path = root / "docs/results/m5-e3-hxk4-followup-20260910/full/result.json.gz"
    refs = {r["run_seed"]: r for r in read(prior_path)["rows"]}
    results = {}
    for mode, seeds in (("smoke", [3000]), ("full", list(range(3000, 3040)))):
        d = base / mode
        result, terminal, execution = [
            read(d / name) for name in ("result.json.gz", "terminal.json", "execution.json")
        ]
        results[mode] = result
        check(terminal["status"] == "succeeded", "attempt_succeeded")
        for name in ("execution.json", "progress.jsonl", "result.json.gz"):
            check(digest(d / name) == terminal["artifacts"][name], "artifact_identity")
        check(
            execution["source_sha256"]
            == terminal["source_sha256"]
            == digest(root / "scripts/run_m5_e3_ridge_strength.py"),
            "source_identity",
        )
        check(
            execution["plan_sha256"] == digest(root / "docs/experiments/M5_E3_RIDGE_STRENGTH.md"),
            "prospective_note_identity",
        )
        check(execution["prior_result_sha256"] == digest(prior_path), "prior_identity")
        check(execution["state_seeds"] == seeds, "execution_seeds")
        check(
            execution["strengths"] == dict(zip(ARMS, (0.1, 1.0, 10.0), strict=True))
            and execution["contexts"] == list(CONTEXTS)
            and execution["primary_context"] == "ridge_pool"
            and execution["application_delta"] is None,
            "fixed_design",
        )
        check(
            sorted(r["run_seed"] for r in result["rows"]) == seeds
            and result["completed_states"] == len(seeds)
            and result["point_fits"] == 3 * len(seeds)
            and result["new_states"] == 0,
            "result_matrix",
        )
        events = [json.loads(line) for line in (d / "progress.jsonl").read_text().splitlines()]
        kinds = [e["event"] for e in events]
        saved = [i for i, k in enumerate(kinds) if k == "predictions_saved"]
        check(
            len(saved) == len(seeds)
            and max(saved) < kinds.index("workers_exited") < kinds.index("scoring_complete"),
            "selection_lock_exit_before_scoring",
        )
        check(
            terminal["peak_rss_bytes"] < 1610612736
            and terminal["peak_disk_bytes"] < 268435456
            and terminal["wall_seconds"] < (90 if mode == "smoke" else 800),
            "execution_bounds",
        )
        for row in result["rows"]:
            old = refs[row["run_seed"]]
            check(
                row["assay_id"] == "HXK4_HUMAN_Gersing_2022_activity"
                and row["initial_sample_size"] == 64
                and row["label_cost"] == 80
                and row["high_denominator"] == 86,
                "scientific_units",
            )
            check(
                row["reference_reconstruction"] == "EXACT"
                and row["allowed_observation_sha256"] == old["allowed_observation_sha256"],
                "original_allowed_observations",
            )
            for context in CONTEXTS:
                metrics = row["metrics"][context]
                check(len({m["pool_high"] for m in metrics.values()}) == 1, "fixed_pool_high")
                check(len({m["pool_size"] for m in metrics.values()}) == 1, "fixed_pool_size")
                for arm in ARMS:
                    m = metrics[arm]
                    high, size, hits = m["pool_high"], m["pool_size"], m["hits_16"]
                    check(
                        0 <= hits <= min(high, 16)
                        and m["oracle_bound"] == min(high, 16)
                        and m["opportunity_gap"] == min(high, 16) - hits,
                        "count_and_opportunity_bounds",
                    )
                    close(m["recall_16"], hits / 86, "recall_denominator")
                    check(
                        high * (high + 1) / 2
                        <= m["high_rank_sum"]
                        <= high * (2 * size - high + 1) / 2,
                        "high_rank_sum_bounds",
                    )
                    check((m["pool_auc"] is None) == (high in (0, size)), "auc_definedness")
                    check(
                        m["pool_auc"] is None or 0 <= m["pool_auc"] <= 1,
                        "auc_range",
                    )
                    if context != "remaining":
                        check(size == 64, "frontier_size")
                    diag = row["diagnostics"][context][arm]
                    check(0 <= diag["top16_overlap_reference"] <= 16, "overlap_bound")
                    if arm == "lambda1":
                        check(diag["top16_overlap_reference"] == 16, "reference_self_overlap")
                        close(diag["rank_rho_to_reference"], 1, "reference_self_rank")
                key = "xr" if context == "xgb_pool" else "rr"
                check(metrics["lambda1"]["hits_16"] == old["cells"][key], "old_context_hits")
            for key, value in old["metrics"]["ridge"].items():
                now = row["metrics"]["remaining"]["lambda1"]
                if key in now:
                    check(now[key] == value, "old_global_metrics_exact")
            for key, direction in (
                ("coefficient_l2", -1),
                ("penalized_effective_df", -1),
                ("training_mse", 1),
            ):
                values = [row["manipulation"][a][key] for a in ARMS]
                check(np.all(direction * np.diff(values) >= -1e-10), "manipulation_monotonicity")
    rows = sorted(results["full"]["rows"], key=lambda r: r["run_seed"])
    for key in (
        "metrics",
        "diagnostics",
        "manipulation",
        "pool_identities",
        "allowed_observation_sha256",
        "reference_reconstruction",
    ):
        check(rows[0][key] == results["smoke"]["rows"][0][key], "smoke_full_reproduction")
    full = results["full"]
    expected = {
        (c, a, r)
        for c in CONTEXTS
        for a, r in (("lambda0p1", "lambda1"), ("lambda10", "lambda1"), ("lambda10", "lambda0p1"))
    }
    check(
        len(full["contrasts"]) == 9
        and {(c["context"], c["alternative"], c["reference"]) for c in full["contrasts"]}
        == expected,
        "all_prespecified_contrasts",
    )
    for c in full["contrasts"]:
        context, alt, ref = c["context"], c["alternative"], c["reference"]
        for section in ("metrics", "diagnostics"):
            for key, value in c[section].items():
                pairs = [
                    (r[section][context][ref][key], r[section][context][alt][key]) for r in rows
                ]
                defined = sum(a is not None and b is not None for a, b in pairs)
                check(value["defined_pairs"] == defined, "defined_pairs")
                if defined == 40:
                    close(np.mean([a for a, b in pairs]), value["reference"], "reference_mean")
                    close(np.mean([b for a, b in pairs]), value["alternative"], "alternative_mean")
                    interval([b - a for a, b in pairs], value["change"])
                else:
                    check(set(value) == {"defined_pairs"}, "no_complete_case_interval")
        interval(
            [
                r["metrics"][context][ref]["high_rank_sum"]
                - r["metrics"][context][alt]["high_rank_sum"]
                for r in rows
            ],
            c["high_rank_forward"],
        )
        delta = np.array(
            [
                r["metrics"][context][alt]["hits_16"] - r["metrics"][context][ref]["hits_16"]
                for r in rows
            ]
        )
        states = c["states"]
        for key, mask in (("positive", delta > 0), ("zero", delta == 0), ("negative", delta < 0)):
            check(states[key] == int(mask.sum()), "state_response_counts")
        means = [np.delete(delta, i).mean() for i in range(40)]
        close(states["leave_one_out_range"], [min(means), max(means)], "independent_leave_one_out")
    check(len(full["availability"]) == 9, "availability_matrix")
    for item in full["availability"]:
        m = [r["metrics"][item["context"]][item["arm"]] for r in rows]
        for key, value in {
            "pool_no_high": sum(v["pool_high"] == 0 for v in m),
            "high_available_but_zero_hits": sum(
                v["pool_high"] > 0 and v["hits_16"] == 0 for v in m
            ),
            "any_hit": sum(v["hits_16"] > 0 for v in m),
            "all_zero": sum(v["hits_16"] == 0 for v in m),
        }.items():
            check(item[key] == value, "availability_counts")
        check(item["all_zero"] + item["any_hit"] == 40, "availability_total")
        check(
            item["pool_no_high"] + item["high_available_but_zero_hits"] == item["all_zero"],
            "zero_hit_partition",
        )
    for arm, metrics in full["manipulation"].items():
        for key, value in metrics.items():
            interval([r["manipulation"][arm][key] for r in rows], value)
    attempts = [json.loads(s) for s in (base / "attempts.jsonl").read_text().splitlines()]
    check(len(attempts) == 4, "all_attempts_reported")
    check([v["event"] for v in attempts] == ["started", "finished"] * 2, "attempt_order")
    check(
        all(v["status"] == "succeeded" for v in attempts if v["event"] == "finished"),
        "no_failed_attempt",
    )
    output = {
        "status": "PASS",
        "checks": CHECKS,
        "total_checks": sum(CHECKS.values()),
        "verifier_sha256": digest(Path(__file__)),
        "numpy": np.__version__,
        "result_sha256": digest(base / "full/result.json.gz"),
        "scope": "independent compact arithmetic/identities; raw arrays checked by server runner",
    }
    with args.output.open("x") as f:
        json.dump(output, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
