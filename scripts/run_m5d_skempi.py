#!/usr/bin/env python3
"""Owner-authorized M5D external replay and independent disk reconstruction."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import multiprocessing
import os
import platform
import subprocess
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np  # noqa: E402
from sklearn.ensemble import RandomForestRegressor  # noqa: E402
from xgboost import XGBRegressor  # noqa: E402

from pcdb.m2.models import m4_model_config_bytes, model_effective_parameters  # noqa: E402
from pcdb.replay.engine import bootstrap_draws  # noqa: E402
from pcdb.replay.oracle import HiddenOracle  # noqa: E402

PROTOCOL = "m5d-skempi-external-v1"
MODELS = ("random-forest-sklearn-v1", "xgboost-cpu-v1")
PROCESSING = "4a9f491fa71b3630466de094e19f5e7b51765ff75e7ed10a345eee6cbcb2efdd"
SPLIT = "b11beb72e951878d42f247478ad8e5bd0bca25a8eeb3fdb71350bd44171f7e72"
SOURCE = Path(__file__).resolve().parents[1]


def encode(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    )


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def identity(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encode(value))
    path.chmod(0o400)


def initial(ids, complex_id, seed, size):
    return sorted(ids, key=lambda cid: (digest([PROTOCOL, complex_id, seed, cid]), cid))[:size]


def controls(state_id, remaining, e, g):
    overlap = set(e) & set(g)
    return [
        sorted(
            overlap
            | set(
                sorted(
                    set(remaining) - set(g),
                    key=lambda cid: (digest([PROTOCOL, state_id, rep, cid]), cid),
                )[: 16 - len(overlap)]
            )
        )
        for rep in range(64)
    ]


def select(task):
    """Process API intentionally contains only observables and revealed observations."""
    state, ids, matrix, observations = task
    ids = list(ids)
    expected = initial(ids, state["complex"], state["seed"], state["initial_size"])
    observed = {obs.candidate_id: obs.label for obs in observations}
    if len(observed) != len(observations) or set(observed) != set(expected):
        raise ValueError("revealed prefix mismatch")
    values = np.asarray(matrix, dtype=float)
    if values.shape != (len(ids), 41) or not np.isfinite(values).all():
        raise ValueError("observable feature shape/values")
    index = {cid: i for i, cid in enumerate(ids)}
    ordered = sorted(observed)
    x = values[[index[cid] for cid in ordered]]
    y = np.array([observed[cid] for cid in ordered])
    remaining = [cid for cid in ids if cid not in observed]
    pool_x = values[[index[cid] for cid in remaining]]
    model = state["model"]
    if model not in MODELS:
        raise ValueError("unfrozen model")

    def predict(train_x, train_y):
        cls = RandomForestRegressor if model == MODELS[0] else XGBRegressor
        estimator = cls(**dict(model_effective_parameters(model)))
        estimator.fit(train_x, train_y)
        prediction = np.asarray(estimator.predict(pool_x), dtype=float)
        if prediction.shape != (len(remaining),) or not np.isfinite(prediction).all():
            raise ValueError("invalid predictions")
        return prediction

    mean = predict(x, y)
    members = []
    for draw in bootstrap_draws(
        protocol_id=PROTOCOL,
        run_seed=state["seed"],
        task_id=state["complex"],
        model_id=model,
        round_index=1,
        observation_count=len(y),
    ):
        indices = np.array(draw.indices)
        members.append(predict(x[indices], y[indices]))
    std = np.std(members, axis=0, ddof=0)

    def top(scores):
        return [remaining[i] for i in np.lexsort((np.array(remaining), -scores))[:16]]

    e, g = top(std if model == MODELS[0] else mean + std), top(mean)
    return {
        "state": state,
        "initial": expected,
        "E": e,
        "G": g,
        "M": controls(digest(state), remaining, e, g),
    }


def load_inputs(root):
    if identity(root / "processing-manifest.json") != PROCESSING:
        raise ValueError("processing manifest drift")
    if identity(root / "split-manifest.json") != SPLIT:
        raise ValueError("split manifest drift")
    manifest = json.loads((root / "processing-manifest.json").read_bytes())
    for record in manifest["output_files"]:
        path = root / Path(record["object_key"]).name
        if path.is_symlink() or identity(path) != record["sha256"]:
            raise ValueError("input checksum mismatch")
        if path.stat().st_size != record["size_bytes"]:
            raise ValueError("input size mismatch")
    candidates = json.loads((root / "candidates.json").read_bytes())
    features = json.loads((root / "features.json").read_bytes())
    with (root / "membership.csv").open() as stream:
        membership = list(csv.DictReader(stream))
    ids = [row["candidate_id"] for row in candidates]
    if len(set(ids)) != len(ids) or len(ids) != 3811:
        raise ValueError("candidate uniqueness/count")
    for rows in (features, membership):
        if len(rows) != len(ids) or {row["candidate_id"] for row in rows} != set(ids):
            raise ValueError("input alignment")
    if any(row["partition"] != "external-candidate" for row in membership):
        raise ValueError("external partition drift")
    groups = {row["candidate_id"]: row["group_id"] for row in membership}
    feature_map = {row["candidate_id"]: row["values"] for row in features}
    pools = defaultdict(list)
    for row in candidates:
        pools[row["complex_id"]].append(row["candidate_id"])
    cohort = []
    for complex_id, pool in sorted(pools.items()):
        if len(pool) < 80:
            continue
        group_set = {groups[cid] for cid in pool}
        if len(group_set) != 1:
            raise ValueError("complex crosses leakage groups")
        cohort.append(
            {
                "complex": complex_id,
                "group": next(iter(group_set)),
                "candidate_count": len(pool),
                "ids": sorted(pool),
            }
        )
    if len(cohort) != 9 or len({row["group"] for row in cohort}) != 4:
        raise ValueError("prospective cohort count drift")
    states = [
        {
            "complex": row["complex"],
            "group": row["group"],
            "pool_sha256": digest(row["ids"]),
            "model": model,
            "initial_size": size,
            "seed": seed,
        }
        for row in cohort
        for model in MODELS
        for size in (32, 64)
        for seed in range(1729, 1739)
    ]
    return cohort, states, feature_map, set(ids)


def read_truth(root, ids):
    rows = json.loads((root / "hidden-truth.json").read_bytes())
    truth = {row["candidate_id"]: row["target"] for row in rows}
    if len(truth) != len(rows) or set(truth) != ids:
        raise ValueError("truth alignment")
    HiddenOracle(truth)  # finite numeric validation
    return truth


def evaluate(selection, pool, truth):
    state = selection["state"]
    initial_ids = initial(pool, state["complex"], state["seed"], state["initial_size"])
    if selection["initial"] != initial_ids:
        raise ValueError("persisted initial mismatch")
    remaining = set(pool) - set(initial_ids)
    e, g, matched = selection["E"], selection["G"], selection["M"]
    if matched != controls(digest(state), remaining, e, g):
        raise ValueError("matched controls drift")
    branches = [e, g, *matched]
    if len(branches) != 66 or any(
        len(b) != 16 or len(set(b)) != 16 or not set(b) <= remaining for b in branches
    ):
        raise ValueError("invalid/disjoint branch selection")
    values = sorted((truth[cid] for cid in pool), reverse=True)
    memberships = {
        str(f): {cid for cid in pool if truth[cid] >= values[math.ceil(f * len(pool)) - 1]}
        for f in (0.005, 0.01, 0.02)
    }
    baseline = max(truth[cid] for cid in initial_ids)
    metrics = []
    for branch in branches:
        oracle = HiddenOracle({cid: truth[cid] for cid in pool})
        oracle.reveal(initial_ids)
        observed = oracle.reveal(branch)
        best = max(baseline, *(obs.label for obs in observed)) - baseline
        metrics.append(
            {
                "recall": {
                    f: len(set(branch) & members) / len(members)
                    for f, members in memberships.items()
                },
                "best_improvement": best,
                "simple_regret_reduction": best,
            }
        )
    return {
        "state": state,
        "overlap": len(set(e) & set(g)),
        "high_value_counts": {f: len(m) for f, m in memberships.items()},
        "branches": metrics,
    }


def summarize(rows):
    cells = defaultdict(list)
    for row in rows:
        state, b = row["state"], row["branches"]
        effects = {}
        for f in ("0.005", "0.01", "0.02"):
            effects[f"recall_{f}_E-G"] = b[0]["recall"][f] - b[1]["recall"][f]
            effects[f"recall_{f}_E-M"] = b[0]["recall"][f] - float(
                np.mean([m["recall"][f] for m in b[2:]])
            )
        for comparator in ("G", "M"):
            other = (
                b[1]["best_improvement"]
                if comparator == "G"
                else float(np.mean([m["best_improvement"] for m in b[2:]]))
            )
            effects[f"best_improvement_E-{comparator}"] = b[0]["best_improvement"] - other
        cells[(state["group"], state["complex"], state["model"], state["initial_size"])].append(
            effects
        )
    cell_rows = []
    for (group, complex_id, model, size), values in sorted(cells.items()):
        if len(values) != 10:
            raise ValueError("incomplete seed cell")
        cell_rows.append(
            {
                "group": group,
                "complex": complex_id,
                "model": model,
                "initial_size": size,
                "effects": {key: float(np.mean([v[key] for v in values])) for key in values[0]},
            }
        )
    group_rows = []
    for group in sorted({r["group"] for r in cell_rows}):
        for model in MODELS:
            subset = [
                r["effects"] for r in cell_rows if r["group"] == group and r["model"] == model
            ]
            group_rows.append(
                {
                    "group": group,
                    "model": model,
                    "effects": {key: float(np.mean([r[key] for r in subset])) for key in subset[0]},
                }
            )
    draws = np.random.Generator(np.random.PCG64(104105)).integers(0, 4, size=(20000, 4))
    summaries = []
    for model in MODELS:
        group_values = [r["effects"] for r in group_rows if r["model"] == model]
        effects = {}
        for key in group_values[0]:
            values = np.array([r[key] for r in group_values])
            interval = np.quantile(
                values[draws].mean(axis=1), [0.003125, 0.996875], method="linear"
            )
            effects[key] = {"estimate": float(values.mean()), "interval": interval.tolist()}
        summaries.append(
            {
                "model": model,
                "effects": effects,
                "both_primary_lower_bounds_positive": all(
                    effects[f"recall_0.01_E-{c}"]["interval"][0] > 0 for c in ("G", "M")
                ),
            }
        )
    return {
        "protocol": PROTOCOL,
        "evidence_stage": "FORMAL_EXTERNAL_EXECUTED_PENDING_REVIEW",
        "independent_review": "PENDING",
        "scientific_gate": "NOT_ASSESSED",
        "states": len(rows),
        "branches": len(rows) * 66,
        "complex_count": 9,
        "group_count": 4,
        "bootstrap_draws": 20000,
        "interval_coverage": 0.99375,
        "models": summaries,
        "groups": group_rows,
        "cells": cell_rows,
        "limitations": [
            "Only four conservative leakage groups; intervals are descriptive.",
            "No independent sequence-homology audit; no universal mechanism claim.",
            "Within-complex fixed-budget replay; no cross-complex prediction claim.",
        ],
    }


def reconstruct(root, output, cohort, states, truth):
    saved = json.loads((output / "private/selections.json").read_bytes())
    if [item["state"] for item in saved] != states:
        raise ValueError("saved state exact-cover mismatch")
    pools = {row["complex"]: row["ids"] for row in cohort}
    rows = [evaluate(item, pools[item["state"]["complex"]], truth) for item in saved]
    report = summarize(rows)
    if encode(rows) != (output / "private/branch-metrics.json").read_bytes():
        raise ValueError("branch metrics disk reconstruction mismatch")
    if encode(report) != (output / "report.json").read_bytes():
        raise ValueError("aggregate disk reconstruction mismatch")
    return report


def rss_tree(pid):
    total, pending, seen = 0, [pid], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            total += sum(
                int(line.split()[1]) * 1024
                for line in Path(f"/proc/{current}/status").read_text().splitlines()
                if line.startswith("VmRSS:")
            )
            pending.extend(
                map(int, Path(f"/proc/{current}/task/{current}/children").read_text().split())
            )
        except FileNotFoundError:
            pass
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    cohort, states, features, ids = load_inputs(args.input_root)
    if args.verify:
        report = reconstruct(
            args.input_root, args.output, cohort, states, read_truth(args.input_root, ids)
        )
        terminal = json.loads((args.output / "terminal.json").read_bytes())
        for name, sha in terminal["artifacts"].items():
            if identity(args.output / name) != sha:
                raise ValueError("sealed artifact drift")
        if json.loads((args.output / "_SUCCESS").read_bytes())["terminal_sha256"] != identity(
            args.output / "terminal.json"
        ):
            raise ValueError("success marker drift")
        print(json.dumps({"readback": "PASS", "states": report["states"]}))
        return
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=SOURCE):
        raise ValueError("source must be clean")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    peak = [0]
    stop = threading.Event()

    def monitor():
        while not stop.wait(0.5):
            peak[0] = max(peak[0], rss_tree(os.getpid()))
            size = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
            if peak[0] > 1610612736 or time.monotonic() - started > 7200 or size > 1073741824:
                write(args.output / "RESOURCE_FAILURE.json", {"rss": peak[0], "bytes": size})
                os.killpg(os.getpgrp(), 15)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        write(
            args.output / "run-spec.json",
            {
                "protocol": PROTOCOL,
                "source_commit": head,
                "contract_sha256": identity(SOURCE / "docs/experiments/M5D_FORMAL_EXECUTION.md"),
                "processing_sha256": PROCESSING,
                "split_sha256": SPLIT,
                "models": {m: hashlib.sha256(m4_model_config_bytes(m)).hexdigest() for m in MODELS},
                "environment": {
                    p: importlib.metadata.version(p)
                    for p in ("numpy", "scikit-learn", "xgboost-cpu")
                },
                "python": subprocess.check_output(
                    [os.sys.executable, "--version"], text=True
                ).strip(),
                "cohort": cohort,
                "states": states,
                "authority_server": "129.28.97.247",
                "hostname": platform.node(),
                "started_utc": datetime.now(UTC).isoformat(),
                "platform": platform.platform(),
                "environment_freeze_sha256": hashlib.sha256(
                    subprocess.check_output([os.sys.executable, "-m", "pip", "freeze", "--all"])
                ).hexdigest(),
                "workers": 1,
                "native_threads": 1,
                "wall_seconds_limit": 7200,
                "rss_bytes_limit": 1610612736,
                "scratch_bytes_limit": 1073741824,
            },
        )
        # spawn never inherits the evaluator's hidden-truth memory.
        with multiprocessing.get_context("spawn").Pool(1) as pool:
            truth = read_truth(args.input_root, ids)
            selections, rows = [], []
            pools = {row["complex"]: row["ids"] for row in cohort}
            for state in states:
                candidate_ids = pools[state["complex"]]
                oracle = HiddenOracle({cid: truth[cid] for cid in candidate_ids})
                observations = oracle.reveal(
                    initial(candidate_ids, state["complex"], state["seed"], state["initial_size"])
                )
                selected = pool.apply(
                    select,
                    (
                        (
                            state,
                            candidate_ids,
                            [features[cid] for cid in candidate_ids],
                            observations,
                        ),
                    ),
                )
                selections.append(selected)
                rows.append(evaluate(selected, candidate_ids, truth))
                with (args.output / "progress.jsonl").open("a") as stream:
                    stream.write(
                        json.dumps(
                            {"completed_states": len(rows), "elapsed": time.monotonic() - started}
                        )
                        + "\n"
                    )
        write(args.output / "private/selections.json", selections)
        write(args.output / "private/branch-metrics.json", rows)
        write(args.output / "report.json", summarize(rows))
        reconstruct(args.input_root, args.output, cohort, states, read_truth(args.input_root, ids))
        artifacts = {
            str(p.relative_to(args.output)): identity(p)
            for p in sorted(args.output.rglob("*"))
            if p.is_file()
        }
        write(
            args.output / "terminal.json",
            {
                "status": "COMPLETE",
                "readback": "PASS",
                "source_commit": head,
                "states": len(rows),
                "branches": len(rows) * 66,
                "elapsed_seconds": time.monotonic() - started,
                "peak_tree_rss_bytes": peak[0],
                "cos_writes": 0,
                "independent_review": "PENDING",
                "artifacts": artifacts,
            },
        )
        write(
            args.output / "_SUCCESS", {"terminal_sha256": identity(args.output / "terminal.json")}
        )
        print((args.output / "terminal.json").read_text())
    except Exception as exc:
        write(
            args.output / "FAILURE.json",
            {
                "status": "FAILED",
                "failure_type": type(exc).__name__,
                "reason": str(exc),
                "source_commit": head,
            },
        )
        raise
    finally:
        stop.set()
        thread.join()


if __name__ == "__main__":
    main()
