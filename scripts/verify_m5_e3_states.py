"""Independent arithmetic check of saved state diagnostics; no candidate data."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def mean(values):
    return math.fsum(values) / len(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    folder = args.directory
    root = Path(__file__).resolve().parents[1]
    execution = json.loads((folder / "execution.json").read_text())
    terminal = json.loads((folder / "terminal.json").read_text())
    raw = (folder / "result.json").read_bytes()
    assert terminal["status"] == "succeeded"
    assert hashlib.sha256(raw).hexdigest() == terminal["result_sha256"]
    for name, field in (
        ("scripts/analyze_m5_e3_states.py", "source_sha256"),
        ("docs/experiments/M5_E3_STATE_DIAGNOSIS.md", "note_sha256"),
    ):
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == execution[field]
    data = json.loads(raw)
    originals = {}
    for rel, sha in execution["inputs"].items():
        source = (root / ("docs/results/m5-e3-" + rel)).read_bytes()
        assert hashlib.sha256(source).hexdigest() == sha
        originals[rel.split("/")[0]] = json.loads(gzip.decompress(source))["rows"]
    counts = {"source_endpoints": 0, "numeric_checks": 0, "intervals": 0, "coverage_terms": 0}

    def same(x, y):
        assert np.allclose(x, y, atol=1e-12, rtol=0), (x, y)
        counts["numeric_checks"] += 1

    assert len(data["rows"]) == data["completed_states"] == 180
    assert data["new_fits"] == data["new_labels"] == 0
    assert not data["candidate_or_raw_truth_access"]
    for r in data["rows"]:
        source_name = "frontier-20260909" if r["cohort"] == "original" else "replication-20260910"
        matches = [
            x
            for x in originals[source_name]
            if all(x[k] == r[k] for k in ("assay_id", "initial_sample_size", "run_seed"))
            and x["model_id"] == "xgboost-cpu-v1"
        ]
        assert len(matches) == 1
        src = matches[0]
        den = {"PABP": 378, "HSP82": 133, "HXK4": 86}[r["assay_id"].split("_")[0]]
        remaining = (
            src["old_frontier"]["remaining_high"]
            if r["cohort"] == "original"
            else src["remaining_high"]
        )
        assert r["observed_high"] == den - remaining
        for arm in ("old", "new"):
            for metric, value in r[arm].items():
                if r["cohort"] == "replication":
                    target = src[arm][metric]
                elif metric.startswith("hits_"):
                    target = src[arm + "_frontier"][metric]
                else:
                    original = next(
                        x
                        for x in originals["input-intervention-20260909"]
                        if all(
                            x[k] == r[k] for k in ("assay_id", "initial_sample_size", "run_seed")
                        )
                        and x["model_id"] == "xgboost-cpu-v1"
                    )
                    target = original[arm + "_diagnostics"][metric]
                same(value, target)
                counts["source_endpoints"] += 1
    weights, vectors, draws = {}, {}, {}
    for c, n, seed in (("original", 10, 12201), ("replication", 20, 12202)):
        sampled = np.random.Generator(np.random.PCG64(seed)).integers(0, n, (20000, n))
        weights[c] = np.eye(n, dtype=int)[sampled].sum(axis=1)
    for summary in data["summaries"]:
        a, size, c = (summary[k] for k in ("assay_id", "initial_sample_size", "cohort"))
        rows = [
            r
            for r in data["rows"]
            if r["assay_id"] == a
            and r["cohort"] == c
            and (size == "combined" or r["initial_sample_size"] == size)
        ]
        order = sorted({r["run_seed"] for r in rows})
        assert order == summary["seed_order"] and len(order) == summary["seed_slots"]
        for k, record in summary["metrics"].items():
            arms = []
            for arm in ("old", "new"):
                v = np.array(
                    [mean([r[arm]["hits_" + k] for r in rows if r["run_seed"] == s]) for s in order]
                )
                arms.append(v)
                vectors[a, size, c, k, arm] = v
                draws[a, size, c, k, arm] = weights[c] @ v / len(order)
                same(record[arm + "_mean"], mean(v))
            delta = arms[1] - arms[0]
            same(record["delta_by_seed"], delta)
            expected_draws = weights[c] @ delta / len(order)
            same(record["effect"]["estimate"], mean(delta))
            same(record["effect"]["interval"], np.percentile(expected_draws, [0.3125, 99.6875]))
            assert record["effect"]["empirical_degenerate"] == (np.ptp(expected_draws) == 0)
            counts["intervals"] += 1
            signs = {
                "positive": sum(x > 0 for x in delta),
                "negative": sum(x < 0 for x in delta),
                "zero": sum(x == 0 for x in delta),
            }
            assert record["sign_counts"] == signs
            assert record["both_arms_zero"] == sum(x == y == 0 for x, y in zip(*arms, strict=True))
            loo = [mean(np.delete(delta, i)) for i in range(len(delta))]
            same(record["leave_one_seed_out_mean_range"], [min(loo), max(loo)])
            positive = [x for x in delta if x > 0]
            if positive:
                same(record["largest_positive_share"], max(positive) / math.fsum(positive))
            else:
                assert record["largest_positive_share"] is None
            same(
                record["sd_between_seed_effects"],
                math.sqrt(math.fsum((x - mean(delta)) ** 2 for x in delta) / (len(delta) - 1)),
            )
            for subset, prefix in (
                (rows, "state_sign_counts"),
                (
                    [
                        r
                        for r in rows
                        if r["new"]["spearman"] > r["old"]["spearman"]
                        and r["new"]["top1_auc"] > r["old"]["top1_auc"]
                    ],
                    "gain_signs_when_both_prediction_metrics_improved",
                ),
            ):
                ds = [r["new"]["hits_" + k] - r["old"]["hits_" + k] for r in subset]
                assert record[prefix] == {
                    "positive": sum(x > 0 for x in ds),
                    "zero": sum(x == 0 for x in ds),
                    "negative": sum(x < 0 for x in ds),
                }
                if prefix.startswith("gain"):
                    assert record["rho_and_auc_improved_states"] == len(ds)
    for contrast in data["cohort_contrasts"]:
        a, size, k = contrast["assay_id"], contrast["initial_sample_size"], str(contrast["k"])
        paired = {}
        for arm in ("old", "new"):
            v = [vectors[a, size, c, k, arm] for c in ("original", "replication")]
            d = [draws[a, size, c, k, arm] for c in ("original", "replication")]
            paired[arm] = d[1] - d[0]
            record = contrast["arm_cohort_changes"][arm]
            same(record["estimate"], mean(v[1]) - mean(v[0]))
            same(record["interval"], np.percentile(paired[arm], [0.3125, 99.6875]))
            counts["intervals"] += 1
        same(
            contrast["effect_gap"]["interval"],
            np.percentile(paired["new"] - paired["old"], [0.3125, 99.6875]),
        )
        same(
            contrast["effect_gap"]["estimate"],
            contrast["arm_cohort_changes"]["new"]["estimate"]
            - contrast["arm_cohort_changes"]["old"]["estimate"],
        )
        counts["intervals"] += 1
    for record in data["coverage_diagnostics"]:
        for group in record["groups"]:
            for c in ("original", "replication"):
                rows = [
                    r
                    for r in data["rows"]
                    if r["cohort"] == c
                    and all(r[k] == record[k] for k in ("assay_id", "initial_sample_size"))
                ]
                subset = [r for r in rows if bool(r["observed_high"]) == group["has_initial_high"]]
                actual = group[c]
                assert actual["count"] == len(subset)
                same(actual["fraction"], len(subset) / len(rows))
                if subset:
                    same(
                        actual["mean_delta"],
                        mean(
                            [
                                r["new"][f"hits_{record['k']}"] - r["old"][f"hits_{record['k']}"]
                                for r in subset
                            ]
                        ),
                    )
                else:
                    assert actual["mean_delta"] is None
        parts = record["decomposition"]
        if parts["status"] == "descriptive_only":
            cross = {}
            for weights_from in ("original", "replication"):
                for means_from in ("original", "replication"):
                    cross[weights_from, means_from] = math.fsum(
                        g[weights_from]["fraction"] * g[means_from]["mean_delta"]
                        for g in record["groups"]
                    )
            o, r = "original", "replication"
            same(
                parts["coverage_composition_term"],
                (cross[r, o] - cross[o, o] + cross[r, r] - cross[o, r]) / 2,
            )
            same(
                parts["within_stratum_term"],
                (cross[o, r] - cross[o, o] + cross[r, r] - cross[r, o]) / 2,
            )
            same(parts["cohort_gap"], cross[r, r] - cross[o, o])
            counts["coverage_terms"] += 3
    result = {
        "status": "PASS",
        **counts,
        "states": len(data["rows"]),
        "result_sha256": terminal["result_sha256"],
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "independent arithmetic implementation; not independent scientific approval",
    }
    with (folder / "verification.json").open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
