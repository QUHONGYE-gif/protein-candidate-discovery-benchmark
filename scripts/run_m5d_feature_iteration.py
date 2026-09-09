#!/usr/bin/env python3
"""执行已授权的四臂开发消融，保持原正式尝试只读。"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import multiprocessing
import os
import platform
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np  # noqa: E402
import run_m5d_skempi as base  # noqa: E402
from sklearn.ensemble import RandomForestRegressor  # noqa: E402
from xgboost import XGBRegressor  # noqa: E402

from pcdb.datasets.skempi_iteration import ARMS, build_features  # noqa: E402
from pcdb.datasets.skempi_readiness import ObservableCandidate, load_residue_map  # noqa: E402
from pcdb.m2.models import model_effective_parameters  # noqa: E402
from pcdb.replay.engine import bootstrap_draws  # noqa: E402
from pcdb.replay.oracle import HiddenOracle  # noqa: E402

STUDY = "m5d-feature-iteration-v1"
TERMINAL = "5e0db1dde3ac38c4cc90b411d326fae82b41554aa7292cb9746ae2a68f192636"
ARCHIVE = "82418131a3864aa8484ea6ec300e58f9bc0349e21530f83f7ce336b41589a95a"


def select(task):
    """只接收可观测矩阵和已揭示观测；预测返回后才由 evaluator 计算诊断。"""
    state, ids, matrix, observations = task
    ids = list(ids)
    expected = base.initial(ids, state["complex"], state["seed"], state["initial_size"])
    observed = {obs.candidate_id: obs.label for obs in observations}
    if len(observed) != len(observations) or set(observed) != set(expected):
        raise ValueError("revealed prefix mismatch")
    values = np.asarray(matrix, dtype=float)
    if (values.ndim != 2 or values.shape[0] != len(ids) or values.shape[1] < 41) or not np.isfinite(
        values
    ).all():
        raise ValueError("observable feature shape/values")
    index = {cid: i for i, cid in enumerate(ids)}
    ordered = sorted(observed)
    x = values[[index[cid] for cid in ordered]]
    y = np.array([observed[cid] for cid in ordered])
    remaining = [cid for cid in ids if cid not in observed]
    pool_x = values[[index[cid] for cid in remaining]]
    model = state["model"]
    if model not in base.MODELS:
        raise ValueError("unfrozen model")

    def predict(train_x, train_y):
        cls = RandomForestRegressor if model == base.MODELS[0] else XGBRegressor
        estimator = cls(**dict(model_effective_parameters(model)))
        estimator.fit(train_x, train_y)
        prediction = np.asarray(estimator.predict(pool_x), dtype=float)
        if prediction.shape != (len(remaining),) or not np.isfinite(prediction).all():
            raise ValueError("invalid predictions")
        return prediction

    mean = predict(x, y)
    members = []
    for draw in bootstrap_draws(
        protocol_id=base.PROTOCOL,
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

    e, g = top(std if model == base.MODELS[0] else mean + std), top(mean)
    selection = {
        "state": state,
        "initial": expected,
        "E": e,
        "G": g,
        "M": base.controls(base.digest(state), remaining, e, g),
    }

    return {
        "selection": selection,
        "prediction": {
            "ids": remaining,
            "mean": mean.tolist(),
            "sigma": std.tolist(),
            "acquisition": (std if model == base.MODELS[0] else mean + std).tolist(),
        },
    }


def ranks(values):
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="stable")
    result = np.empty(len(values), dtype=float)
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[order[j]] == values[order[i]]:
            j += 1
        result[order[i:j]] = (i + j - 1) / 2
        i = j
    return result


def correlation(a, b):
    a, b = ranks(a), ranks(b)
    if np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def diagnostic(record, truth):
    """评价侧诊断不回传选择进程，也不用于动态修改任何臂。"""
    prediction = record["prediction"]
    ids = prediction["ids"]
    mean = np.asarray(prediction["mean"], dtype=float)
    sigma = np.asarray(prediction["sigma"], dtype=float)
    score = np.asarray(prediction["acquisition"], dtype=float)
    if (
        not ids
        or len(set(ids)) != len(ids)
        or any(a.shape != (len(ids),) or not np.isfinite(a).all() for a in (mean, sigma, score))
        or (sigma < 0).any()
    ):
        raise ValueError("预测身份、形状或数值不合法")
    selection = record["selection"]
    model = selection["state"]["model"]
    expected_score = sigma if model == base.MODELS[0] else mean + sigma
    if not np.array_equal(score, expected_score):
        raise ValueError("采集分数与保存预测不一致")
    for branch, scores in (("G", mean), ("E", score)):
        chosen = [ids[i] for i in np.lexsort((np.array(ids), -scores))[:16]]
        if chosen != selection[branch]:
            raise ValueError("保存选择不能由预测重建")
    y = np.array([truth[cid] for cid in ids])
    error = np.abs(y - mean)
    return {
        "mae": float(error.mean()),
        "prediction_spearman": correlation(mean, y),
        "sigma_error_spearman": correlation(sigma, error),
        "mean_score_std": float(np.std(mean)),
        "sigma_score_std": float(np.std(sigma)),
        "mean_sigma": float(sigma.mean()),
        "overlap": len(set(selection["E"]) & set(selection["G"])),
    }


def average(values):
    valid = [v for v in values if v is not None]
    return float(np.mean(valid)) if valid else None


def evaluate_records(records, cohort, states, truth, reference, arms=ARMS):
    expected = [(base.digest(s), arm) for s in states for arm in arms]
    if [(base.digest(r["selection"]["state"]), r["arm"]) for r in records] != expected:
        raise ValueError("臂/状态精确覆盖失败")
    pools = {c["complex"]: c["ids"] for c in cohort}
    rows = []
    for record in records:
        selection, arm = record["selection"], record["arm"]
        state = selection["state"]
        ids = pools[state["complex"]]
        if record["prediction"]["ids"] != [cid for cid in ids if cid not in selection["initial"]]:
            raise ValueError("残余池预测顺序漂移")
        if arm == "F0" and selection != reference[base.digest(state)]:
            raise ValueError("F0 不精确复现原正式选择")
        metrics = base.evaluate(selection, ids, truth)
        y = sorted([truth[cid] for cid in ids], reverse=True)
        cutoff = y[int(np.ceil(0.01 * len(ids))) - 1]
        high = {cid for cid in ids if truth[cid] >= cutoff}
        rows.append(
            {
                "arm": arm,
                "metrics": metrics,
                "diagnostic": diagnostic(record, truth),
                "top1_exhausted": high <= set(selection["initial"]),
            }
        )
    return rows


def rollup(rows):
    """状态先在复合物内汇总，再按四个保守组等权，不伪增样本数。"""
    cells = defaultdict(list)
    for row in rows:
        state = row["metrics"]["state"]
        values = dict(row["diagnostic"])
        b = row["metrics"]["branches"]
        for name, branch in (("E", b[0]), ("G", b[1])):
            for fraction in ("0.005", "0.01", "0.02"):
                values[f"{name}_recall_{fraction}"] = branch["recall"][fraction]
            values[f"{name}_best"] = branch["best_improvement"]
        values["E-M_recall_0.01"] = b[0]["recall"]["0.01"] - average(
            [v["recall"]["0.01"] for v in b[2:]]
        )
        values["top1_exhausted_fraction"] = int(row["top1_exhausted"])
        cells[
            (row["arm"], state["model"], state["group"], state["complex"], state["initial_size"])
        ].append(values)
    cell_rows = []
    for (arm, model, group, complex_id, size), values in sorted(cells.items()):
        cell_rows.append(
            {
                "arm": arm,
                "model": model,
                "group": group,
                "complex": complex_id,
                "initial_size": size,
                "seed_count": len(values),
                "values": {k: average([v[k] for v in values]) for k in values[0]},
                "defined_counts": {k: sum(v[k] is not None for v in values) for k in values[0]},
            }
        )
    group_rows = []
    for arm in ARMS:
        for model in base.MODELS:
            for group in sorted({r["group"] for r in cell_rows}):
                subset = [
                    r["values"]
                    for r in cell_rows
                    if r["arm"] == arm and r["model"] == model and r["group"] == group
                ]
                group_rows.append(
                    {
                        "arm": arm,
                        "model": model,
                        "group": group,
                        "values": {k: average([r[k] for r in subset]) for k in subset[0]},
                    }
                )
    effects = []
    for arm in ARMS:
        for model in base.MODELS:
            rr = [r for r in group_rows if r["arm"] == arm and r["model"] == model]
            baseline = [r for r in group_rows if r["arm"] == "F0" and r["model"] == model]
            deltas = {
                k: [a["values"][k] - b["values"][k] for a, b in zip(rr, baseline, strict=True)]
                for k in ("E_recall_0.01", "G_recall_0.01", "E_best", "G_best")
            }
            n = len(rr)
            draw = np.random.Generator(np.random.PCG64(104105)).integers(0, n, size=(20000, n))
            contrast = {}
            for key, delta in deltas.items():
                a = np.array(delta)
                contrast[key] = {
                    "estimate": float(a.mean()),
                    "group_deltas": delta,
                    "interval": np.quantile(
                        a[draw].mean(axis=1), [0.003125, 0.996875], method="linear"
                    ).tolist(),
                }
            e, g = contrast["E_recall_0.01"]["estimate"], contrast["G_recall_0.01"]["estimate"]
            if e == 0 and g == 0:
                decision = "inconclusive"
            elif e <= 0 and g <= 0:
                decision = "negative"
            elif (
                e > 0
                and g >= 0
                and sum(v > 0 for v in deltas["E_recall_0.01"]) >= 3
                and min(deltas["E_recall_0.01"]) >= -0.05
                and contrast["E_best"]["estimate"] >= 0
                and n == 4
            ):
                decision = "positive"
            else:
                decision = "unstable"
            effects.append(
                {
                    "arm": arm,
                    "model": model,
                    "decision": decision,
                    "values": {k: average([r["values"][k] for r in rr]) for k in rr[0]["values"]},
                    "delta_vs_F0": contrast,
                }
            )
    return {
        "study": STUDY,
        "evidence_stage": "ITERATION",
        "independent_review": "PENDING",
        "scientific_gate": "NOT_ASSESSED",
        "arm_states": len(rows),
        "branches": len(rows) * 66,
        "cells": cell_rows,
        "groups": group_rows,
        "results": effects,
    }


def inputs(args):
    if base.identity(args.formal_root / "terminal.json") != TERMINAL:
        raise ValueError("原正式终态身份漂移")
    terminal = json.loads((args.formal_root / "terminal.json").read_bytes())
    for name, sha in terminal["artifacts"].items():
        if base.identity(args.formal_root / name) != sha:
            raise ValueError("原正式产物漂移")
    if base.identity(args.archive) != ARCHIVE:
        raise ValueError("PDB 冻结来源哈希漂移")
    cohort, states, old_features, all_ids = base.load_inputs(args.input_root)
    mappings = load_residue_map(args.archive)
    residues = {}
    for (pdb, chain, _), (aa, position) in mappings.items():
        key = (pdb, chain, position)
        if key in residues and residues[key] != aa:
            raise ValueError("残基邻域存在矛盾")
        residues[key] = aa
    rows = json.loads((args.input_root / "candidates.json").read_bytes())
    candidates = {
        r["candidate_id"]: ObservableCandidate(
            r["candidate_id"], r["complex_id"], tuple(r["mutations"]), tuple(r["cleaned_mutations"])
        )
        for r in rows
    }
    feature_sets, audit = {}, []
    for c in cohort:
        built = build_features([candidates[cid] for cid in c["ids"]], residues)
        if list(map(list, built.matrices["F0"])) != [old_features[cid] for cid in c["ids"]]:
            raise ValueError("F0 特征漂移")
        feature_sets[c["complex"]] = built
        audit.append(
            {
                "complex": c["complex"],
                "candidate_count": len(c["ids"]),
                "centers": built.center_count,
                "neighbors": built.neighbor_count,
                "present": built.neighbor_present,
                "arms": {
                    a: {
                        "dimension": len(built.columns[a]),
                        "unique_vectors": len(set(built.matrices[a])),
                        "matrix_sha256": base.digest(built.matrices[a]),
                        "columns_sha256": base.digest(built.columns[a]),
                    }
                    for a in ARMS
                },
            }
        )
    if sum(x["present"] for x in audit) / sum(x["neighbors"] for x in audit) < 0.95:
        raise ValueError("局部残基覆盖低于预声明95%")
    # 只取选定开发池标签，不将其他复合物用于新实验。
    if args.mode == "smoke":
        states = [
            s
            for s in states
            if s["complex"] in ("3BT1_A_U", "1CHO_EFG_I")
            and s["seed"] == 1729
            and s["initial_size"] == 32
        ]
    reference = {
        base.digest(r["state"]): r
        for r in json.loads((args.formal_root / "private/selections.json").read_bytes())
    }
    return cohort, states, feature_sets, audit, all_ids, reference


def readback(args, cohort, states, truth, reference):
    records = json.loads((args.output / "private/records.json").read_bytes())
    rows = evaluate_records(records, cohort, states, truth, reference)
    if base.encode(rows) != (args.output / "private/metrics.json").read_bytes():
        raise ValueError("分支指标或诊断回读不一致")
    if base.encode(rollup(rows)) != (args.output / "report.json").read_bytes():
        raise ValueError("汇总回读不一致")
    return rows


def require_smoke(study_root, head, audit):
    for path in sorted(study_root.glob("*/terminal.json")):
        terminal = json.loads(path.read_bytes())
        if (
            terminal.get("mode") != "smoke"
            or terminal.get("status") != "succeeded"
            or terminal.get("executed_git_commit") != head
        ):
            continue
        for name, sha in terminal["artifacts"].items():
            if base.identity(path.parent / name) != sha:
                raise ValueError("smoke 产物哈希漂移")
        if json.loads((path.parent / "feature-audit.json").read_bytes()) != audit:
            raise ValueError("smoke 与全量特征身份不一致")
        return
    raise ValueError("全量前必须存在同源码、同特征的成功 smoke")


def main():
    parser = argparse.ArgumentParser()
    for name in ("input-root", "formal-root", "archive", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--mode", choices=("smoke", "full"), required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    cohort, states, features, audit, all_ids, reference = inputs(args)
    if args.verify:
        truth = base.read_truth(args.input_root, all_ids)
        rows = readback(args, cohort, states, truth, reference)
        terminal = json.loads((args.output / "terminal.json").read_bytes())
        for name, sha in terminal["artifacts"].items():
            if base.identity(args.output / name) != sha:
                raise ValueError("终态文件哈希漂移")
        print(json.dumps({"readback": "PASS", "arm_states": len(rows)}))
        return
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base.SOURCE, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=base.SOURCE):
        raise ValueError("源码必须干净")
    if args.mode == "full":
        require_smoke(args.output.parent, head, audit)
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    prior_seconds = sum(
        json.loads(p.read_bytes()).get("elapsed_seconds", 0)
        for p in args.output.parent.glob("*/terminal.json")
    )
    limit = 7200 - prior_seconds
    peak, stop = [0], threading.Event()
    entry = {
        "study_id": STUDY,
        "attempt_id": args.output.name,
        "executed_git_commit": head,
        "command": [sys.executable, *sys.argv],
        "started_at": datetime.now(UTC).isoformat(),
        "config_identity": base.identity(base.SOURCE / "docs/experiments/M5D_FEATURE_ITERATION.md"),
        "data_identities": [base.PROCESSING, base.SPLIT, ARCHIVE, TERMINAL],
        "mode": args.mode,
        "parent_attempt_id": next(
            iter(
                sorted(
                    (p.parent.name for p in args.output.parent.glob("*/terminal.json")),
                    reverse=True,
                )
            ),
            None,
        ),
    }
    with (args.output.parent / "attempts.jsonl").open("a") as stream:
        stream.write(json.dumps({**entry, "status": "running"}, sort_keys=True) + "\n")

    def monitor():
        while not stop.wait(0.5):
            peak[0] = max(peak[0], base.rss_tree(os.getpid()))
            size = sum(p.stat().st_size for p in args.output.parent.rglob("*") if p.is_file())
            if peak[0] > 1610612736 or time.monotonic() - started > limit or size > 1073741824:
                base.write(args.output / "RESOURCE_FAILURE.json", {"rss": peak[0], "bytes": size})
                failed = {
                    **entry,
                    "status": "stopped",
                    "failure_class": "budget",
                    "elapsed_seconds": time.monotonic() - started,
                    "peak_tree_rss_bytes": peak[0],
                    "finished_at": datetime.now(UTC).isoformat(),
                    "failure_reason": "资源上限触发",
                    "result_summary": None,
                    "selection_disposition": "discarded",
                }
                base.write(args.output / "terminal.json", failed)
                with (args.output.parent / "attempts.jsonl").open("a") as stream:
                    stream.write(json.dumps(failed, sort_keys=True) + "\n")
                os.killpg(os.getpgrp(), 15)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        base.write(
            args.output / "run-spec.json",
            {
                **entry,
                "states": states,
                "arms": ARMS,
                "feature_audit": audit,
                "environment": {
                    p: importlib.metadata.version(p)
                    for p in ("numpy", "scikit-learn", "xgboost-cpu")
                },
                "python": sys.version,
                "hostname": platform.node(),
                "budget_seconds_remaining": limit,
                "model_config_sha256": {
                    m: base.hashlib.sha256(base.m4_model_config_bytes(m)).hexdigest()
                    for m in base.MODELS
                },
                "environment_freeze_sha256": base.hashlib.sha256(
                    subprocess.check_output([sys.executable, "-m", "pip", "freeze", "--all"])
                ).hexdigest(),
            },
        )
        base.write(args.output / "feature-audit.json", audit)
        for complex_id, built in features.items():
            base.write(
                args.output / "private/features" / (complex_id + ".json"),
                {"matrices": built.matrices, "columns": built.columns},
            )
        records = []
        with multiprocessing.get_context("spawn").Pool(1) as pool:
            truth = base.read_truth(args.input_root, all_ids)
            pools = {c["complex"]: c["ids"] for c in cohort}
            for state in states:
                ids = pools[state["complex"]]
                oracle = HiddenOracle({cid: truth[cid] for cid in ids})
                observations = oracle.reveal(
                    base.initial(ids, state["complex"], state["seed"], state["initial_size"])
                )
                for arm in ARMS:
                    task = (state, ids, features[state["complex"]].matrices[arm], observations)
                    result = pool.apply(select, (task,))
                    if args.mode == "smoke" and result != pool.apply(select, (task,)):
                        raise ValueError("smoke 重复拟合非确定性")
                    if arm == "F0" and result["selection"] != reference[base.digest(state)]:
                        raise ValueError("F0 选择不匹配，立即停止")
                    records.append({"arm": arm, **result})
                    with (args.output / "progress.jsonl").open("a") as stream:
                        stream.write(
                            json.dumps(
                                {"arm_states": len(records), "elapsed": time.monotonic() - started}
                            )
                            + "\n"
                        )
        rows = evaluate_records(records, cohort, states, truth, reference)
        base.write(args.output / "private/records.json", records)
        base.write(args.output / "private/metrics.json", rows)
        base.write(args.output / "report.json", rollup(rows))
        readback(args, cohort, states, base.read_truth(args.input_root, all_ids), reference)
        entry.update(
            status="succeeded",
            failure_class="none",
            result_summary={"arm_states": len(rows), "readback": "PASS"},
            failure_reason=None,
            selection_disposition="diagnostic",
        )
    except Exception as exc:
        entry.update(
            status="failed",
            failure_class="implementation" if not isinstance(exc, ValueError) else "validity",
            result_summary=None,
            failure_reason=str(exc),
            selection_disposition="discarded",
        )
        raise
    finally:
        stop.set()
        thread.join()
        entry.update(
            finished_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=time.monotonic() - started,
            peak_tree_rss_bytes=peak[0],
            cos_writes=0,
        )
        entry["artifacts"] = {
            str(p.relative_to(args.output)): base.identity(p)
            for p in args.output.rglob("*")
            if p.is_file()
        }
        base.write(args.output / "terminal.json", entry)
        with (args.output.parent / "attempts.jsonl").open("a") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")
        print(json.dumps({k: v for k, v in entry.items() if k != "artifacts"}))


if __name__ == "__main__":
    main()
