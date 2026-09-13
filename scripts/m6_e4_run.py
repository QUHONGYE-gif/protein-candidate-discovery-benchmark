"""四 assay E4 实验内部运行器；76小时、有界真实执行、独立决策回读。"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import resource
import runpy
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from pcdb.m2.features import (
    CandidateSequence,
    build_sequence_feature_rows,
    sequence_feature_definitions,
)
from pcdb.m6.e4 import ProxySpec, generate_proxy
from pcdb.m6.e4_policy import make_e4_policy
from pcdb.m6.e4_scorer import RFScorer, domain_seed
from pcdb.m6.e4_thread_probe import benchmark
from pcdb.m6.policies import STRATEGIES, Scores
from pcdb.replay._m6_engine import Costs, evaluate, replay

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs/results/m6-e4-proteingym-inputs-20260913"
OPERATION = ROOT / "configs/experiments/m6/e4-operation-v3.json"
CONDITIONS = (
    ("C0", 0.8, False, 1),
    ("C1", 0.2, False, 1),
    ("C2", 0.8, True, 1),
    ("C3", 0.8, False, 4),
)


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    with path.open("xb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def read(path):
    return json.loads(path.read_bytes())


def source_identity():
    return {
        str(p.relative_to(ROOT)): sha(p)
        for base in ("src", "scripts", "configs", "docs")
        for p in (ROOT / base).rglob("*")
        if p.is_file() and p.suffix in (".py", ".json", ".md")
    }


def state_hash(state):
    return hashlib.sha256(canonical(asdict(state))).hexdigest()


def matrix(tasks, seeds):
    return [(task, seed, "HF", "hf_only") for task in tasks for seed in seeds] + [
        (task, seed, condition, strategy)
        for task in tasks
        for seed in seeds
        for condition, *_ in CONDITIONS
        for strategy in STRATEGIES
        if strategy != "hf_only"
    ]


def initial_ids(ids, task, seed):
    return tuple(
        sorted(
            ids,
            key=lambda c: (
                hashlib.sha256(f"m6-e4-initial-v1:{task}:{seed}:{c}".encode()).hexdigest(),
                c,
            ),
        )[:16]
    )


def summary(evaluations, tasks, seeds):
    rows = []
    directions = {}
    for task in tasks:
        for condition, *_ in CONDITIONS:
            for strategy in STRATEGIES[1:]:
                base = [evaluations[(task, s, "HF", "hf_only")] for s in seeds]
                test = [evaluations[(task, s, condition, strategy)] for s in seeds]
                deltas = [
                    t["0.01"]["checkpoints"]["6400"]["recall"]
                    - b["0.01"]["checkpoints"]["6400"]["recall"]
                    for b, t in zip(base, test, strict=True)
                ]
                mean = sum(deltas) / len(deltas)
                direction = (
                    "positive"
                    if mean >= 0.05 and sum(v > 0 for v in deltas) >= 4
                    else "negative"
                    if mean <= -0.05 and sum(v < 0 for v in deltas) >= 4
                    else "unstable"
                    if min(deltas) < 0 < max(deltas)
                    else "inconclusive"
                )
                directions[(task, condition, strategy)] = (direction, deltas)
                savings = []
                bc = tc = 0
                for b, t in zip(base, test, strict=True):
                    bh, th = b["0.01"]["cost_to_target"], t["0.01"]["cost_to_target"]
                    bc += bh is None
                    tc += th is None
                    if bh is not None and th is not None:
                        savings.append(1 - th / bh)
                rows.append(
                    dict(
                        task=task,
                        condition=condition,
                        strategy=strategy,
                        seeds=list(seeds),
                        paired_delta_recall=deltas,
                        mean_delta_recall=mean,
                        direction=direction,
                        negative_seed_fraction=sum(v < 0 for v in deltas) / len(deltas),
                        baseline_censored=bc,
                        treatment_censored=tc,
                        both_reached=len(savings),
                        savings_both_reached=savings,
                        mean_savings_both_reached=sum(savings) / len(savings) if savings else None,
                    )
                )
    mechanisms = []
    for task in tasks:
        d0, v0 = directions[(task, "C0", "cost_aware")]
        d2, v2 = directions[(task, "C2", "cost_aware")]
        delta = [b - a for a, b in zip(v0, v2, strict=True)]
        mechanisms.append(
            dict(
                task=task,
                paired_c2_minus_c0=delta,
                supports=d0 == "positive" and d2 == "negative" and sum(v < 0 for v in delta) >= 4,
            )
        )
    supports = sum(r["supports"] for r in mechanisms)
    conflicting = any(
        "unstable" in {directions[(t, c, "cost_aware")][0] for t in tasks}
        or {"positive", "negative"}.issubset({directions[(t, c, "cost_aware")][0] for t in tasks})
        for c in ("C0", "C2")
    )
    verdict = (
        "SUPPORTS_BOUNDED_HYPOTHESIS"
        if supports == len(tasks)
        else "UNSTABLE / REVISE"
        if supports or conflicting
        else "INCONCLUSIVE / STOP_CURRENT_MATRIX"
    )
    return dict(
        contrasts=rows,
        mechanisms=mechanisms,
        verdict=verdict,
        unique_trajectories=len(evaluations),
        logical_comparisons=len(tasks) * len(seeds) * 16,
        gate_a="PENDING",
        evidence_scope="development-directional",
    )


class Limits:
    def __init__(self, output, operation, start):
        self.output, self.operation, self.start = output, operation, start
        self.child = None

    def check(self):
        if time.monotonic() - self.start >= self.operation["wall_budget_seconds"]:
            raise TimeoutError("wall budget exceeded")
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != "darwin":
            rss *= 1024
        if self.child is not None and sys.platform == "linux":
            status = Path(f"/proc/{self.child.pid}/status")
            try:
                rss += next(
                    int(line.split()[1]) * 1024
                    for line in status.read_text().splitlines()
                    if line.startswith("VmRSS:")
                )
            except (FileNotFoundError, ProcessLookupError, StopIteration):
                if self.child.poll() is None:
                    raise RuntimeError("unable to measure readback child RSS") from None
        if rss > self.operation["peak_rss_bytes"]:
            raise MemoryError("RSS budget exceeded")
        if (
            sum(p.stat().st_size for p in self.output.rglob("*") if p.is_file())
            > self.operation["artifact_bytes"]
        ):
            raise OSError("artifact budget exceeded")


def prepare(data_root, output, operation, limits):
    actual = runpy.run_path(str(AUDIT / "audit.py"))["audit"](data_root)
    expected = read(AUDIT / "report.json")
    if (
        actual["assays"] != expected["assays"]
        or actual["receipt_sha256"] != expected["receipt_sha256"]
    ):
        raise ValueError("published observable input identity drifted")
    write(output / "observable-audit.json", actual)
    folder = output / "inputs"
    folder.mkdir()
    for audited in actual["assays"]:
        limits.check()
        task = audited["assay_id"]
        metadata = next(
            f for f in audited["files"] if f["relative_path"].endswith("/processing-manifest.json")
        )
        processing_path = data_root / metadata["relative_path"]
        processing = read(processing_path)
        parent = processing_path.parent
        with gzip.open(parent / "membership.csv.gz", "rt") as stream:
            candidates = [r["candidate_id"] for r in csv.DictReader(stream)]
        selected = sorted(
            candidates,
            key=lambda c: (hashlib.sha256(f"m6-e4-pool-v1:{task}:{c}".encode()).hexdigest(), c),
        )[:256]
        if (
            hashlib.sha256(json.dumps(selected, separators=(",", ":")).encode()).hexdigest()
            != audited["ordered_subset_ids_sha256"]
        ):
            raise ValueError("subset identity drifted")
        ids = tuple(sorted(selected))
        selected_set = set(ids)
        records = {}
        with gzip.open(parent / "candidates.csv.gz", "rt") as stream:
            for row in csv.DictReader(stream):
                if row["candidate_id"] in selected_set:
                    records[row["candidate_id"]] = CandidateSequence(
                        row["candidate_id"], row["mutated_sequence"]
                    )
        feature_rows = build_sequence_feature_rows([records[c] for c in ids])
        names = [d["feature_id"] for d in sequence_feature_definitions()]
        x = np.asarray([[r[n] for n in names] for r in feature_rows], dtype=np.float64)
        target = folder / task
        target.mkdir()
        write(target / "ids.json", ids)
        write(
            target / "feature-definitions.json", [dict(d) for d in sequence_feature_definitions()]
        )
        with (target / "features.npy").open("xb") as stream:
            np.save(stream, x, allow_pickle=False)
        # ID与特征已固定；仅模拟器/回放owner转换子集HF，模型没有此文件路径。
        truth_entry = next(
            f
            for f in processing["output_files"]
            if f["object_key"].endswith("/hidden-truth.csv.gz")
        )
        truth_path = parent / "hidden-truth.csv.gz"
        write(
            target / "truth-access-started.json",
            dict(task=task, started_at=datetime.now(UTC).isoformat(), gate_a="PENDING"),
        )
        if (
            sha(truth_path) != truth_entry["sha256"]
            or truth_path.stat().st_size != truth_entry["size_bytes"]
        ):
            raise ValueError("HF bytes mismatch")
        truth = {}
        count = 0
        with gzip.open(truth_path, "rt") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != ["candidate_id", "DMS_score"]:
                raise ValueError("HF schema mismatch")
            for row in reader:
                count += 1
                c = row["candidate_id"]
                if c in selected_set:
                    if c in truth:
                        raise ValueError("duplicate subset HF")
                    truth[c] = float(row["DMS_score"])
        if (
            set(truth) != selected_set
            or count != truth_entry["row_count"]
            or not all(math.isfinite(v) for v in truth.values())
        ):
            raise ValueError("HF coverage/nonfinite mismatch")
        write(target / "hf.json", truth)
        write(
            target / "identity.json",
            dict(
                parent_audit=audited,
                hf_parent=truth_entry,
                files={p.name: sha(p) for p in target.iterdir() if p.is_file()},
            ),
        )
        for seed in operation["seeds"]:
            for condition, rho, reverse, _ in CONDITIONS:
                proxy = generate_proxy(
                    truth,
                    ProxySpec(rho, 0.1, reverse, domain_seed(f"m6-e4-proxy-v1:{task}:{seed}")),
                )
                write(
                    target / f"{seed}-{condition}.json",
                    dict(
                        values={c: 16 * v for c, v in proxy.values.items()},
                        pearson=proxy.pearson,
                        pearson_before=generate_proxy(
                            truth,
                            ProxySpec(
                                rho, 0.1, False, domain_seed(f"m6-e4-proxy-v1:{task}:{seed}")
                            ),
                        ).pearson,
                        hf_unique_count=len(set(truth.values())),
                        lf_unique_count=len(set(proxy.values.values())),
                        high_tail_count=sum(
                            v >= sorted(truth.values(), reverse=True)[25] for v in truth.values()
                        ),
                        high_tail_spearman=proxy.high_tail_spearman,
                    ),
                )
        limits.check()
    write(
        output / "input-checksums.json",
        {str(p.relative_to(folder)): sha(p) for p in folder.rglob("*") if p.is_file()},
    )


def evaluation_record(trace, truth):
    result = {}
    for fraction in (0.005, 0.01, 0.02):
        value = evaluate(trace, truth, high_value_fraction=fraction, target_recall=0.5)
        threshold = sorted(truth.values(), reverse=True)[math.ceil(len(truth) * fraction) - 1]
        result[str(fraction)] = dict(
            points=[asdict(p) for p in value.points],
            checkpoints={str(c): asdict(value.at_cost(c)) for c in (3200, 4800, 6400)},
            cost_to_target=value.cost_to_target,
            target_censored=value.target_censored,
            high_value_count=sum(v >= threshold for v in truth.values()),
        )
    result["purchases"] = {
        f: sum(e.action.fidelity == f for e in trace.events) for f in ("low", "high")
    }
    return result


def run_trajectory(output, key, limits, operation, verify=False):
    task, seed, condition, strategy = key
    inputs = output / "inputs" / task
    ids = tuple(read(inputs / "ids.json"))
    truth = read(inputs / "hf.json")
    proxy = read(inputs / f"{seed}-{'C0' if condition == 'HF' else condition}.json")["values"]
    low_cost = 4 if condition == "C3" else 1
    initial = initial_ids(ids, task, seed)
    dest = output / "trajectories" / f"{task}-{seed}-{condition}-{strategy}"
    if not verify:
        dest.mkdir()
    scorer = (
        None
        if verify
        else RFScorer(
            ids,
            np.load(inputs / "features.npy", allow_pickle=False),
            task=task,
            seed=seed,
            initial_ids=initial,
            warmup_ids=() if strategy == "hf_only" else initial,
            member_jobs=operation["member_jobs"],
        )
    )
    mode = "rb" if verify else "xb"
    with (
        (dest / "scores.jsonl").open(mode) as scores_file,
        (dest / "events.jsonl").open(mode) as events_file,
    ):

        def recorded(state):
            limits.check()
            if verify:
                record = json.loads(next(scores_file))
                if record["state_sha256"] != state_hash(state):
                    raise ValueError("score State identity mismatch")
                scores = Scores(
                    *(
                        dict(zip(ids, record[n], strict=True))
                        for n in ("mean", "high_value", "low_value")
                    )
                )
            else:
                scores = scorer(state)
                scores.validate(ids)
                scores_file.write(
                    canonical(
                        dict(
                            state_sha256=state_hash(state),
                            **{
                                n: [getattr(scores, n)[c] for c in ids]
                                for n in ("mean", "high_value", "low_value")
                            },
                        )
                    )
                )
                scores_file.flush()
            return scores

        def event(value):
            limits.check()
            payload = canonical(asdict(value))
            if verify:
                if next(events_file) != payload:
                    raise ValueError("event/decision readback mismatch")
            else:
                events_file.write(payload)
                events_file.flush()
                os.fsync(events_file.fileno())

        policy = make_e4_policy(strategy, recorded, seed=seed, initial_ids=initial)
        trace = replay(
            truth, proxy, Costs(low_cost, 100), 6400, policy, initial_high=initial, on_event=event
        )
        result = evaluation_record(trace, truth)
        if verify:
            if scores_file.read() or events_file.read() or read(dest / "evaluation.json") != result:
                raise ValueError("trailing records/evaluation mismatch")
        else:
            write(dest / "evaluation.json", result)
            write(dest / "checksums.json", {p.name: sha(p) for p in dest.iterdir() if p.is_file()})
    return result


def validate_inputs(output):
    folder = output / "inputs"
    expected = read(output / "input-checksums.json")
    actual = {str(p.relative_to(folder)): sha(p) for p in folder.rglob("*") if p.is_file()}
    if expected != actual:
        raise ValueError("input bytes drifted")


def execute(args):
    operation = read(OPERATION)
    if len(args.source_commit) != 40 or any(
        c not in "0123456789abcdef" for c in args.source_commit
    ):
        raise ValueError("source commit must be exact SHA-1")
    output = args.output
    start = time.monotonic()
    if not args.verify:
        output.mkdir(parents=True, exist_ok=False)
        write(output / "operation.json", operation)
        write(
            output / "provenance.json",
            dict(
                source_commit=args.source_commit,
                source_file_hashes=source_identity(),
                operation_sha256=sha(OPERATION),
                started_at=datetime.now(UTC).isoformat(),
                python=sys.version,
                platform=platform.platform(),
                packages={
                    k: importlib.metadata.version(k)
                    for k in ("numpy", "scikit-learn", "xgboost-cpu")
                },
                gate_a="PENDING",
                execution_authority=operation["execution_authority"],
            ),
        )
    elif (
        read(output / "operation.json") != operation
        or read(output / "provenance.json")["source_commit"] != args.source_commit
        or read(output / "provenance.json")["source_file_hashes"] != source_identity()
    ):
        raise ValueError("operation drifted")
    limits = Limits(output, operation, start)

    def timeout(signum, frame):
        raise TimeoutError("wall hard limit")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(operation["wall_budget_seconds"])
    try:
        if not args.verify:
            signal.alarm(300)
            probe = benchmark()
            signal.alarm(max(1, int(operation["wall_budget_seconds"] - (time.monotonic() - start))))
            seconds = max(r["wall_seconds"] for r in probe["rows"] if r["member_jobs"] == 2)
            projected = seconds * operation["scorer_call_bound"]
            passed = projected <= 0.8 * (
                operation["wall_budget_seconds"] - (time.monotonic() - start)
            )
            write(
                output / "resource-probe.json",
                dict(
                    probe=probe,
                    projected_seconds=projected,
                    status="PASS" if passed else "RESOURCE_STOP",
                    truth_read=False,
                ),
            )
            if not passed:
                raise TimeoutError("resource projection failed before truth")
            prepare(args.data_root, output, operation, limits)
            (output / "trajectories").mkdir()
        validate_inputs(output)
        keys = matrix(operation["assays"], operation["seeds"])
        evaluations = {}
        for key in keys:
            if args.verify:
                dest = output / "trajectories" / "-".join(map(str, key))
                expected = read(dest / "checksums.json")
                if {
                    p.name: sha(p) for p in dest.iterdir() if p.name != "checksums.json"
                } != expected:
                    raise ValueError("trajectory checksum mismatch")
            evaluations[key] = run_trajectory(output, key, limits, operation, args.verify)
            print(
                json.dumps(dict(completed=len(evaluations), total=len(keys), verify=args.verify)),
                flush=True,
            )
        result = summary(evaluations, operation["assays"], operation["seeds"])
        if args.verify:
            if result != read(output / "summary.json"):
                raise ValueError("summary readback mismatch")
            limits.check()
            write(
                output / "READBACK_VERIFIED.json",
                dict(
                    status="PASS",
                    trajectories=len(keys),
                    summary_sha256=sha(output / "summary.json"),
                    file_checksums={
                        str(p.relative_to(output)): sha(p) for p in output.rglob("*") if p.is_file()
                    },
                    gate_a="PENDING",
                    finished_at=datetime.now(UTC).isoformat(),
                ),
            )
        else:
            write(output / "summary.json", result)
            process = subprocess.Popen(
                [
                    sys.executable,
                    __file__,
                    "--output",
                    str(output),
                    "--source-commit",
                    args.source_commit,
                    "--verify",
                ],
            )
            limits.child = process
            try:
                while process.poll() is None:
                    limits.check()
                    time.sleep(1)
                if process.returncode != 0:
                    raise RuntimeError("independent readback failed")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                limits.child = None
        limits.check()
    except Exception as exc:
        error_path = output / ("READBACK_FAILED.json" if args.verify else "FAILED.json")
        if not error_path.exists():
            write(
                error_path,
                dict(
                    error=f"{type(exc).__name__}: {exc}",
                    gate_a="PENDING",
                    time=datetime.now(UTC).isoformat(),
                ),
            )
        raise
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--verify", action="store_true")
    execute(parser.parse_args())
