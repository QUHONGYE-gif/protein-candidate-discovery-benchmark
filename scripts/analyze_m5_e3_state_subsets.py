"""Enumerate finite subsets of existing seed states; these ranges are not CIs."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import platform
import resource
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "docs/results/m5-e3-state-diagnosis-20260910/full-001/result.json"
INPUT_SHA = "5cbdedb4e35b2595345c992e1ba7d1573a12dd972ce2a30fc090f524d959cdec"
NOTE = ROOT / "docs/experiments/M5_E3_GENERALIZATION_PRECISION.md"
ASSAYS = ("PABP_YEAST_Melamed_2013", "HSP82_YEAST_Flynn_2019", "HXK4_HUMAN_Gersing_2022_activity")
KS = (8, 16, 32, 64)
PROTOCOL = "m5-e3-finite-state-subsets-development-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open("x") as f:
        json.dump(value, f, sort_keys=True, indent=2, allow_nan=False)
        f.write("\n")


def describe(d, index, original):
    values = d[index].mean(axis=1)
    n = index.shape[1]
    expected_var = float(d.var(ddof=1) * (1 / n - 1 / len(d)))
    if not math.isclose(float(values.var()), expected_var, abs_tol=1e-12, rel_tol=1e-12):
        raise ValueError("finite subset variance identity failed")
    return {
        "subset_size": n,
        "subsets": len(values),
        "source_blocks": len(d),
        "source_mean": float(d.mean()),
        "source_effect_sd": float(d.std(ddof=1)),
        "subset_mean": float(values.mean()),
        "subset_mean_variance": float(values.var()),
        "finite_variance_formula": expected_var,
        "central_95pct_subset_range": np.percentile(values, [2.5, 97.5]).tolist(),
        "min_max": [float(values.min()), float(values.max())],
        "positive_count": int((values > 0).sum()),
        "zero_count": int((values == 0).sum()),
        "negative_count": int((values < 0).sum()),
        "positive_fraction": float((values > 0).mean()),
        "original10_mean": original,
        "at_least_original_count": int((values >= original).sum()) if n == 10 else None,
        "at_least_original_fraction": float((values >= original).mean()) if n == 10 else None,
        "is_population_ci_or_p_value": False,
    }


def selftest():
    d = np.array([-1.0, 0, 0, 1, 2, 4])
    indices = np.array(list(itertools.combinations(range(6), 3)))
    out = describe(d, indices, 0.5)
    assert out["subsets"] == 20 and abs(out["subset_mean"] - d.mean()) < 1e-12
    assert out["positive_count"] + out["zero_count"] + out["negative_count"] == 20


def run():
    if sha(INPUT) != INPUT_SHA:
        raise ValueError("source aggregate mismatch")
    data = json.loads(INPUT.read_text())
    if data["completed_states"] != 180 or len(data["rows"]) != 180:
        raise ValueError("source state count mismatch")
    indices = {
        n: np.asarray(list(itertools.combinations(range(20), n)), dtype=np.int16)
        for n in (5, 10, 15)
    }
    blocks, summaries = [], []
    for assay in ASSAYS:
        for size in (32, 64, "combined"):
            cohorts = {}
            for cohort, seeds in (
                ("original", range(1729, 1739)),
                ("replication", range(2000, 2020)),
            ):
                cell = sorted(
                    [
                        r
                        for r in data["rows"]
                        if r["assay_id"] == assay
                        and r["cohort"] == cohort
                        and (size == "combined" or r["initial_sample_size"] == size)
                    ],
                    key=lambda r: (r["initial_sample_size"], r["run_seed"]),
                )
                expected = [
                    (n, s) for n in ((32, 64) if size == "combined" else (size,)) for s in seeds
                ]
                if [(r["initial_sample_size"], r["run_seed"]) for r in cell] != expected:
                    raise ValueError("cohort state matrix mismatch")
                delta = {}
                for k in KS:
                    values = np.array(
                        [r["new"][f"hits_{k}"] - r["old"][f"hits_{k}"] for r in cell], dtype=float
                    )
                    delta[str(k)] = values.reshape(-1, len(seeds)).mean(axis=0).tolist()
                cohorts[cohort] = {
                    "seed_order": list(seeds),
                    "delta_by_k": delta,
                    "raw_state_count": len(cell),
                    "both_arms_zero_raw_states": {
                        str(k): sum(
                            r["old"][f"hits_{k}"] == r["new"][f"hits_{k}"] == 0 for r in cell
                        )
                        for k in KS
                    },
                }
            blocks.append({"assay_id": assay, "initial_sample_size": size, "cohorts": cohorts})
            for k in KS:
                d = np.asarray(cohorts["replication"]["delta_by_k"][str(k)])
                original = float(np.mean(cohorts["original"]["delta_by_k"][str(k)]))
                for n in (5, 10, 15):
                    summaries.append(
                        {
                            "assay_id": assay,
                            "initial_sample_size": size,
                            "k": k,
                            **describe(d, indices[n], original),
                        }
                    )
    return {
        "protocol": PROTOCOL,
        "input_sha256": INPUT_SHA,
        "source_states": 180,
        "new_fits": 0,
        "new_labels": 0,
        "candidate_or_raw_truth_access": False,
        "blocks": blocks,
        "summaries": summaries,
        "limits": [
            "fixed observed20 catalog",
            "not population CI",
            "not p-value or prospective replication probability",
            "no measurement-noise attribution",
            "old and new cohorts not pooled",
            "no independence across assays or nested sizes",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    selftest()
    if args.self_test:
        print("finite subset variance and counting checks PASS")
        return
    if args.output is None:
        parser.error("output required")
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()

    def event(kind, **data):
        with (args.output / "attempts.jsonl").open("a") as f:
            f.write(
                json.dumps({"time": datetime.now(UTC).isoformat(), "event": kind, **data}) + "\n"
            )

    def stop(_signum, _frame):
        raise RuntimeError("local execution wall cap")

    signal.signal(signal.SIGALRM, stop)
    signal.alarm(60)
    status, failure = "failed", None
    event("started", source_sha256=sha(Path(__file__)), plan_sha256=sha(NOTE))
    write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "source_sha256": sha(Path(__file__)),
            "plan_sha256": sha(NOTE),
            "input_sha256": INPUT_SHA,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "wall_cap_seconds": 60,
            "subset_sizes": [5, 10, 15],
            "range_quantiles": [0.025, 0.975],
        },
    )
    try:
        result = run()
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1 if platform.system() == "Darwin" else 1024
        )
        if peak > 512 * 1024 * 1024:
            raise RuntimeError("local memory cap")
        write(args.output / "result.json", result)
        if sum(p.stat().st_size for p in args.output.iterdir()) > 5 * 1024 * 1024:
            raise RuntimeError("local output cap")
        status = "succeeded"
    except BaseException as exc:
        failure = type(exc).__name__
    finally:
        signal.alarm(0)
        event("finished", status=status, failure_class=failure)
        terminal = {
            "status": status,
            "failure_class": failure,
            "wall_seconds": time.monotonic() - started,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if platform.system() == "Darwin" else 1024),
            "artifacts": {p.name: sha(p) for p in args.output.iterdir() if p.is_file()},
        }
        write(args.output / "terminal.json", terminal)
        print(json.dumps(terminal))
    if status != "succeeded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
