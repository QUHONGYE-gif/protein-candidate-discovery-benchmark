"""Independent aggregate arithmetic, provenance and event-order verification; no oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import verify_m5_e3_hxk4_followup as v

np, read, check, close = v.np, v.read, v.check, v.close
HELPER = "82c7341fe4cf93e32b361ae262a7c5f9086611ff571439d672e2ce7e02076a2f"
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
        np.percentile(v.resample(values, seed=18001), [0.3125, 99.6875]),
        actual["interval"],
        "weighted_bootstrap",
    )
    check(
        actual["n"] == len(values)
        and actual["seed"] == 18001
        and actual["draws"] == 20000
        and actual["coverage"] == 0.99375,
        "interval_units",
    )


def row_check(row, old):
    for key in ("allowed_observation_sha256", "prediction_sha256"):
        check(row[key] == old[key], "cached_identity")
    check(row["prior_selection_sha256"] == old["selection_sha256"], "cached_identity")
    check(len(row["locked_sha256"]) == 64, "locked_identity_present")
    check(0 < row["public_evaluator_calls"] <= 36, "batch_count_bound")
    mm = row["arm_metrics"]
    check(set(mm) == {m + "_" + a for m in MODELS for a in ARMS}, "arm_matrix")
    for model in MODELS:
        for arm in ARMS:
            x, y = mm[model + "_" + arm], old["metrics"][model][arm]
            for key in ("hits_16", "hits_64", "fixed_hits_16"):
                check(x[key] == y[key], "original_counts_exact")
            h, h64, f = (x[k] for k in ("hits_16", "hits_64", "fixed_hits_16"))
            check(all(type(x[k]) is int for k in x), "integer_arm_metrics")
            check(0 <= h <= min(h64, 16) <= 16 and 0 <= h64 <= 64 and 0 <= f <= 16, "hit_bounds")
            check(x["capacity"] == min(h64, 16), "capacity")
            check(x["unused_capacity"] == x["capacity"] - h, "capacity_loss")
            check(x["global_minus_fixed"] == h - f, "reference_gap")
            overlap, outside = x["fixed_selection_overlap"], x["global16_outside_reference"]
            check(0 <= overlap <= 16 and overlap + outside == 16, "reference_order_nesting")
            check(abs(h - f) <= outside, "reference_gap_bound")
    expected = [(m, *c) for m in MODELS for c in CONTRASTS]
    check(
        [(c["model"], c["reference"], c["alternative"], c["role"]) for c in row["comparisons"]]
        == expected,
        "contrast_matrix",
    )
    for c in row["comparisons"]:
        a, b = [mm[c["model"] + "_" + c[k]] for k in ("reference", "alternative")]
        aa, ab, ba, bb = (c["cells"][k] for k in ("AA", "AB", "BA", "BB"))
        check(all(type(z) is int and 0 <= z <= 16 for z in (aa, ab, ba, bb)), "cross_hit_bounds")
        check(aa == a["hits_16"] and bb == b["hits_16"], "diagonal")
        check(ab <= a["capacity"] and ba <= b["capacity"], "cross_capacity_bound")
        effects = c["effects"]
        expected_effects = {
            "total": b["hits_16"] - a["hits_16"],
            "pool": (bb + ba - aa - ab) / 2,
            "ranking": (bb + ab - aa - ba) / 2,
            "pool_minus_ranking": ba - ab,
            "pool_at_old_rank": ba - aa,
            "pool_at_new_rank": bb - ab,
            "ranking_in_old_pool": ab - aa,
            "ranking_in_new_pool": bb - ba,
            "interaction": (bb - ba) - (ab - aa),
        }
        check(effects == expected_effects, "cross_algebra")
        check(c["changes"] == {k: b[k] - a[k] for k in a}, "paired_changes")
        d = c["changes"]
        check(d["hits_16"] == d["capacity"] - d["unused_capacity"], "capacity_identity")
        check(d["hits_16"] == d["fixed_hits_16"] + d["global_minus_fixed"], "gap_identity")
        check(0 <= c["pool_overlap"] <= 64, "pool_overlap_bound")
        if c["pool_overlap"] == 64:
            check(aa == ba and ab == bb and effects["pool"] == 0, "same_pool_identity")
        noop = (c["role"] == "primary_A" and not old["label_balance"]["pair_changed"]) or (
            c["role"] == "primary_B" and not old["label_balance"]["composition_changed"]
        )
        if noop:
            check(a == b and aa == ab == ba == bb and c["pool_overlap"] == 64, "all_noops_retained")


def summary_check(data):
    rows = data["rows"]
    for key, aggregate in data["arms"].items():
        mm = [r["arm_metrics"][key] for r in rows]
        for metric, result in aggregate["metrics"].items():
            interval([r[metric] for r in mm], result)
        counts = aggregate["state_counts"]
        check(
            counts
            == {
                "no_high_in_pool": sum(r["hits_64"] == 0 for r in mm),
                "high_in_pool_but_zero_hits": sum(r["hits_16"] == 0 < r["hits_64"] for r in mm),
                "any_hit": sum(r["hits_16"] > 0 for r in mm),
            },
            "zero_hit_categories",
        )
        check(sum(counts.values()) == len(rows), "category_partition")
    for i, aggregate in enumerate(data["comparisons"]):
        rr = [r["comparisons"][i] for r in rows]
        for section in ("cells", "effects", "changes"):
            for metric, actual in aggregate[section].items():
                interval([r[section][metric] for r in rr], actual)
        interval([r["pool_overlap"] for r in rr], aggregate["pool_overlap"])
        check(
            aggregate["state_counts"]
            == {
                "positive": sum(r["effects"]["total"] > 0 for r in rr),
                "zero": sum(r["effects"]["total"] == 0 for r in rr),
                "negative": sum(r["effects"]["total"] < 0 for r in rr),
                "zero_interaction": sum(r["effects"]["interaction"] == 0 for r in rr),
                "same_pool": sum(r["pool_overlap"] == 64 for r in rr),
            },
            "effect_state_counts",
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    root, base = Path(__file__).resolve().parents[1], args.directory
    check(sha(Path(v.__file__)) == HELPER, "independent_helper_identity")
    previous = root / "docs/results/m5-e3-label-balanced-support-20260911/full"
    old_ex = read(previous / "execution.json")
    refs = {r["run_seed"]: r for r in read(previous / "result.json.gz")["rows"]}
    result = {}
    for mode in ("smoke", "full"):
        d = base / mode
        t, ex, data = [read(d / k) for k in ("terminal.json", "execution.json", "result.json.gz")]
        result[mode] = data
        seeds = [3000] if mode == "smoke" else list(range(3000, 3040))
        check(t["status"] == "succeeded", "attempt_success")
        for name in ("execution.json", "progress.jsonl", "result.json.gz"):
            check(sha(d / name) == t["artifacts"][name], "artifact_identity")
        check(
            ex["source_sha256"]
            == t["source_sha256"]
            == sha(root / "scripts/analyze_m5_e3_entry_ranking.py"),
            "source_identity",
        )
        check(
            ex["plan_sha256"] == sha(root / "docs/experiments/M5_E3_ENTRY_RANKING.md"),
            "plan_identity",
        )
        check(
            ex["prior_source_sha256"] == old_ex["source_sha256"]
            and ex["prior_execution_sha256"] == sha(previous / "execution.json")
            and ex["prior_result_sha256"] == sha(previous / "result.json.gz"),
            "prior_identity",
        )
        for key in ("helper_sha256", "versions", "input_identity", "input_sha256"):
            check(ex[key] == old_ex[key], "runtime_input_identity")
        check(ex["state_seeds"] == [r["run_seed"] for r in data["rows"]] == seeds, "states")
        check(ex["new_fits"] == ex["new_labels"] == data["new_fits"] == 0, "no_new_training")
        check(
            (
                ex["initial_sample_size"],
                ex["training_size"],
                ex["label_cost"],
                ex["remaining_size"],
                ex["batch_size"],
                ex["pool_size"],
                ex["high_denominator"],
            )
            == (64, 32, 80, 8506, 16, 64, 86),
            "budget_pool_units",
        )
        check(
            ex["arms"] == list(ARMS)
            and ex["models"] == list(MODELS)
            and ex["contrasts"] == [list(c) for c in CONTRASTS],
            "locked_recipe",
        )
        check(
            t["wall_seconds"] < (45 if mode == "smoke" else 100)
            and t["peak_rss_bytes"] < 1610612736
            and t["peak_disk_bytes"] < 67108864,
            "resource_caps",
        )
        events = [json.loads(s) for s in (d / "progress.jsonl").read_text().splitlines()]
        names = [e["event"] for e in events]
        locks = [i for i, n in enumerate(names) if n == "choices_locked"]
        check(
            len(locks) == len(seeds)
            and max(locks)
            < names.index("workers_exited")
            < names.index("public_evaluator_started")
            < names.index("scoring_completed"),
            "lock_exit_score_order",
        )
        for row, i in zip(data["rows"], locks, strict=True):
            check(events[i]["locked_sha256"] == row["locked_sha256"], "lock_result_binding")
            row_check(row, refs[row["run_seed"]])
        check(
            data["completed_states"] == len(seeds)
            and data["public_evaluator_calls"]
            == sum(r["public_evaluator_calls"] for r in data["rows"]),
            "execution_accounting",
        )
        summary_check(data)
    check(result["smoke"]["rows"][0] == result["full"]["rows"][0], "smoke_full_exact")
    ledger = [json.loads(s) for s in (base / "attempts.jsonl").read_text().splitlines()]
    starts, ends = [[r for r in ledger if r["event"] == event] for event in ("started", "finished")]
    check(len(starts) == len(ends) == 2, "all_attempts_first_success")
    for start, end in zip(starts, ends, strict=True):
        check(
            start["attempt"] == end["attempt"]
            and end["status"] == "succeeded"
            and end["returncode"] == 0,
            "attempt_pair",
        )
        mode = "smoke" if "smoke" in end["attempt"] else "full"
        check(end["terminal_sha256"] == sha(base / mode / "terminal.json"), "ledger_terminal")
        check(
            start["source_sha256"]
            == end["source_sha256"]
            == sha(root / "scripts/analyze_m5_e3_entry_ranking.py"),
            "ledger_source",
        )
    check(sum(e["wall_seconds"] for e in ends) < 300, "cumulative_wall")
    output = {
        "status": "PASS",
        "checks": v.CHECKS,
        "total_checks": sum(v.CHECKS.values()),
        "numpy": np.__version__,
        "verifier_sha256": sha(Path(__file__)),
        "helper_sha256": HELPER,
        "result_sha256": sha(base / "full/result.json.gz"),
        "scope": "compact arithmetic/identities; no raw label or prediction reconstruction",
    }
    with args.output.open("x") as f:
        json.dump(output, f, indent=2, sort_keys=True)
        f.write("\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "total_checks": output["total_checks"],
                "intervals": v.CHECKS["weighted_bootstrap"],
            }
        )
    )


if __name__ == "__main__":
    main()
