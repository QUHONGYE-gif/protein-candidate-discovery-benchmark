"""E4 无真实标签前置资源检查；输出检查证据，不启动科学轨迹。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from pcdb.m6.e4_scorer import RFScorer
from pcdb.replay._m6_engine import Costs, State
from pcdb.replay.oracle import Observation

# 10 HF-only*48；40 fixed-ratio*144；40硬筛*48；40 cost-aware*288。
# 硬筛逐 LF 不拟合；warmup 不拟合；保守向上取整而非用观察到的策略行为降估。
SCORER_CALL_BOUND = 10 * 48 + 40 * 144 + 40 * 48 + 40 * 288


def resource_decision(seconds: float, elapsed: float) -> dict[str, object]:
    projected = seconds * SCORER_CALL_BOUND
    return {
        "scorer_call_bound": SCORER_CALL_BOUND,
        "projected_seconds": projected,
        "remaining_wall_seconds": 21600 - elapsed,
        "status": "PASS" if projected <= 0.8 * (21600 - elapsed) else "RESOURCE_STOP",
    }


def probe() -> dict[str, Any]:
    """最坏维度、独立 synthetic 随机数；无参数搜索、无真实 pilot。"""
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        if os.environ.get(name) != "1":
            raise ValueError(f"{name} must equal 1")
    rng = np.random.Generator(np.random.PCG64(1729))
    ids = tuple(f"synthetic-{i:03}" for i in range(256))
    x = rng.normal(size=(256, 420))
    h, lf_samples = rng.normal(size=64), rng.normal(size=256)
    state = State(
        ids,
        tuple(Observation(c, float(y)) for c, y in zip(ids, lf_samples, strict=True)),
        tuple(Observation(c, float(y)) for c, y in zip(ids[:64], h, strict=True)),
        Costs(1, 100),
        6656,
        7000,
    )
    # 这个状态仅测试最大拟合维度，不是合法 6400 预算科学轨迹。
    scorer = RFScorer(
        ids, x, task="synthetic-probe", seed=1729, initial_ids=ids[:16], warmup_ids=ids[:16]
    )
    start = time.monotonic()
    scorer(state).validate(ids)
    seconds = time.monotonic() - start
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = int(rss if sys.platform == "darwin" else rss * 1024)
    result = resource_decision(seconds, seconds)
    if peak > 1.5 * 1024**3:
        result["status"] = "RESOURCE_STOP"
    result.update(
        {
            "single_scorer_seconds": seconds,
            "peak_rss_bytes": peak,
            "truth_read": False,
            "scientific_trajectories": 0,
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    # 先建立唯一尝试目录；timeout/错误不能产生成功标记。
    args.output.mkdir(parents=True, exist_ok=False)
    start = datetime.now(UTC).isoformat()
    versions = {p: importlib.metadata.version(p) for p in ("numpy", "scikit-learn", "xgboost-cpu")}
    environment = {"python": sys.version, "platform": platform.platform(), "packages": versions}
    digest = hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest()
    report: dict[str, Any] = {
        "schema_version": "m6-e4-preflight-v1",
        "started_at": start,
        "source_commit": args.source_commit,
        "environment": environment,
        "environment_sha256": digest,
        "gate_a": "PENDING",
        "execution_authority": "user-directed-exception-20260913",
        "evidence_scope": "synthetic-resource-preflight-only",
    }

    def timeout(signum: int, frame: object) -> None:
        raise TimeoutError("synthetic resource probe exceeded 300 seconds")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(300)
    try:
        report.update(probe())
    except Exception as exc:
        report.update({"status": "PREFLIGHT_FAILED", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        signal.alarm(0)
    report["finished_at"] = datetime.now(UTC).isoformat()
    payload = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    (args.output / "report.json").write_bytes(payload)
    (args.output / "report.sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n")
    print(payload.decode(), end="")
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
