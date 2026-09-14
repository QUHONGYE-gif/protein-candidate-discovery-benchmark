#!/usr/bin/env python3
"""在初始已揭示观测内交叉拟合，诊断固定bootstrap的不确定性排序。"""

from __future__ import annotations

import argparse
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
import run_m5d_feature_iteration as feature  # noqa: E402
import run_m5d_skempi as base  # noqa: E402
from xgboost import XGBRegressor  # noqa: E402

from pcdb.m2.models import model_effective_parameters  # noqa: E402
from pcdb.replay.engine import bootstrap_draws  # noqa: E402
from pcdb.replay.oracle import HiddenOracle  # noqa: E402

STUDY = "m5d-oof-diagnostic-v1"
PARENT = "69fabeba628fa813a9a6d5b5c08785b86396797ea0a54e66ffff608026acf1ac"
CONTRACT = "docs/experiments/M5D_OOF_DIAGNOSTIC.md"


def folds(state, pool):
    initial = base.initial(pool, state["complex"], state["seed"], state["initial_size"])
    ordered = sorted(
        initial, key=lambda cid: (base.digest([STUDY, state["complex"], state["seed"], cid]), cid)
    )
    return [sorted(ordered[i::4]) for i in range(4)]


def train_predict(task):
    """模型侧只接收该折训练观测；验证标签和残余标签不能进入任务。"""
    if set(task) != {"state", "pool", "matrix", "fold_index", "observations"}:
        raise ValueError("模型任务存在非授权字段")
    state, pool, matrix, fold_index, observations = (
        task[k] for k in ("state", "pool", "matrix", "fold_index", "observations")
    )
    if (
        state["model"] != base.MODELS[1]
        or fold_index not in range(4)
        or len(set(pool)) != len(pool)
    ):
        raise ValueError("模型、折或候选身份不合法")
    partition = folds(state, pool)
    validation = partition[fold_index]
    train = sorted(cid for i, ids in enumerate(partition) if i != fold_index for cid in ids)
    observed = {o.candidate_id: o.label for o in observations}
    if len(observed) != len(observations) or set(observed) != set(train):
        raise ValueError("训练观测越过折隔离边界")
    index = {cid: i for i, cid in enumerate(pool)}
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or values.shape[0] != len(pool) or not np.isfinite(values).all():
        raise ValueError("特征形状或数值不合法")
    x = values[[index[cid] for cid in train]]
    y = np.asarray([observed[cid] for cid in train], dtype=float)
    if not np.isfinite(y).all():
        raise ValueError("训练标签不合法")
    test_x = values[[index[cid] for cid in validation]]

    def predict(tx, ty):
        estimator = XGBRegressor(**dict(model_effective_parameters(base.MODELS[1])))
        estimator.fit(tx, ty)
        result = np.asarray(estimator.predict(test_x), dtype=float)
        if result.shape != (len(validation),) or not np.isfinite(result).all():
            raise ValueError("折外预测不合法")
        return result

    mean = predict(x, y)
    members = []
    for draw in bootstrap_draws(
        protocol_id=STUDY,
        run_seed=state["seed"],
        task_id=state["complex"],
        model_id=base.MODELS[1],
        round_index=fold_index + 1,
        observation_count=len(train),
    ):
        idx = np.array(draw.indices)
        members.append(predict(x[idx], y[idx]).tolist())
    return {
        "state": state,
        "fold_index": fold_index,
        "train_ids": train,
        "validation_ids": validation,
        "train_labels_sha256": base.digest([[cid, observed[cid]] for cid in train]),
        "train_features_sha256": base.digest(x.tolist()),
        "mean": mean.tolist(),
        "members": members,
        "sigma": np.std(members, axis=0, ddof=0).tolist(),
    }


def load_source(args):
    if base.identity(args.parent_root / "terminal.json") != PARENT:
        raise ValueError("上游终态身份漂移")
    terminal = json.loads((args.parent_root / "terminal.json").read_bytes())
    for name, sha in terminal["artifacts"].items():
        if base.identity(args.parent_root / name) != sha:
            raise ValueError("上游产物身份漂移")
    cohort, states, _, all_ids = base.load_inputs(args.input_root)
    states = [s for s in states if s["model"] == base.MODELS[1]]
    if args.mode == "smoke":
        states = [
            s
            for s in states
            if s["complex"] in ("3BT1_A_U", "1CHO_EFG_I")
            and s["seed"] == 1729
            and s["initial_size"] == 32
        ]
    source = {
        c["complex"]: json.loads(
            (args.parent_root / "private/features" / (c["complex"] + ".json")).read_bytes()
        )["matrices"]["F2"]
        for c in cohort
    }
    return cohort, states, source, all_ids


def execute(args, cohort, states, source, all_ids):
    records = []
    pools = {c["complex"]: c["ids"] for c in cohort}
    # spawn先于父进程读取标签，任务逐折只传入训练观测。
    with multiprocessing.get_context("spawn").Pool(1) as worker:
        truth = base.read_truth(args.input_root, all_ids)
        for state in states:
            ids = pools[state["complex"]]
            initial = base.initial(ids, state["complex"], state["seed"], state["initial_size"])
            observations = HiddenOracle({cid: truth[cid] for cid in ids}).reveal(initial)
            partition = folds(state, ids)
            for fold_index in range(4):
                task = {
                    "state": state,
                    "pool": ids,
                    "matrix": source[state["complex"]],
                    "fold_index": fold_index,
                    "observations": tuple(
                        o for o in observations if o.candidate_id not in partition[fold_index]
                    ),
                }
                result = worker.apply(train_predict, (task,))
                if args.mode == "smoke" and result != worker.apply(train_predict, (task,)):
                    raise ValueError("smoke 重复拟合不一致")
                records.append(result)
                with (args.output / "progress.jsonl").open("a") as stream:
                    stream.write(json.dumps({"completed_folds": len(records)}) + "\n")
    return records


def metrics(mean, sigma, y, ids):
    mean, sigma, y = (np.asarray(v, dtype=float) for v in (mean, sigma, y))
    error = np.abs(mean - y)
    count = int(np.ceil(len(ids) / 4))
    high = np.lexsort((np.array(ids), -sigma))[:count]
    low = np.lexsort((np.array(ids), sigma))[:count]
    constant = np.ptp(sigma) == 0
    mae = float(error.mean())
    high_mae = None if constant else float(error[high].mean())
    return {
        "sigma_error_spearman": feature.correlation(sigma, error),
        "prediction_spearman": feature.correlation(mean, y),
        "mae": mae,
        "mean_sigma": float(sigma.mean()),
        "mean_sigma_over_mae": float(sigma.mean()) / mae if mae else None,
        "zero_sigma_fraction": float(np.mean(sigma == 0)),
        "high_sigma_mae": high_mae,
        "low_sigma_mae": None if constant else float(error[low].mean()),
        "high_sigma_lift": high_mae / mae if mae and high_mae is not None else None,
    }


def evaluate(records, cohort, states, source, truth):
    if [(base.digest(r["state"]), r["fold_index"]) for r in records] != [
        (base.digest(s), f) for s in states for f in range(4)
    ]:
        raise ValueError("折/状态覆盖不一致")
    pools = {c["complex"]: c["ids"] for c in cohort}
    rows = []
    for j, state in enumerate(states):
        pool = pools[state["complex"]]
        partition = folds(state, pool)
        initial = base.initial(pool, state["complex"], state["seed"], state["initial_size"])
        observed = {
            o.candidate_id: o.label
            for o in HiddenOracle({cid: truth[cid] for cid in pool}).reveal(initial)
        }
        index = {cid: i for i, cid in enumerate(pool)}
        by_id, fold_metrics = {}, []
        for r in records[j * 4 : j * 4 + 4]:
            f = r["fold_index"]
            validation = partition[f]
            train = sorted(set(initial) - set(validation))
            matrix = [source[state["complex"]][index[cid]] for cid in train]
            if (
                r["train_ids"] != train
                or r["validation_ids"] != validation
                or r["train_labels_sha256"] != base.digest([[cid, observed[cid]] for cid in train])
                or r["train_features_sha256"]
                != base.digest(np.asarray(matrix, dtype=float).tolist())
            ):
                raise ValueError("训练/留出身份或哈希漂移")
            mean, members, sigma = (
                np.asarray(r[k], dtype=float) for k in ("mean", "members", "sigma")
            )
            if (
                mean.shape != (len(validation),)
                or members.shape != (5, len(validation))
                or sigma.shape != mean.shape
                or any(not np.isfinite(v).all() for v in (mean, members, sigma))
                or not np.array_equal(sigma, np.std(members, axis=0, ddof=0))
            ):
                raise ValueError("保存预测或成员方差不一致")
            fold_metrics.append(
                metrics(mean, sigma, [observed[cid] for cid in validation], validation)
            )
            for i, cid in enumerate(validation):
                if cid in by_id:
                    raise ValueError("OOF候选重复")
                by_id[cid] = (mean[i], sigma[i])
        if set(by_id) != set(initial):
            raise ValueError("OOF未完整覆盖初始观测")
        ids = sorted(initial)
        values = metrics(
            [by_id[cid][0] for cid in ids],
            [by_id[cid][1] for cid in ids],
            [observed[cid] for cid in ids],
            ids,
        )
        values["within_fold_spearman"] = feature.average(
            [v["sigma_error_spearman"] for v in fold_metrics]
        )
        values["defined_fold_fraction"] = (
            sum(v["sigma_error_spearman"] is not None for v in fold_metrics) / 4
        )
        rows.append({"state": state, "values": values, "fold_metrics": fold_metrics})
    return rows


def classify(values, groups, defined):
    keys = ("sigma_error_spearman", "within_fold_spearman", "high_sigma_lift")
    if len(groups) != 4 or any(values[k] is None for k in keys) or any(v < 0.9 for v in defined):
        return "inconclusive"
    rho, within, lift = (values[k] for k in keys)
    group_rho = [g["values"]["sigma_error_spearman"] for g in groups]
    if any(v is None for v in group_rho):
        return "inconclusive"
    if (
        rho >= 0.20
        and within > 0
        and lift >= 1.20
        and sum(v > 0 for v in group_rho) >= 3
        and min(group_rho) >= 0
    ):
        return "positive"
    if rho <= 0 and lift <= 1:
        return "negative"
    return "unstable"


def rollup(rows):
    cells = defaultdict(list)
    for row in rows:
        s = row["state"]
        cells[(s["group"], s["complex"], s["initial_size"])].append(row["values"])
    cell_rows = [
        {
            "group": g,
            "complex": c,
            "initial_size": n,
            "state_count": len(v),
            "values": {k: feature.average([r[k] for r in v]) for k in v[0]},
            "defined_counts": {k: sum(r[k] is not None for r in v) for k in v[0]},
        }
        for (g, c, n), v in sorted(cells.items())
    ]
    groups, by_size = [], []
    for size in (None, 32, 64):
        size_groups = []
        for g in sorted({c["group"] for c in cell_rows}):
            subset = [
                c
                for c in cell_rows
                if c["group"] == g and (size is None or c["initial_size"] == size)
            ]
            if not subset:
                continue
            size_groups.append(
                {
                    "group": g,
                    "values": {
                        k: feature.average([r["values"][k] for r in subset])
                        for k in subset[0]["values"]
                    },
                    "defined_fractions": {
                        k: sum(r["defined_counts"][k] for r in subset)
                        / sum(r["state_count"] for r in subset)
                        for k in subset[0]["values"]
                    },
                }
            )
        if not size_groups:
            continue
        values = {
            k: feature.average([g["values"][k] for g in size_groups])
            for k in size_groups[0]["values"]
        }
        intervals = {}
        for k in ("sigma_error_spearman", "within_fold_spearman", "high_sigma_lift", "mae"):
            vals = [g["values"][k] for g in size_groups]
            if all(v is not None for v in vals):
                a = np.array(vals)
                draw = np.random.Generator(np.random.PCG64(104105)).integers(
                    0, len(a), size=(20000, len(a))
                )
                intervals[k] = np.quantile(
                    a[draw].mean(axis=1), [0.003125, 0.996875], method="linear"
                ).tolist()
            else:
                intervals[k] = None
        if size is None:
            groups = size_groups
            overall = values
            overall_intervals = intervals
        else:
            by_size.append(
                {
                    "initial_size": size,
                    "values": values,
                    "intervals": intervals,
                    "groups": size_groups,
                }
            )
    defined = [
        g["defined_fractions"][k]
        for g in groups
        for k in ("sigma_error_spearman", "within_fold_spearman", "high_sigma_lift")
    ]
    decision = classify(overall, groups, defined)
    return {
        "study": STUDY,
        "evidence_stage": "ITERATION",
        "independent_review": "PENDING",
        "states": len(rows),
        "folds": 4 * len(rows),
        "retained_fits": 24 * len(rows),
        "oof_predictions": sum(r["state"]["initial_size"] for r in rows),
        "decision": decision,
        "disposition": {
            "positive": "continue",
            "negative": "stop",
            "inconclusive": "revise",
            "unstable": "revise",
        }[decision],
        "values": overall,
        "intervals": overall_intervals,
        "cells": cell_rows,
        "groups": groups,
        "by_initial_size": by_size,
    }


def readback(args, cohort, states, source, all_ids):
    records = json.loads((args.output / "private/records.json").read_bytes())
    rows = evaluate(records, cohort, states, source, base.read_truth(args.input_root, all_ids))
    if (
        base.encode(rows) != (args.output / "private/metrics.json").read_bytes()
        or base.encode(rollup(rows)) != (args.output / "report.json").read_bytes()
    ):
        raise ValueError("OOF指标或汇总回读不一致")
    return rows


def require_smoke(root, head, contract):
    for path in sorted(root.glob("*/terminal.json")):
        t = json.loads(path.read_bytes())
        if (
            t.get("mode") == "smoke"
            and t.get("status") == "succeeded"
            and t.get("executed_git_commit") == head
            and t.get("config_identity") == contract
            and t.get("source_terminal_sha256") == PARENT
        ):
            for name, sha in t["artifacts"].items():
                if base.identity(path.parent / name) != sha:
                    raise ValueError("smoke 哈希漂移")
            return path.parent.name
    raise ValueError("全量前必须通过同源码同输入 smoke")


def main():
    parser = argparse.ArgumentParser()
    for name in ("input-root", "parent-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--mode", choices=("smoke", "full"), required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        cohort, states, source, all_ids = load_source(args)
        terminal = json.loads((args.output / "terminal.json").read_bytes())
        if (
            terminal["status"] != "succeeded"
            or terminal["mode"] != args.mode
            or terminal["config_identity"] != base.identity(base.SOURCE / CONTRACT)
        ):
            raise ValueError("待复核终态不匹配")
        for name, sha in terminal["artifacts"].items():
            if base.identity(args.output / name) != sha:
                raise ValueError("结果产物身份漂移")
        rows = readback(args, cohort, states, source, all_ids)
        print(json.dumps({"readback": "PASS", "states": len(rows)}))
        return
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base.SOURCE, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=base.SOURCE):
        raise ValueError("执行源码必须干净")
    config = base.identity(base.SOURCE / CONTRACT)
    parent = require_smoke(args.output.parent, head, config) if args.mode == "full" else None
    prior = sum(
        json.loads(p.read_bytes()).get("elapsed_seconds", 0)
        for p in args.output.parent.glob("*/terminal.json")
    )
    if prior >= 3600:
        raise ValueError("累计预算耗尽")
    if os.getsid(0) != os.getpid():
        os.setsid()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    entry = {
        "study_id": STUDY,
        "attempt_id": args.output.name,
        "parent_attempt_id": parent,
        "executed_git_commit": head,
        "command": [sys.executable, *sys.argv],
        "config_identity": config,
        "source_terminal_sha256": PARENT,
        "data_identities": [base.PROCESSING, base.SPLIT, PARENT],
        "mode": args.mode,
        "started_at": datetime.now(UTC).isoformat(),
    }

    def ledger(value):
        with (args.output.parent / "attempts.jsonl").open("a") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")

    ledger({**entry, "status": "running"})
    started, peak, stop = time.monotonic(), [0], threading.Event()

    def monitor():
        while not stop.wait(0.5):
            peak[0] = max(peak[0], base.rss_tree(os.getpid()))
            size = sum(p.stat().st_size for p in args.output.parent.rglob("*") if p.is_file())
            if (
                peak[0] > 1610612736
                or time.monotonic() - started + prior > 3600
                or size > 536870912
            ):
                failed = {
                    **entry,
                    "status": "stopped",
                    "failure_class": "budget",
                    "failure_reason": "资源预算触发",
                    "result_summary": None,
                    "selection_disposition": "discarded",
                    "elapsed_seconds": time.monotonic() - started,
                    "peak_tree_rss_bytes": peak[0],
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                base.write(args.output / "terminal.json", failed)
                ledger(failed)
                os.killpg(os.getpgrp(), 15)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        cohort, states, source, all_ids = load_source(args)
        base.write(
            args.output / "run-spec.json",
            {
                **entry,
                "states": states,
                "folds": 4,
                "bootstrap_members": 5,
                "feature": "F2",
                "model": base.MODELS[1],
                "model_config_sha256": base.hashlib.sha256(
                    base.m4_model_config_bytes(base.MODELS[1])
                ).hexdigest(),
                "features_sha256": base.digest(source),
                "python": sys.version,
                "hostname": platform.node(),
                "prior_seconds": prior,
                "environment_freeze_sha256": base.hashlib.sha256(
                    subprocess.check_output([sys.executable, "-m", "pip", "freeze", "--all"])
                ).hexdigest(),
            },
        )
        records = execute(args, cohort, states, source, all_ids)
        rows = evaluate(records, cohort, states, source, base.read_truth(args.input_root, all_ids))
        report = rollup(rows)
        base.write(args.output / "private/records.json", records)
        base.write(args.output / "private/metrics.json", rows)
        base.write(args.output / "report.json", report)
        readback(args, cohort, states, source, all_ids)
        entry.update(
            status="succeeded",
            failure_class="none",
            failure_reason=None,
            selection_disposition="diagnostic",
            result_summary={
                "states": len(rows),
                "folds": 4 * len(rows),
                "fits": 24 * len(rows) * (2 if args.mode == "smoke" else 1),
                "readback": "PASS",
                "decision": report["decision"],
                "disposition": report["disposition"],
            },
        )
    except Exception as exc:
        entry.update(
            status="failed",
            failure_class="validity" if isinstance(exc, ValueError) else "implementation",
            failure_reason=str(exc),
            result_summary=None,
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
        ledger(entry)
        print(json.dumps({k: v for k, v in entry.items() if k != "artifacts"}))


if __name__ == "__main__":
    main()
