"""Paired cutoff diagnostics from existing benign C aggregates; no candidate access."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import platform
import resource
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from summarize_m5_e3_prediction import ASSAY_IDS, DENOMINATORS, MODELS

INPUT_SHA = "67e98445f6c4fe3f6fd46a966b2af321122ea4c1cc8371a7eac81fef164715ba"
KS = (8, 16, 32, 64)
WINDOWS = ((0, 16), (16, 32), (32, 64))
SEEDS = tuple(range(1729, 1739))
ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "docs/results/m5-e3-frontier-20260909/result.json.gz"
NOTE = ROOT / "docs/experiments/M5_E3_BUDGET_EFFECTS.md"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def key(row):
    return tuple(row[k] for k in ("assay_id", "model_id", "initial_sample_size", "run_seed"))


def interval(values, seed):
    """Each bootstrap draw shares seed slots across all nested initial sizes."""
    cells = np.asarray(values, dtype=float).reshape(-1, 10)
    assert np.isfinite(cells).all()
    slots = np.random.Generator(np.random.PCG64(seed)).integers(0, 10, (20000, 10))
    draws = cells[:, slots].mean(axis=(0, 2))
    return {
        "estimate": float(cells.mean()),
        "interval": np.percentile(draws, [0.3125, 99.6875]).tolist(),
        "seed": seed,
        "draws": 20000,
        "coverage": 0.99375,
    }


def validate(data):
    rows = data["rows"]
    expected = {(a, m, n, s) for a in ASSAY_IDS for m in MODELS for n in (32, 64) for s in SEEDS}
    assert len(rows) == len({key(r) for r in rows}) == 120
    assert {key(r) for r in rows} == expected
    assert data["paired_initials"] == data["previous_reconstruction"] == "PASS"
    checks = 0
    for row in rows:
        identity = row["source_identity"]
        shas = [identity[k] for k in ("old_state_id", "new_state_id", "order_sha256")]
        for name in ("source_prediction_shas", "source_selection_shas"):
            assert len(identity[name]) == 2
            shas.extend(identity[name])
        assert all(len(s) == 64 and all(c in "0123456789abcdef" for c in s) for s in shas)
        old, new = (row[v + "_frontier"] for v in ("old", "new"))
        for name in ("remaining_high", "remaining_near", "remaining_far"):
            assert old[name] == new[name]
        for f in (old, new):
            previous = 0
            for k in KS:
                hits = f[f"hits_{k}"]
                assert type(hits) is int and previous <= hits <= min(k, f["remaining_high"])
                assert math.isclose(f[f"precision_{k}"], hits / k, abs_tol=1e-14)
                assert math.isclose(
                    f[f"recall_{k}"], hits / DENOMINATORS[row["assay_id"]][1], abs_tol=1e-14
                )
                previous = hits
                checks += 3
            for lower, upper in WINDOWS:
                count = f[f"hits_{upper}"] - (f[f"hits_{lower}"] if lower else 0)
                assert 0 <= count <= upper - lower
                checks += 1
    return checks


def arm_metrics(frontier):
    out = {
        f"{metric}_{k}": frontier[f"{metric}_{k}"]
        for metric in ("hits", "recall", "precision")
        for k in KS
    }
    for lower, upper in WINDOWS:
        name = f"window_{lower + 1}_{upper}"
        count = frontier[f"hits_{upper}"] - (frontier[f"hits_{lower}"] if lower else 0)
        out[name + "_hits"] = count
        out[name + "_rate"] = count / (upper - lower)
    out["precision64_minus_precision16"] = frontier["precision_64"] - frontier["precision_16"]
    return out


def core(row):
    old, new = (row[v + "_frontier"] for v in ("old", "new"))
    return (new["hits_64"] - old["hits_64"]) / 64 - (new["hits_16"] - old["hits_16"]) / 16


def analyze(data):
    checks = validate(data)
    summaries, consistency = [], []
    for assay in ASSAY_IDS:
        for mi, model in enumerate(MODELS):
            cell = sorted([r for r in data["rows"] if key(r)[:2] == (assay, model)], key=key)
            for size in (32, 64, "combined"):
                selected = [
                    r for r in cell if size == "combined" or r["initial_sample_size"] == size
                ]
                arms = [[arm_metrics(r[v + "_frontier"]) for r in selected] for v in ("old", "new")]
                metrics = {}
                for metric in arms[0][0]:
                    old, new = (np.array([a[metric] for a in arm]) for arm in arms)
                    metrics[metric] = {
                        "old": float(old.mean()),
                        "new": float(new.mean()),
                        "change": interval(new - old, 10800 + mi),
                    }
                summaries.append(
                    {
                        "assay_id": assay,
                        "model_id": model,
                        "initial_sample_size": size,
                        "paired_states": len(selected),
                        "independent_seed_slots": 10,
                        "metrics": metrics,
                    }
                )
            d32, d64 = [core(r) for r in cell[:10]], [core(r) for r in cell[10:]]
            consistency.append(
                {
                    "assay_id": assay,
                    "model_id": model,
                    "seed_order": list(SEEDS),
                    "D_initial32": d32,
                    "D_initial64": d64,
                    "D_initial64_minus_initial32": interval(np.subtract(d64, d32), 10800 + mi),
                }
            )
    return {
        "protocol": "m5-e3-budget-effects-development-v1",
        "completed_states": 120,
        "new_fits": 0,
        "new_labels": 0,
        "candidate_or_raw_truth_access": False,
        "input_sha256": INPUT_SHA,
        "input_count_and_rate_checks": checks,
        "summaries": summaries,
        "initial_size_consistency": consistency,
    }


def self_test():
    # Independent-row resampling would wrongly make this perfectly paired mean uncertain.
    paired = interval([0.0] * 10 + [2.0] * 10, 10800)
    assert paired["estimate"] == 1 and paired["interval"] == [1, 1]
    first = {f"hits_{k}": k // 8 for k in KS}
    second = {f"hits_{k}": k // 4 for k in KS}
    assert core({"old_frontier": first, "new_frontier": second}) == 0
    for f in (first, second):
        for k in KS:
            f[f"precision_{k}"] = f[f"hits_{k}"] / k
            f[f"recall_{k}"] = f[f"hits_{k}"] / 378
        m = arm_metrics(f)
        assert sum(m[f"window_{a + 1}_{b}_hits"] for a, b in WINDOWS) == f["hits_64"]
        assert m["window_17_32_rate"] == m["window_33_64_rate"] == m["precision_16"]
    return {
        "status": "PASS",
        "shared_seed_pairing": True,
        "normalized_effect": True,
        "window_identity": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started, clock = datetime.now(UTC).isoformat(), time.monotonic()
    source_paths = (
        Path(__file__),
        NOTE,
        ROOT / "scripts/summarize_m5_e3_prediction.py",
        ROOT / "scripts/m5c_benign_scope.py",
    )
    execution = {
        "started_at": started,
        "environment": "existing local Python; compact public aggregates only",
        "python": platform.python_version(),
        "numpy": np.__version__,
        "command": "python3 scripts/analyze_m5_e3_budget.py --output-dir ATTEMPT_DIR"
        + (" --self-test" if args.self_test else ""),
        "mode": "synthetic-smoke" if args.self_test else "full-aggregate-analysis",
        "source_sha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in source_paths},
        "input_sha256": digest(INPUT),
        "limits": {"cumulative_seconds": 900, "peak_mib": 1536, "output_mib": 256},
    }
    write_json(args.output_dir / "execution.json", execution)

    def deadline(_signum, _frame):
        raise TimeoutError("analysis deadline exceeded")

    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(900)
    terminal = {"status": "failed", "artifacts": {}}
    try:
        assert execution["input_sha256"] == INPUT_SHA
        result = (
            self_test()
            if args.self_test
            else analyze(json.loads(gzip.decompress(INPUT.read_bytes())))
        )
        raw = gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0)
        with (args.output_dir / "result.json.gz").open("xb") as handle:
            handle.write(raw)
        terminal["artifacts"]["result.json.gz"] = hashlib.sha256(raw).hexdigest()
        terminal["status"] = "succeeded"
    except Exception as error:
        terminal["error_type"] = type(error).__name__
        raise
    finally:
        signal.alarm(0)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mib = peak / (1024**2 if platform.system() == "Darwin" else 1024)
        terminal.update(
            finished_at=datetime.now(UTC).isoformat(),
            wall_seconds=time.monotonic() - clock,
            peak_mib=peak_mib,
        )
        if terminal["wall_seconds"] > 900 or peak_mib > 1536:
            terminal["status"] = "budget_exceeded"
        write_json(args.output_dir / "terminal.json", terminal)
        print(json.dumps(terminal))
    assert terminal["status"] == "succeeded"


if __name__ == "__main__":
    main()
