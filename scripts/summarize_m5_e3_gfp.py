"""Verify and summarize completed E3 aggregate rows; never open candidate truth."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

ASSAY_IDS = ("GFP_AEQVI_Sarkisyan_2016",)

MODELS = ("random-forest-sklearn-v1", "xgboost-cpu-v1")
DENOMINATORS = {ASSAY_IDS[0]: (259, 518, 1035)}
FRACTIONS = ("0.005", "0.01", "0.02")


def bootstrap(values, seed):
    """Independent weighted-count arithmetic for the runner's paired seed bootstrap."""
    cells = np.asarray(values, dtype=float).reshape(-1, 10)
    means = np.array([math.fsum(cells[:, i]) / len(cells) for i in range(10)])
    slots = np.random.Generator(np.random.PCG64(seed)).integers(0, 10, (20000, 10))
    weights = np.eye(10, dtype=np.int64)[slots].sum(axis=1)
    draws = weights @ means / 10
    return {
        "estimate": math.fsum(means) / 10,
        "interval": np.percentile(draws, [0.3125, 99.6875]).tolist(),
    }


def matched_interval(values, seed):
    """Resample seed slots, then 64 matched replicates within each state occurrence."""
    values = np.asarray(values).reshape(2, 10, 64)
    rng = np.random.Generator(np.random.PCG64(seed))
    slots = rng.integers(0, 10, (20000, 10))
    draws = []
    for start in range(0, 20000, 200):
        selected = values[:, slots[start : start + 200]].transpose(1, 0, 2, 3)
        indices = rng.integers(0, 64, selected.shape)
        sampled = np.take_along_axis(selected, indices, axis=3)
        draws.extend(sampled.mean(axis=(1, 2, 3)).tolist())
    return {
        "estimate": float(values.mean()),
        "interval": np.percentile(draws, [0.3125, 99.6875]).tolist(),
        "seed": seed,
        "draws": 20000,
        "coverage": 0.99375,
        "unit": "shared seed slots; matched replicates nested within state occurrence",
    }


def check_summary(actual, expected):
    assert math.isclose(actual["estimate"], expected["estimate"], abs_tol=1e-12)
    assert np.allclose(actual["interval"], expected["interval"], atol=1e-12, rtol=0)


def recall(branch):
    return branch["high_value"]["0.01"]["recall"]


def analyze(result):
    assert result["completed_states"] == 40 and result["evaluated_new_branches"] == 2640
    assert result["baseline_reconstruction"] == result["paired_initials"] == "PASS"
    rows = result["rows"]
    expected = {
        (a, m, n, s) for a in ASSAY_IDS for m in MODELS for n in (32, 64) for s in range(1729, 1739)
    }
    actual = {(r["assay_id"], r["model_id"], r["initial_sample_size"], r["run_seed"]) for r in rows}
    assert len(rows) == len(actual) == 40 and actual == expected
    checked = 0
    for row in rows:
        for version in ("old", "new"):
            state = row[version]
            for key in ("assay_id", "model_id", "initial_sample_size", "run_seed"):
                assert state[key] == row[key]
            branches = state["branches"]
            assert len(branches["matched_random"]) == 64
            for branch in [
                branches["greedy"],
                branches["exploration"],
                *branches["matched_random"],
            ]:
                for fraction, denominator in zip(
                    FRACTIONS, DENOMINATORS[row["assay_id"]], strict=True
                ):
                    metric = branch["high_value"][fraction]
                    assert 0 <= metric["count"] <= 16
                    assert abs(metric["recall"] - metric["count"] / denominator) < 1e-14
                    checked += 1
        for policy in ("greedy", "exploration"):
            delta = row["decomposition"][policy]
            assert delta["net_hits"] == delta["entered_hits"] - delta["exited_hits"]
            counts = [
                row[v]["branches"][policy]["high_value"]["0.01"]["count"] for v in ("old", "new")
            ]
            assert counts[1] - counts[0] == delta["net_hits"]
    summaries = []
    for summary in result["summaries"]:
        assay, model = summary["assay_id"], summary["model_id"]
        mi = MODELS.index(model)
        selected = sorted(
            [r for r in rows if r["assay_id"] == assay and r["model_id"] == model],
            key=lambda r: (r["initial_sample_size"], r["run_seed"]),
        )
        assert len(selected) == 20
        for name, metric in summary["prediction"].items():
            pairs = [(r["old_diagnostics"][name], r["new_diagnostics"][name]) for r in selected]
            if all(a is not None and b is not None for a, b in pairs):
                check_summary(metric["change"], bootstrap([b - a for a, b in pairs], 10600 + mi))
        for policy in ("greedy", "exploration"):
            for fraction in FRACTIONS:
                pairs = [
                    [
                        r[v]["branches"][policy]["high_value"][fraction]["recall"]
                        for v in ("old", "new")
                    ]
                    for r in selected
                ]
                key = policy + "_recall_" + fraction
                check_summary(
                    summary["fixed_policy_effects"][key],
                    bootstrap([b - a for a, b in pairs], 10600 + mi),
                )
            pairs = [
                [r[v]["branches"][policy]["best_found_improvement"] for v in ("old", "new")]
                for r in selected
            ]
            check_summary(
                summary["fixed_policy_effects"][policy + "_best"],
                bootstrap([b - a for a, b in pairs], 10600 + mi),
            )
        record = dict(summary)
        record["primary_denominator"] = DENOMINATORS[assay][1]
        record["auxiliary_contrasts"] = {}
        record["mean_hits"] = {}
        for version in ("old", "new"):
            batches = [r[version]["branches"] for r in selected]
            eg = [recall(b["exploration"]) - recall(b["greedy"]) for b in batches]
            em = [
                [recall(b["exploration"]) - recall(m) for m in b["matched_random"]] for b in batches
            ]
            record["auxiliary_contrasts"][version] = {
                "E-G": bootstrap(eg, 10600 + mi),
                "E-M": matched_interval(em, 10700 + mi),
            }
            record["mean_hits"][version] = {
                policy: float(np.mean([b[policy]["high_value"]["0.01"]["count"] for b in batches]))
                for policy in ("greedy", "exploration")
            }
        summaries.append(record)
    assert len(summaries) == 2
    return {
        "status": "AGGREGATE_CHECKS_PASS",
        "states": 40,
        "recall_count_checks": checked,
        "summaries": summaries,
        "limits": result["limits"],
        "application_delta": None,
        "auxiliary_method": "nested PCG64 bootstrap; not the legacy SHA-based stream",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--terminal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    terminal = json.loads(args.terminal.read_text())
    assert terminal["status"] == "succeeded" and terminal["mode"] == "full"
    sha = hashlib.sha256(raw).hexdigest()
    assert sha == terminal["artifacts"]["result.json.gz"]
    result = analyze(json.loads(gzip.decompress(raw)))
    result.update(
        input_sha256=sha,
        executed_source_sha256=terminal["source_sha256"],
        analysis_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "states", "recall_count_checks")}))


if __name__ == "__main__":
    main()
