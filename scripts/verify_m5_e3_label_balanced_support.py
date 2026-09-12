"""Independent compact arithmetic and identities; no experiment runner or raw truth."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import verify_m5_e3_hxk4_followup as v

np, check, close, read = v.np, v.check, v.close, v.read
HELPER_SHA = "82c7341fe4cf93e32b361ae262a7c5f9086611ff571439d672e2ce7e02076a2f"
ARMS = ("pair_low", "pair_high", "composition_matched", "original_support", "original_random")
MODELS = ("xgb", "ridge")
CONTRASTS = (
    ("pair_low", "pair_high", "primary_A"),
    ("original_support", "composition_matched", "primary_B"),
    ("original_random", "composition_matched", "auxiliary"),
    ("original_random", "original_support", "historical_reconstruction"),
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interval(values, actual):
    close(np.mean(values), actual["estimate"], "mean")
    close(
        np.quantile(v.resample(values, seed=17001), [0.003125, 0.996875]),
        actual["interval"],
        "weighted_bootstrap",
    )
    check(
        actual["n"] == len(values)
        and actual["seed"] == 17001
        and actual["draws"] == 20000
        and actual["coverage"] == 0.99375,
        "interval_units",
    )


def paired(rows, getter, reference, alternative, actual):
    check(set(actual) == set(getter(rows[0], reference)), "all_fields")
    for key, item in actual.items():
        pairs = [(getter(r, reference)[key], getter(r, alternative)[key]) for r in rows]
        n = sum(a is not None and b is not None for a, b in pairs)
        check(item["defined_pairs"] == n, "paired_definedness")
        if n == len(rows):
            close(np.mean([a for a, b in pairs]), item["reference"], "reference_mean")
            close(np.mean([b for a, b in pairs]), item["alternative"], "alternative_mean")
            interval([b - a for a, b in pairs], item["change"])
        else:
            check(set(item) == {"defined_pairs"}, "no_complete_case_interval")


def balance(row):
    c = row["label_balance"]
    mm = row["manipulation"]
    check(0 <= c["pair_eligible"] <= 32 and 0 <= c["anchors"] <= 32, "design_counts")
    check(
        0 <= c["pair_label_mean_absolute_gap_sd"] <= c["pair_label_max_gap_sd"] <= 0.05 + 1e-12,
        "label_caliper",
    )
    check(
        abs(c["pair_label_mean_signed_gap_sd"]) <= c["pair_label_mean_absolute_gap_sd"] + 1e-12,
        "mean_bound",
    )
    old, new = (
        c["composition_old_squared_distance_sd"],
        c["composition_matched_squared_distance_sd"],
    )
    check(-1e-10 <= new <= old + 1e-10, "matching_improves_feasible_objective")
    close(old - new, c["composition_distance_reduction_sd"], "matching_reduction")
    check(c["composition_support_count_max_difference"] == 0, "support_count_exact")
    check(c["pair_changed"] in (0, 1) and c["composition_changed"] in (0, 1), "inclusion_noop")
    for m in mm.values():
        check(m["train_count"] == 32 and 1 <= m["train_sites"] <= 32, "training_count")
        for prefix, n in (("remaining", 8506), ("reference64", 64)):
            x, p = [m[prefix + "_" + k] for k in ("supported_fraction", "paired_fraction")]
            check(0 <= p <= x <= 1, "support_bounds")
            close(n * (x + p), m[prefix + "_support_score"], "capped_support")
            close(n * p, m[prefix + "_range_defined"], "paired_count")
            check(
                m[prefix + "_support_count_sum"] >= m[prefix + "_support_score"]
                and m[prefix + "_range_sum"] >= 0,
                "support_range_bounds",
            )
    for key in (
        "reference64_supported_fraction",
        "reference64_paired_fraction",
        "reference64_support_score",
        "reference64_support_count_sum",
        "reference64_range_defined",
        "reference64_range_sum",
    ):
        close(mm["composition_matched"][key], mm["original_support"][key], "B_fixed_support")
    check(
        mm["pair_high"]["reference64_support_score"] >= mm["pair_low"]["reference64_support_score"],
        "A_support_order",
    )
    if not c["pair_changed"]:
        for key in mm["pair_low"]:
            close(mm["pair_high"][key], mm["pair_low"][key], "no_op_A_manipulation")
    if not c["composition_changed"]:
        for key in mm["original_support"]:
            close(
                mm["composition_matched"][key], mm["original_support"][key], "no_op_B_manipulation"
            )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = args.directory
    check(sha(Path(v.__file__)) == HELPER_SHA, "analysis_helper_identity")
    previous = root / "docs/results/m5-e3-site-support-20260911/full/result.json.gz"
    refs = {r["run_seed"]: r for r in read(previous)["rows"]}
    results = {}
    for mode in ("design", "smoke", "full"):
        d = base / mode
        t, ex, r = [read(d / k) for k in ("terminal.json", "execution.json", "result.json.gz")]
        results[mode] = r
        expected = [3000] if mode == "smoke" else list(range(3000, 3040))
        check(t["status"] == "succeeded", "attempt_success")
        for key in ("execution.json", "progress.jsonl", "result.json.gz"):
            check(sha(d / key) == t["artifacts"][key], "artifact_identity")
        check(
            ex["source_sha256"]
            == t["source_sha256"]
            == sha(root / "scripts/run_m5_e3_label_balanced_support.py"),
            "source_identity",
        )
        check(
            ex["plan_sha256"] == sha(root / "docs/experiments/M5_E3_LABEL_BALANCED_SUPPORT.md"),
            "plan_identity",
        )
        check(ex["prior_result_sha256"] == sha(previous), "prior_result_identity")
        check(
            ex["state_seeds"] == expected and [row["run_seed"] for row in r["rows"]] == expected,
            "state_matrix",
        )
        check(
            ex["label_cost"] == 80
            and ex["batch_size"] == 16
            and ex["training_size"] == 32
            and ex["initial_sample_size"] == 64
            and ex["new_labels"] == ex["new_states"] == 0,
            "cost_and_scope",
        )
        check(
            ex["arms"] == list(ARMS)
            and ex["fitted_arms"] == list(ARMS[:-1])
            and ex["contrasts"] == [list(c) for c in CONTRASTS],
            "fixed_recipe",
        )
        check(
            r["point_fits"] == (0 if mode == "design" else 8 * len(expected))
            and r["completed_states"] == len(expected),
            "fit_accounting",
        )
        check(
            t["wall_seconds"] < (700 if mode == "full" else 90)
            and t["peak_rss_bytes"] < 1610612736
            and t["peak_disk_bytes"] < 268435456,
            "resource_caps",
        )
        events = [json.loads(s)["event"] for s in (d / "progress.jsonl").read_text().splitlines()]
        lock = "design_saved" if mode == "design" else "predictions_saved"
        indices = [i for i, e in enumerate(events) if e == lock]
        last = "design_complete_without_scoring" if mode == "design" else "scoring_complete"
        check(
            len(indices) == len(expected)
            and max(indices) < events.index("workers_exited") < events.index(last),
            "lock_exit_score_order",
        )
        if mode == "design":
            check(
                "scoring_complete" not in events and ex["design_result_sha256"] is None,
                "design_no_score",
            )
        else:
            check(
                ex["design_result_sha256"] == sha(base / "design/result.json.gz"),
                "locked_design_result",
            )
        for row in r["rows"]:
            check(
                row["allowed_observation_sha256"]
                == refs[row["run_seed"]]["allowed_observation_sha256"],
                "allowed_state_identity",
            )
            balance(row)
    designs = {r["run_seed"]: r for r in results["design"]["rows"]}
    for mode in ("smoke", "full"):
        for row in results[mode]["rows"]:
            design = designs[row["run_seed"]]
            original = refs[row["run_seed"]]
            for key in ("design_sha256", "manipulation", "label_balance"):
                check(row[key] == design[key], "design_unchanged")
            check(
                row["reference_reconstruction"] == "EXACT"
                and row["high_denominator"] == 86
                and row["label_cost"] == 80,
                "baseline_units",
            )
            for model in MODELS:
                for arm, m in row["metrics"][model].items():
                    close(m["recall_16"], m["hits_16"] / 86, "recall_denominator")
                    check(0 <= m["hits_16"] <= min(16, m["hits_64"]) <= 64, "hit_bounds")
                    check(
                        m["top16_high"] == m["hits_16"]
                        and m["top16_high"] + m["top16_near"] + m["top16_far"] == 16,
                        "frontier_partition",
                    )
                    h = m["fixed_pool_high"]
                    check(0 <= m["fixed_hits_16"] <= min(16, h), "fixed_hit_bound")
                    check(
                        h * (h + 1) / 2 <= m["fixed_high_rank_sum"] <= h * (129 - h) / 2,
                        "rank_sum_bound",
                    )
                    check((m["fixed_auc"] is None) == (h in (0, 64)), "fixed_auc_definedness")
                    check(
                        (m["balanced_frontier_auc"] is None)
                        == (m["balanced_frontier_high"] in (0, m["balanced_frontier_size"])),
                        "frontier_auc_definedness",
                    )
                    if arm in ("original_support", "original_random"):
                        old_arm = "support32" if arm == "original_support" else "random32"
                        for key, value in original["metrics"][model][old_arm].items():
                            if key in m:
                                check(m[key] == value, "exact_prior_metrics")
                for key in ("fixed_pool_high", "balanced_frontier_high", "balanced_frontier_size"):
                    check(
                        len({m[key] for m in row["metrics"][model].values()}) == 1,
                        "shared_diagnostic_pool",
                    )
                for a, b, changed in [
                    ("pair_low", "pair_high", row["label_balance"]["pair_changed"]),
                    (
                        "original_support",
                        "composition_matched",
                        row["label_balance"]["composition_changed"],
                    ),
                ]:
                    if not changed:
                        check(row["metrics"][model][a] == row["metrics"][model][b], "no_op_metrics")
    full = results["full"]
    rows = full["rows"]
    for key in (
        "metrics",
        "manipulation",
        "label_balance",
        "design_sha256",
        "allowed_observation_sha256",
    ):
        check(results["smoke"]["rows"][0][key] == rows[0][key], "smoke_full_reproduction")
    check(
        [(c["model"], c["reference"], c["alternative"], c["role"]) for c in full["comparisons"]]
        == [(m, *c) for m in MODELS for c in CONTRASTS],
        "comparison_matrix",
    )
    for c in full["comparisons"]:
        model, a, b = c["model"], c["reference"], c["alternative"]
        paired(rows, lambda r, k, model=model: r["metrics"][model][k], a, b, c["metrics"])
        x, y = [np.array([r["metrics"][model][k]["hits_16"] for r in rows]) for k in (a, b)]
        delta = y - x
        for key, mask in [
            ("positive", delta > 0),
            ("negative", delta < 0),
            ("zero", delta == 0),
            ("both_zero", (x == 0) & (y == 0)),
            ("reference_zero", x == 0),
            ("alternative_zero", y == 0),
        ]:
            check(c["states"][key] == int(mask.sum()), "state_effect_counts")
        loo = [np.delete(delta, i).mean() for i in range(40)]
        close(c["states"]["leave_one_out_range"], [min(loo), max(loo)], "delete_one")
    for mode in ("design", "full"):
        result = results[mode]
        rr = result["rows"]
        for key, summary in result["label_balance_summary"].items():
            vals = [r["label_balance"][key] for r in rr]
            for k, value in [("mean", np.mean(vals)), ("min", min(vals)), ("max", max(vals))]:
                close(summary[k], value, "balance_summary")
        check(len(result["manipulation_comparisons"]) == 4, "all_manipulations")
        for c in result["manipulation_comparisons"]:
            paired(
                rr, lambda r, k: r["manipulation"][k], c["reference"], c["alternative"], c["fields"]
            )
    attempts = [json.loads(s) for s in (base / "attempts.jsonl").read_text().splitlines()]
    check(
        len(attempts) == 6 and [a["event"] for a in attempts] == ["started", "finished"] * 3,
        "complete_attempts",
    )
    check(
        all(
            a["status"] == "succeeded" and a["returncode"] == 0
            for a in attempts
            if a["event"] == "finished"
        ),
        "first_success",
    )
    check(sum(a.get("wall_seconds", 0) for a in attempts) < 900, "cumulative_wall")
    output = {
        "status": "PASS",
        "checks": v.CHECKS,
        "total_checks": sum(v.CHECKS.values()),
        "verifier_sha256": sha(Path(__file__)),
        "helper_sha256": HELPER_SHA,
        "result_sha256": sha(base / "full/result.json.gz"),
        "numpy": np.__version__,
        "scope": "independent compact arithmetic/identities; raw arrays verified by server runner",
    }
    with args.output.open("x") as f:
        json.dump(output, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
