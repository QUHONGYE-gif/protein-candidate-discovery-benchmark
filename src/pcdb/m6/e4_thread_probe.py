"""同一无标签探针的串并行比较；不改变 RF 参数、bootstrap 或科学矩阵。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import signal
import statistics
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from pcdb.m6.e4_preflight import SCORER_CALL_BOUND
from pcdb.m6.e4_scorer import RFScorer
from pcdb.replay._m6_engine import Costs, State
from pcdb.replay.oracle import Observation


def benchmark() -> dict[str, Any]:
    if any(
        os.environ.get(k) != "1"
        for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
    ):
        raise ValueError("native thread limits must all equal 1")
    rng = np.random.Generator(np.random.PCG64(1729))
    ids = tuple(f"synthetic-{i:03}" for i in range(256))
    x = rng.normal(size=(256, 420))
    hf, lf = rng.normal(size=64), rng.normal(size=256)
    state = State(
        ids,
        tuple(Observation(c, float(y)) for c, y in zip(ids, lf, strict=True)),
        tuple(Observation(c, float(y)) for c, y in zip(ids[:64], hf, strict=True)),
        Costs(1, 100),
        6656,
        7000,
    )
    results: list[dict[str, Any]] = []
    reference = None
    for repetition, order in enumerate(((1, 2), (2, 1), (1, 2)), 1):
        for jobs in order:
            scorer = RFScorer(
                ids,
                x,
                task="synthetic-probe",
                seed=1729,
                initial_ids=ids[:16],
                warmup_ids=ids[:16],
                member_jobs=jobs,
            )
            wall, cpu = time.monotonic(), time.process_time()
            scores = scorer(state)
            seconds, cpu_seconds = time.monotonic() - wall, time.process_time() - cpu
            scores.validate(ids)
            digest = hashlib.sha256(
                json.dumps(
                    asdict(scores), sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            ).hexdigest()
            if reference is None:
                reference = digest
            if digest != reference:
                raise ValueError("threading changed scores")
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak = int(rss if sys.platform == "darwin" else rss * 1024)
            results.append(
                {
                    "repetition": repetition,
                    "member_jobs": jobs,
                    "wall_seconds": seconds,
                    "process_cpu_seconds": cpu_seconds,
                    "scores_sha256": digest,
                    "cumulative_peak_rss_bytes": peak,
                }
            )
            if peak > 1.5 * 1024**3:
                raise MemoryError("thread probe exceeded RSS cap")
    summaries: list[dict[str, Any]] = []
    for jobs in (1, 2):
        values = [r["wall_seconds"] for r in results if r["member_jobs"] == jobs]
        summaries.append(
            {
                "member_jobs": jobs,
                "median_seconds": statistics.median(values),
                "min_seconds": min(values),
                "max_seconds": max(values),
                "projected_median_hours": statistics.median(values) * SCORER_CALL_BOUND / 3600,
                "projected_max_hours": max(values) * SCORER_CALL_BOUND / 3600,
            }
        )
    speedup = summaries[0]["median_seconds"] / summaries[1]["median_seconds"]
    return {
        "rows": results,
        "summaries": summaries,
        "median_speedup": speedup,
        "scorer_call_bound": SCORER_CALL_BOUND,
        "scores_exactly_equal": True,
        "recommended_member_jobs": 2 if speedup > 1 else 1,
        "six_hour_cap_feasible": min(s["max_seconds"] for s in summaries) * SCORER_CALL_BOUND
        <= 0.8 * 21600,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    environment = {
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "packages": {
            k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")
        },
    }
    report: dict[str, Any] = {
        "schema_version": "m6-e4-thread-probe-v1",
        "source_commit": args.source_commit,
        "started_at": datetime.now(UTC).isoformat(),
        "environment": environment,
        "truth_read": False,
        "scientific_trajectories": 0,
        "gate_a": "PENDING",
        "evidence_scope": "synthetic-thread-performance-only",
    }

    def timeout(signum: int, frame: object) -> None:
        raise TimeoutError("thread probe exceeded 300 seconds")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(300)
    try:
        report.update(benchmark())
        report["status"] = "BENCHMARK_COMPLETE"
    except Exception as exc:
        report.update({"status": "BENCHMARK_FAILED", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        signal.alarm(0)
    report["finished_at"] = datetime.now(UTC).isoformat()
    payload = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    (args.output / "report.json").write_bytes(payload)
    (args.output / "report.sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n")
    print(payload.decode(), end="")
    if report["status"] != "BENCHMARK_COMPLETE":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
