"""Compact-only verification: weighted bootstrap and exact subset-sum counting."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ASSAYS = {
    "PABP_YEAST_Melamed_2013": 378,
    "HSP82_YEAST_Flynn_2019": 133,
    "HXK4_HUMAN_Gersing_2022_activity": 86,
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def close(a, b):
    if b is None:
        assert a is None
    else:
        assert a is not None and math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-11), (a, b)


def read(folder, zipped=True):
    t = json.loads((folder / "terminal.json").read_text())
    assert t["status"] == "succeeded"
    for name, digest in t["artifacts"].items():
        assert sha(folder / name) == digest
    name = "result.json.gz" if zipped else "result.json"
    raw = (folder / name).read_bytes()
    return json.loads(gzip.decompress(raw) if zipped else raw)


def subset_verification(folder):
    data = read(folder, False)
    ex = json.loads((folder / "execution.json").read_text())
    assert ex["source_sha256"] == sha(ROOT / "scripts/analyze_m5_e3_state_subsets.py")
    assert ex["plan_sha256"] == sha(ROOT / "docs/experiments/M5_E3_GENERALIZATION_PRECISION.md")
    source_path = ROOT / "docs/results/m5-e3-state-diagnosis-20260910/full-001/result.json"
    assert sha(source_path) == ex["input_sha256"] == data["input_sha256"]
    raw = json.loads(source_path.read_text())["rows"]
    assert len(data["blocks"]) == 9 and len(data["summaries"]) == 108
    source_checks = distributions = 0
    for b in data["blocks"]:
        for cohort, c in b["cohorts"].items():
            sizes = (
                (32, 64) if b["initial_sample_size"] == "combined" else (b["initial_sample_size"],)
            )
            cell = [
                r
                for r in raw
                if r["assay_id"] == b["assay_id"]
                and r["cohort"] == cohort
                and r["initial_sample_size"] in sizes
            ]
            assert c["raw_state_count"] == len(cell)
            for k in (8, 16, 32, 64):
                for i, seed in enumerate(c["seed_order"]):
                    own = [r for r in cell if r["run_seed"] == seed]
                    expected = math.fsum(
                        r["new"][f"hits_{k}"] - r["old"][f"hits_{k}"] for r in own
                    ) / len(sizes)
                    close(c["delta_by_k"][str(k)][i], expected)
                    source_checks += 1
                assert c["both_arms_zero_raw_states"][str(k)] == sum(
                    r["new"][f"hits_{k}"] == r["old"][f"hits_{k}"] == 0 for r in cell
                )
        for k in (8, 16, 32, 64):
            d = b["cohorts"]["replication"]["delta_by_k"][str(k)]
            doubled = [round(v * 2) for v in d]
            assert [v / 2 for v in doubled] == d
            dp = [Counter() for _ in range(16)]
            dp[0][0] = 1
            for seen, v in enumerate(doubled, 1):
                for n in range(min(seen, 15), 0, -1):
                    for total, count in list(dp[n - 1].items()):
                        dp[n][total + v] += count
            for n in (5, 10, 15):
                s = next(
                    x
                    for x in data["summaries"]
                    if (x["assay_id"], x["initial_sample_size"], x["k"], x["subset_size"])
                    == (b["assay_id"], b["initial_sample_size"], k, n)
                )
                pairs = sorted(dp[n].items())
                count = sum(c for t, c in pairs)
                assert count == math.comb(20, n) == s["subsets"]
                values = np.array([t / (2 * n) for t, c in pairs])
                weights = np.array([c for t, c in pairs], dtype=np.int64)
                mu = (
                    math.fsum(float(v) * int(w) for v, w in zip(values, weights, strict=True))
                    / count
                )
                variance = (
                    math.fsum(
                        (float(v) - mu) ** 2 * int(w) for v, w in zip(values, weights, strict=True)
                    )
                    / count
                )
                cum = np.cumsum(weights)
                for q, actual in zip((0.025, 0.975), s["central_95pct_subset_range"], strict=True):
                    position = q * (count - 1)
                    lo = math.floor(position)
                    hi = math.ceil(position)
                    a = values[np.searchsorted(cum, lo, side="right")]
                    z = values[np.searchsorted(cum, hi, side="right")]
                    close(actual, a + (z - a) * (position - lo))
                close(s["subset_mean"], mu)
                close(s["subset_mean_variance"], variance)
                close(s["source_mean"], math.fsum(d) / 20)
                sd = math.sqrt(math.fsum((v - math.fsum(d) / 20) ** 2 for v in d) / 19)
                close(s["source_effect_sd"], sd)
                close(s["finite_variance_formula"], sd * sd * (1 / n - 1 / 20))
                close(s["min_max"][0], values[0])
                close(s["min_max"][1], values[-1])
                for label, mask in (
                    ("positive", values > 0),
                    ("zero", values == 0),
                    ("negative", values < 0),
                ):
                    assert s[label + "_count"] == int(weights[mask].sum())
                close(s["positive_fraction"], int(weights[values > 0].sum()) / count)
                old = math.fsum(b["cohorts"]["original"]["delta_by_k"][str(k)]) / 10
                close(s["original10_mean"], old)
                if n == 10:
                    expected_count = int(weights[values >= old].sum())
                    assert s["at_least_original_count"] == expected_count
                    close(s["at_least_original_fraction"], expected_count / count)
                else:
                    assert s["at_least_original_count"] is s["at_least_original_fraction"] is None
                assert s["is_population_ci_or_p_value"] is False
                distributions += 1
    return {
        "source_block_effects": source_checks,
        "independent_dynamic_program_distributions": distributions,
        "enumeration_used_by_verifier": False,
    }


def generalization_verification(directory):
    full, smoke = read(directory / "full"), read(directory / "smoke")
    prior_path = ROOT / "docs/results/m5-e3-replication-20260910/full/result.json.gz"
    prior = json.loads(gzip.decompress(prior_path.read_bytes()))

    def key(r):
        return r["assay_id"], r["initial_sample_size"], r["run_seed"]

    old = {key(r): r for r in prior["rows"]}
    counts = baselines = intervals = means = undefined = 0
    for mode, data, seeds in (("full", full, range(2000, 2020)), ("smoke", smoke, (2000,))):
        folder = directory / mode
        ex = json.loads((folder / "execution.json").read_text())
        for field, path in (
            ("source_sha256", "scripts/run_m5_e3_generalization.py"),
            ("plan_sha256", "docs/experiments/M5_E3_GENERALIZATION_PRECISION.md"),
            ("helper_sha256", "scripts/run_m5_e3_prediction.py"),
            ("coverage_helper_sha256", "scripts/run_m5_e3_fixed_count_coverage.py"),
            ("replication_source_sha256", "scripts/run_m5_e3_replication.py"),
        ):
            assert ex[field] == sha(ROOT / path)
        assert ex["replication_result_sha256"] == sha(prior_path)
        expected = {(a, n, s) for a in ASSAYS for n in (32, 64) for s in seeds}
        assert len(data["rows"]) == data["completed_states"] == len(expected)
        assert {key(r) for r in data["rows"]} == expected and data["point_fits"] == 2 * len(
            expected
        )
        events = [json.loads(x) for x in (folder / "progress.jsonl").read_text().splitlines()]
        for assay in ASSAYS:
            names = [e["event"] for e in events if e.get("assay_id") == assay]
            assert names == ["predictions_saved"] * (2 * len(seeds)) + [
                "workers_exited",
                "assay_scored",
            ]
        for r in data["rows"]:
            assert r["baseline_reconstruction"] == "EXACT"
            assert r["label_cost"] == r["initial_sample_size"] + 16
            assert r["high_denominator"] == ASSAYS[r["assay_id"]]
            assert r["allowed_observation_sha256"] == old[key(r)]["allowed_observation_sha256"]
            assert r["baseline_prediction_sha256"] == old[key(r)]["prediction_sha256"]
            for a in ("xgb", "ridge"):
                m = r["metrics"][a]
                last = 0
                for k in (8, 16, 32, 64):
                    hits = m[f"hits_{k}"]
                    assert type(hits) is int and last <= hits <= min(k, r["remaining_high"])
                    close(m[f"recall_{k}"], hits / r["high_denominator"])
                    close(m[f"precision_{k}"], hits / k)
                    last = hits
                    counts += 3
                assert (
                    m["top16_high"] == m["hits_16"]
                    and sum(m["top16_" + c] for c in ("high", "near", "far")) == 16
                )
                close(m["greedy_recall"], m["recall_16"])
                assert (m["common_frontier_auc"] is None) == (
                    r["frontier_high_count"] in (0, r["frontier_count"])
                )
                counts += 4
            for metric, value in old[key(r)]["new"].items():
                close(r["metrics"]["xgb"][metric], value)
                baselines += 1
    keyed = {key(r): r for r in full["rows"]}
    for r in smoke["rows"]:
        for section in ("metrics", "training"):
            assert r[section] == keyed[key(r)][section]
    slots = np.random.Generator(np.random.PCG64(12901)).integers(0, 20, (20000, 20))
    weights = np.zeros((20000, 20), dtype=int)
    np.add.at(weights, (np.arange(20000)[:, None], slots), 1)
    assert len(full["summaries"]) == 9
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
        assert s["states"] == len(rows) and s["seed_slots"] == 20
        for section in ("metrics", "training"):
            assert set(s[section]) == set(rows[0][section]["xgb"])
            for metric, item in s[section].items():
                pairs = [(r[section]["xgb"][metric], r[section]["ridge"][metric]) for r in rows]
                defined = sum(a is not None and b is not None for a, b in pairs)
                assert item["defined_pairs"] == defined
                if defined != len(rows):
                    assert set(item) == {"defined_pairs"}
                    undefined += 1
                    continue
                close(item["before"], math.fsum(a for a, b in pairs) / len(rows))
                close(item["after"], math.fsum(b for a, b in pairs) / len(rows))
                means += 2
                cells = np.array([b - a for a, b in pairs]).reshape(-1, 20)
                d = np.array([math.fsum(cells[:, i]) / len(cells) for i in range(20)])
                c = item["change"]
                close(c["estimate"], math.fsum(d) / 20)
                for a, b in zip(
                    c["interval"], np.percentile(weights @ d / 20, [0.3125, 99.6875]), strict=True
                ):
                    close(a, b)
                assert (c["draws"], c["seed"], c["coverage"]) == (20000, 12901, 0.99375)
                intervals += 1
    return {
        "count_rate_checks": counts,
        "historical_metric_checks": baselines,
        "independent_intervals": intervals,
        "arm_mean_checks": means,
        "undefined_summaries": undefined,
        "smoke_full_scientific_states": 6,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = time.monotonic()
    result = {
        "status": "PASS",
        "generalization": generalization_verification(args.directory),
        "subsets": subset_verification(args.directory / "subsets"),
        "raw_truth_or_candidate_access": False,
        "scope": "independent compact arithmetic, not raw-data or scientific approval",
    }
    result.update(
        source_sha256=sha(Path(__file__)),
        full_sha256=sha(args.directory / "full/result.json.gz"),
        subsets_sha256=sha(args.directory / "subsets/result.json"),
        wall_seconds=time.monotonic() - start,
    )
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
