"""Independent compact arithmetic; imports only a prior analysis-only verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import verify_m5_e3_hxk4_followup as v

np, check, close, read = v.np, v.check, v.close, v.read
MODELS = ("xgb", "ridge")
SUBSETS = ("random32", "support32", "all64")
HELPER_SHA = "82c7341fe4cf93e32b361ae262a7c5f9086611ff571439d672e2ce7e02076a2f"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interval(values, actual):
    close(np.mean(values), actual["estimate"], "interval_mean")
    close(
        np.quantile(v.resample(values, seed=16001), [0.003125, 0.996875]),
        actual["interval"],
        "independent_weighted_interval",
    )
    check(
        actual["n"] == len(values)
        and actual["seed"] == 16001
        and actual["draws"] == 20000
        and actual["coverage"] == 0.99375,
        "interval_units",
    )


def paired(rows, getter, reference, alternative, actual):
    check(set(actual) == set(getter(rows[0], reference)), "all_fields_reported")
    for key, value in actual.items():
        pairs = [(getter(r, reference)[key], getter(r, alternative)[key]) for r in rows]
        defined = sum(a is not None and b is not None for a, b in pairs)
        check(value["defined_pairs"] == defined, "paired_definedness")
        if defined == len(rows):
            close(np.mean([a for a, b in pairs]), value["reference"], "reference_mean")
            close(np.mean([b for a, b in pairs]), value["alternative"], "alternative_mean")
            interval([b - a for a, b in pairs], value["change"])
        else:
            check(set(value) == {"defined_pairs"}, "no_complete_case_ci")


def manipulation(row):
    mm = row["manipulation"]
    for subset, m in mm.items():
        size = 64 if subset == "all64" else 32
        check(m["train_count"] == size and 1 <= m["train_sites"] <= size, "training_size")
        for prefix, n in (("remaining", 8506), ("reference64", 64)):
            supported, pairs = [
                m[prefix + name] for name in ("_supported_fraction", "_paired_fraction")
            ]
            check(0 <= pairs <= supported <= 1, "support_rate_bounds")
            close(n * (supported + pairs), m[prefix + "_support_score"], "capped_support_score")
            close(n * pairs, m[prefix + "_range_defined"], "range_defined_count")
            check(
                m[prefix + "_support_count_sum"] >= m[prefix + "_support_score"],
                "uncapped_support_bound",
            )
            check(m[prefix + "_range_sum"] >= 0, "nonnegative_range")
            for field in (
                "_supported_fraction",
                "_paired_fraction",
                "_support_score",
                "_support_count_sum",
                "_range_defined",
                "_range_sum",
            ):
                check(
                    m[prefix + field] <= mm["all64"][prefix + field] + 1e-9, "subset_support_bound"
                )
    check(
        mm["support32"]["reference64_support_score"] >= mm["random32"]["reference64_support_score"],
        "target_objective_manipulation",
    )
    check(0 <= row["subset_overlap"] <= 32, "training_overlap")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, base = Path(__file__).resolve().parents[1], args.directory
    check(sha(Path(v.__file__)) == HELPER_SHA, "independent_helper_identity")
    previous = root / "docs/results/m5-e3-hxk4-followup-20260910/full/result.json.gz"
    refs = {r["run_seed"]: r for r in read(previous)["rows"]}
    results = {}
    for mode in ("diagnostic", "smoke", "full"):
        d = base / mode
        t, ex, r = [read(d / n) for n in ("terminal.json", "execution.json", "result.json.gz")]
        results[mode] = r
        expected = [3000] if mode == "smoke" else list(range(3000, 3040))
        check(t["status"] == "succeeded", "attempt_status")
        for name in ("execution.json", "progress.jsonl", "result.json.gz"):
            check(sha(d / name) == t["artifacts"][name], "terminal_identity")
        check(
            ex["source_sha256"]
            == t["source_sha256"]
            == (
                "eda5692a80086606f105d5506d76adda50aa2a0fd182ceec88d96ba3e99a3c57"
                if mode == "diagnostic"
                else sha(root / "scripts/run_m5_e3_site_support.py")
            ),
            "source_identity",
        )
        check(
            ex["plan_sha256"] == sha(root / "docs/experiments/M5_E3_SITE_SUPPORT.md"),
            "plan_identity",
        )
        check(ex["prior_result_sha256"] == sha(previous), "prior_result_identity")
        check(
            ex["state_seeds"] == expected
            and sorted(row["run_seed"] for row in r["rows"]) == expected,
            "matrix",
        )
        check(
            ex["label_cost"] == 80
            and ex["initial_sample_size"] == 64
            and ex["batch_size"] == 16
            and ex["new_labels"] == ex["new_states"] == 0,
            "cost_and_scope",
        )
        check(
            r["completed_states"] == len(expected)
            and r["point_fits"] == (0 if mode == "diagnostic" else len(expected) * 6),
            "fit_accounting",
        )
        if mode != "diagnostic":
            check(
                ex["diagnostic_result_sha256"] == sha(base / "diagnostic/result.json.gz"),
                "locked_design_result",
            )
        events = [json.loads(s)["event"] for s in (d / "progress.jsonl").read_text().splitlines()]
        lock = "design_saved" if mode == "diagnostic" else "predictions_saved"
        indices = [i for i, e in enumerate(events) if e == lock]
        check(
            len(indices) == len(expected)
            and max(indices) < events.index("workers_exited") < events.index("scoring_complete"),
            "lock_exit_before_score",
        )
        check(
            t["wall_seconds"] < (700 if mode == "full" else 90)
            and t["peak_rss_bytes"] < 1610612736
            and t["peak_disk_bytes"] < 268435456,
            "resource_bounds",
        )
        for row in r["rows"]:
            check(
                row["allowed_observation_sha256"]
                == refs[row["run_seed"]]["allowed_observation_sha256"],
                "allowed_state_identity",
            )
            manipulation(row)
    diag = results["diagnostic"]
    designs = {r["run_seed"]: r for r in diag["rows"]}
    for row in diag["rows"]:
        check(
            row["remaining_count"] == 8506 and 22 <= row["remaining_high"] <= 86, "remaining_scope"
        )
        for model in MODELS:
            groups = row["groups"][model]
            for band, count in (("1_16", 16), ("17_64", 48), ("beyond64", 8442)):
                check(
                    groups[band + "/high"]["n"] + groups[band + "/nonhigh"]["n"] == count,
                    "rank_band_partition",
                )
            check(
                sum(g["n"] for key, g in groups.items() if key.endswith("/high"))
                == row["remaining_high"],
                "all_high_partition",
            )
            check(
                groups["1_16/high"]["n"] == refs[row["run_seed"]]["metrics"][model]["hits_16"],
                "original_diagnostic_hits",
            )
            for g in groups.values():
                check(
                    0 <= g["nonzero_range"] <= g["paired"] <= g["supported"] <= g["n"],
                    "diagnostic_count_bounds",
                )
                check(
                    g["support_count_sum"] >= g["supported"] + g["paired"] and g["range_sum"] >= 0,
                    "diagnostic_support_bounds",
                )
                if g["paired"] == 0:
                    close(g["range_sum"], 0, "undefined_range_not_observed")
            m = row["manipulation"]["all64"]
            close(
                sum(g["supported"] for g in groups.values()),
                m["remaining_supported_fraction"] * 8506,
                "group_support_conservation",
            )
            close(
                sum(g["paired"] for g in groups.values()),
                m["remaining_range_defined"],
                "group_pair_conservation",
            )
            close(
                sum(g["range_sum"] for g in groups.values()),
                m["remaining_range_sum"],
                "group_range_conservation",
            )
            close(
                sum(g["support_count_sum"] for g in groups.values()),
                m["remaining_support_count_sum"],
                "group_support_count_conservation",
            )
        for key in row["groups"]["ridge"]["1_16/high"]:
            for cls in ("high", "nonhigh"):
                totals = [
                    sum(g[key] for k, g in row["groups"][m].items() if k.endswith("/" + cls))
                    for m in MODELS
                ]
                close(*totals, "same_label_partition_across_models")
    for m, gg in diag["group_totals"].items():
        for g, values in gg.items():
            for key, value in values.items():
                close(
                    sum(r["groups"][m][g][key] for r in diag["rows"]),
                    value,
                    "independent_group_totals",
                )
    check(
        diag["changed_subsets"] == sum(r["subset_overlap"] < 32 for r in diag["rows"]),
        "changed_subsets",
    )
    paired(
        diag["rows"],
        lambda r, a: r["manipulation"][a],
        "random32",
        "support32",
        diag["manipulation"],
    )
    for mode in ("smoke", "full"):
        for row in results[mode]["rows"]:
            original = refs[row["run_seed"]]
            design = designs[row["run_seed"]]
            check(
                row["design_sha256"] == design["design_sha256"]
                and row["manipulation"] == design["manipulation"],
                "locked_design_reused",
            )
            check(
                row["reference_reconstruction"] == "EXACT"
                and row["label_cost"] == 80
                and row["high_denominator"] == 86,
                "baseline_and_units",
            )
            for model in MODELS:
                for subset in SUBSETS:
                    m = row["metrics"][model][subset]
                    close(m["recall_16"], m["hits_16"] / 86, "recall_denominator")
                    check(0 <= m["hits_16"] <= min(16, m["hits_64"]) <= 64, "hit_count_bounds")
                    check(
                        m["top16_high"] == m["hits_16"]
                        and m["top16_high"] + m["top16_near"] + m["top16_far"] == 16,
                        "frontier_category_partition",
                    )
                    h = m["fixed_pool_high"]
                    check(0 <= m["fixed_hits_16"] <= min(h, 16), "fixed_pool_hit_bound")
                    check(
                        h * (h + 1) / 2 <= m["fixed_high_rank_sum"] <= h * (129 - h) / 2,
                        "fixed_pool_rank_bound",
                    )
                    check((m["fixed_auc"] is None) == (h in (0, 64)), "fixed_auc_definedness")
                    check(
                        (m["subset_frontier_auc"] is None)
                        == (m["subset_frontier_high"] in (0, m["subset_frontier_size"])),
                        "common_auc_definedness",
                    )
                    check(m["best_improvement"] >= 0, "best_value_nondecreasing")
                    if subset == "all64":
                        for key, value in original["metrics"][model].items():
                            if key in m:
                                check(value == m[key], "exact_old_metrics")
                for key in ("fixed_pool_high", "subset_frontier_high", "subset_frontier_size"):
                    check(
                        len({r[key] for r in row["metrics"][model].values()}) == 1,
                        "shared_diagnostic_pool",
                    )
    full = results["full"]
    rows = sorted(full["rows"], key=lambda r: r["run_seed"])
    for key in (
        "metrics",
        "manipulation",
        "subset_overlap",
        "design_sha256",
        "training",
        "allowed_observation_sha256",
    ):
        check(results["smoke"]["rows"][0][key] == rows[0][key], "smoke_full_reproduction")
    check(len(full["comparisons"]) == 6, "comparison_matrix")
    for c in full["comparisons"]:
        model, ref, alt = c["model"], c["reference"], c["alternative"]
        paired(rows, lambda r, a, model=model: r["metrics"][model][a], ref, alt, c["metrics"])
        x, y = [np.array([r["metrics"][model][a]["hits_16"] for r in rows]) for a in (ref, alt)]
        d = y - x
        for key, mask in (
            ("positive", d > 0),
            ("negative", d < 0),
            ("zero", d == 0),
            ("both_zero", (x == 0) & (y == 0)),
            ("reference_zero", x == 0),
            ("alternative_zero", y == 0),
        ):
            check(c["states"][key] == int(mask.sum()), "state_effect_counts")
        loo = [np.delete(d, i).mean() for i in range(40)]
        close(c["states"]["leave_one_out_range"], [min(loo), max(loo)], "independent_delete_one")
    paired(rows, lambda r, a: r["manipulation"][a], "random32", "support32", full["manipulation"])
    interaction = [
        (
            r["metrics"]["ridge"]["support32"]["hits_16"]
            - r["metrics"]["ridge"]["random32"]["hits_16"]
        )
        - (r["metrics"]["xgb"]["support32"]["hits_16"] - r["metrics"]["xgb"]["random32"]["hits_16"])
        for r in rows
    ]
    interval(interaction, full["model_interaction"])
    attempts = [json.loads(s) for s in (base / "attempts.jsonl").read_text().splitlines()]
    check(
        len(attempts) == 10 and [v["event"] for v in attempts] == ["started", "finished"] * 5,
        "complete_attempts",
    )
    check(
        [a["status"] for a in attempts if a["event"] == "finished"]
        == ["succeeded", "failed", "failed", "succeeded", "succeeded"],
        "all_attempts_retained",
    )
    failure = read(base / "smoke-failed/failure.json")
    check(failure["message"] == "all64 metrics mismatch", "bounded_implementation_retry")
    audit = read(base / "prediction-audit.json")
    check(
        audit["remaining_exact"]
        and all(m["point_predictions_exact"] for m in audit["models"].values()),
        "failed_attempt_baseline_exact",
    )
    check(sum(a.get("wall_seconds", 0) for a in attempts) < 900, "cumulative_budget")
    output = {
        "status": "PASS",
        "checks": v.CHECKS,
        "total_checks": sum(v.CHECKS.values()),
        "verifier_sha256": sha(Path(__file__)),
        "helper_sha256": HELPER_SHA,
        "result_sha256": sha(base / "full/result.json.gz"),
        "numpy": np.__version__,
        "scope": "independent compact arithmetic/identities; raw arrays checked by server runner",
    }
    with args.output.open("x") as f:
        json.dump(output, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
