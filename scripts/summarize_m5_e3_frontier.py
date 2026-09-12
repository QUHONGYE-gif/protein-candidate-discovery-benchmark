"""Independently verify compact frontier outputs without candidate IDs or truth."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
from summarize_m5_e3_prediction import ASSAY_IDS, DENOMINATORS, MODELS, bootstrap


def key(row):
    return (row["assay_id"], row["model_id"], row["initial_sample_size"], row["run_seed"])


def check_interval(actual, values, seed):
    expected = bootstrap(values, seed)
    assert np.isclose(actual["estimate"], expected["estimate"], rtol=1e-12, atol=1e-12)
    assert np.allclose(actual["interval"], expected["interval"], rtol=1e-12, atol=1e-12)
    assert actual["seed"] == seed and actual["draws"] == 20000
    assert actual["coverage"] == 0.99375


def analyze(result, smoke, previous):
    rows = result["rows"]
    assert result["completed_states"] == 120 and result["new_mixed_branches"] == 120
    assert result["new_fits"] == 0 and result["previous_reconstruction"] == "PASS"
    wanted = {
        (a, m, n, s) for a in ASSAY_IDS for m in MODELS for n in (32, 64) for s in range(1729, 1739)
    }
    indexed, old = {key(r): r for r in rows}, {key(r): r for r in previous["rows"]}
    assert set(indexed) == wanted and len(indexed) == len(rows) == 120
    assert len(smoke["rows"]) == 6 and smoke["new_mixed_branches"] == 6
    assert all(r == indexed[key(r)] for r in smoke["rows"])
    metric_checks, intervals = 0, 0
    for row in rows:
        prior, denominator = old[key(row)], DENOMINATORS[row["assay_id"]][1]
        for version in ("old", "new"):
            f = row[version + "_frontier"]
            nnear, nfar = f["remaining_near"], f["remaining_far"]
            if f["auc_reconstructed"] is not None:
                assert (
                    abs(
                        f["auc_all"]
                        - (f["auc_near"] * nnear + f["auc_far"] * nfar) / (nnear + nfar)
                    )
                    < 1e-12
                )
            assert f["auc_all"] == prior[version + "_diagnostics"]["top1_auc"]
            assert (
                f["hits_16"] == prior[version]["branches"]["greedy"]["high_value"]["0.01"]["count"]
            )
            assert f["hits_16"] + f["top16_near"] + f["top16_far"] == 16
            for k in (8, 16, 32, 64):
                assert 0 <= f[f"hits_{k}"] <= min(k, f["remaining_high"])
                assert abs(f[f"recall_{k}"] - f[f"hits_{k}"] / denominator) < 1e-14
                assert abs(f[f"precision_{k}"] - f[f"hits_{k}"] / k) < 1e-14
                metric_checks += 3
            assert f["hits_8"] <= f["hits_16"] <= f["hits_32"] <= f["hits_64"]
            metric_checks += 5
        if row["model_id"] == MODELS[0]:
            assert row["factorial"] is None
        else:
            assert set(row["factorial"]) == {"00", "10", "01", "11"}
            for branch, version in (("00", "old"), ("11", "new")):
                assert row["factorial"][branch] == prior[version]["branches"]["exploration"]
            for b in row["factorial"].values():
                high = b["high_value"]["0.01"]
                assert 0 <= high["count"] <= 16
                assert abs(high["recall"] - high["count"] / denominator) < 1e-14
                metric_checks += 2
    assert len(result["summaries"]) == 6
    for summary in result["summaries"]:
        mi = MODELS.index(summary["model_id"])
        cell = sorted(
            [
                r
                for r in rows
                if r["assay_id"] == summary["assay_id"] and r["model_id"] == summary["model_id"]
            ],
            key=key,
        )
        assert len(cell) == 20
        for metric, record in summary["frontier"].items():
            pairs = [(r["old_frontier"][metric], r["new_frontier"][metric]) for r in cell]
            defined = sum(a is not None and b is not None for a, b in pairs)
            assert record["defined_pairs"] == defined
            if defined == 20:
                assert np.isclose(record["old"], np.mean([a for a, b in pairs]))
                assert np.isclose(record["new"], np.mean([b for a, b in pairs]))
                check_interval(record["change"], [b - a for a, b in pairs], 10800 + mi)
                intervals += 1
            else:
                assert record["old"] is record["new"] is record["change"] is None
        if mi == 0:
            assert summary["factorial"] is None
            continue
        for metric, record in summary["factorial"].items():
            v = {}
            for branch in ("00", "10", "01", "11"):
                b = [r["factorial"][branch] for r in cell]
                v[branch] = np.array(
                    [
                        x["best_found_improvement"]
                        if metric == "best_found_improvement"
                        else x["high_value"]["0.01"]["count" if metric == "hits" else "recall"]
                        for x in b
                    ]
                )
                assert np.isclose(record["branch_means"][branch], np.mean(v[branch]))
            effects = {
                "mean_given_old_uq": v["10"] - v["00"],
                "mean_given_new_uq": v["11"] - v["01"],
                "uq_given_old_mean": v["01"] - v["00"],
                "uq_given_new_mean": v["11"] - v["10"],
                "total": v["11"] - v["00"],
                "interaction": v["11"] - v["10"] - v["01"] + v["00"],
            }
            for effect, values in effects.items():
                check_interval(record["effects"][effect], values, 10901)
                intervals += 1
    return {
        "status": "PASS",
        "states": 120,
        "metric_checks": metric_checks,
        "independently_recomputed_intervals": intervals,
        "smoke_full_exact_states": 6,
        "raw_candidate_or_truth_access": False,
        "summaries": result["summaries"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "terminal", "smoke", "smoke-terminal", "previous", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    values = []
    for path, terminal in ((args.input, args.terminal), (args.smoke, args.smoke_terminal)):
        raw, meta = path.read_bytes(), json.loads(terminal.read_text())
        assert meta["status"] == "succeeded"
        assert hashlib.sha256(raw).hexdigest() == meta["artifacts"]["result.json.gz"]
        values.append(json.loads(gzip.decompress(raw)))
    previous = json.loads(gzip.decompress(args.previous.read_bytes()))
    result = analyze(*values, previous)
    result.update(
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
    )
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "summaries"}))


if __name__ == "__main__":
    main()
