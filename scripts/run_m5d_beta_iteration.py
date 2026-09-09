#!/usr/bin/env python3
"""复用已验证预测开展有界 beta 开发比较；不修改历史尝试。"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import run_m5d_feature_iteration as feature
import run_m5d_skempi as base

BETAS = (0, 0.25, 0.5, 1, 2)
STUDY = "m5d-beta-iteration-v1"
PARENT = "69fabeba628fa813a9a6d5b5c08785b86396797ea0a54e66ffff608026acf1ac"
CONTRACT = "docs/experiments/M5D_BETA_ITERATION.md"


def select(state, pool, prediction, beta):
    """仅接受固定预测，不接受真值或评价结果；多余字段也拒绝。"""
    if set(prediction) != {"ids", "mean", "sigma", "acquisition"} or beta not in BETAS:
        raise ValueError("预测字段或 beta 不合法")
    if state["model"] != base.MODELS[1] or len(pool) != len(set(pool)):
        raise ValueError("模型或候选池漂移")
    initial = base.initial(pool, state["complex"], state["seed"], state["initial_size"])
    remaining = [cid for cid in pool if cid not in initial]
    if prediction["ids"] != remaining or len(remaining) < 16:
        raise ValueError("预测残余池顺序不一致")
    mean, sigma, acquisition = (
        np.asarray(prediction[k], dtype=float) for k in ("mean", "sigma", "acquisition")
    )
    if (
        any(
            v.shape != (len(remaining),) or not np.isfinite(v).all()
            for v in (mean, sigma, acquisition)
        )
        or (sigma < 0).any()
    ):
        raise ValueError("预测数值不合法")
    if not np.array_equal(acquisition, mean + sigma):
        raise ValueError("上游采集分数不一致")

    def top(scores):
        return [remaining[i] for i in np.lexsort((np.array(remaining), -scores))[:16]]

    e, g = top(mean + beta * sigma), top(mean)
    return {
        "state": state,
        "initial": initial,
        "E": e,
        "G": g,
        "M": base.controls(base.digest(state), remaining, e, g),
    }


def load_source(args):
    if base.identity(args.parent_root / "terminal.json") != PARENT:
        raise ValueError("上游终态身份漂移")
    terminal = json.loads((args.parent_root / "terminal.json").read_bytes())
    if terminal["status"] != "succeeded":
        raise ValueError("上游未成功")
    for name, sha in terminal["artifacts"].items():
        if base.identity(args.parent_root / name) != sha:
            raise ValueError("上游产物身份漂移")
    cohort, all_states, _, all_ids = base.load_inputs(args.input_root)
    states = [s for s in all_states if s["model"] == base.MODELS[1]]
    records = json.loads((args.parent_root / "private/records.json").read_bytes())
    expected = [(base.digest(s), arm) for s in all_states for arm in feature.ARMS]
    if [(base.digest(r["selection"]["state"]), r["arm"]) for r in records] != expected:
        raise ValueError("上游完整状态覆盖漂移")
    selected = [
        r
        for r in records
        if r["arm"] == "F2" and r["selection"]["state"]["model"] == base.MODELS[1]
    ]
    if args.mode == "smoke":
        states = [
            s
            for s in states
            if s["complex"] in ("3BT1_A_U", "1CHO_EFG_I")
            and s["seed"] == 1729
            and s["initial_size"] == 32
        ]
    selected = {base.digest(r["selection"]["state"]): r for r in selected}
    return cohort, states, selected, all_ids


def generate(cohort, states, source):
    pools = {c["complex"]: c["ids"] for c in cohort}
    records = []
    for state in states:
        original = source[base.digest(state)]
        for beta in BETAS:
            selection = select(state, pools[state["complex"]], original["prediction"], beta)
            if beta == 0 and selection["E"] != original["selection"]["G"]:
                raise ValueError("beta=0 未复现 Greedy")
            if beta == 1 and selection != original["selection"]:
                raise ValueError("beta=1 未复现原完整选择")
            records.append({"beta": beta, "selection": selection})
    return records


def ratio(a, b):
    return float(a / b) if b != 0 else None


def evaluate(records, cohort, states, source, truth):
    if records != generate(cohort, states, source):
        raise ValueError("保存选择无法由固定预测重建")
    pools = {c["complex"]: c["ids"] for c in cohort}
    rows = []
    for record in records:
        s = record["selection"]
        original = source[base.digest(s["state"])]
        p = original["prediction"]
        metrics = base.evaluate(s, pools[s["state"]["complex"]], truth)
        d = feature.diagnostic(original, truth)
        d["sigma_spread_over_mean_spread"] = ratio(d["sigma_score_std"], d["mean_score_std"])
        d["mean_sigma_over_mae"] = ratio(d["mean_sigma"], d["mae"])
        index = {cid: i for i, cid in enumerate(p["ids"])}
        d["overlap"] = metrics["overlap"]
        d["replaced_count"] = 16 - metrics["overlap"]
        d["selected_mean"] = float(np.mean([p["mean"][index[cid]] for cid in s["E"]]))
        d["selected_truth_mean"] = float(np.mean([truth[cid] for cid in s["E"]]))
        d["selected_mae"] = float(
            np.mean([abs(p["mean"][index[cid]] - truth[cid]) for cid in s["E"]])
        )
        incoming, outgoing = set(s["E"]) - set(s["G"]), set(s["G"]) - set(s["E"])
        d["replacement_truth_delta"] = (
            float(
                np.mean([truth[cid] for cid in sorted(incoming)])
                - np.mean([truth[cid] for cid in sorted(outgoing)])
            )
            if incoming
            else None
        )
        values = dict(d)
        for f in ("0.005", "0.01", "0.02"):
            values["recall_" + f] = metrics["branches"][0]["recall"][f]
            values["E-M_recall_" + f] = values["recall_" + f] - float(
                np.mean([b["recall"][f] for b in metrics["branches"][2:]])
            )
        values["best"] = metrics["branches"][0]["best_improvement"]
        rows.append({"beta": record["beta"], "metrics": metrics, "values": values})
    return rows


def classify(delta0, delta1, groups1, best0, best1):
    if delta0 == 0 and delta1 == 0:
        return "inconclusive"
    if delta0 <= 0 and delta1 <= 0:
        return "negative"
    if (
        delta0 > 0
        and delta1 > 0
        and len(groups1) == 4
        and sum(v > 0 for v in groups1) >= 3
        and min(groups1) >= -0.05
        and best0 >= 0
        and best1 >= 0
    ):
        return "positive"
    return "unstable"


def rollup(rows):
    cells = defaultdict(list)
    for row in rows:
        s = row["metrics"]["state"]
        cells[(row["beta"], s["group"], s["complex"], s["initial_size"])].append(row["values"])
    cell_rows = [
        {
            "beta": b,
            "group": g,
            "complex": c,
            "initial_size": n,
            "seed_count": len(v),
            "values": {k: feature.average([r[k] for r in v]) for k in v[0]},
            "defined_counts": {k: sum(r[k] is not None for r in v) for k in v[0]},
        }
        for (b, g, c, n), v in sorted(cells.items())
    ]
    groups = []
    for beta in BETAS:
        for group in sorted({c["group"] for c in cell_rows}):
            rr = [c["values"] for c in cell_rows if c["beta"] == beta and c["group"] == group]
            groups.append(
                {
                    "beta": beta,
                    "group": group,
                    "values": {k: feature.average([r[k] for r in rr]) for k in rr[0]},
                }
            )
    effects = []
    for beta in BETAS:
        rr = [r for r in groups if r["beta"] == beta]
        contrast = {}
        for reference in (0, 1):
            ref = [r for r in groups if r["beta"] == reference]
            contrast[str(reference)] = {}
            for key in ("recall_0.005", "recall_0.01", "recall_0.02", "best"):
                deltas = np.array(
                    [r["values"][key] - b["values"][key] for r, b in zip(rr, ref, strict=True)]
                )
                draw = np.random.Generator(np.random.PCG64(104105)).integers(
                    0, len(rr), size=(20000, len(rr))
                )
                contrast[str(reference)][key] = {
                    "estimate": float(deltas.mean()),
                    "group_deltas": deltas.tolist(),
                    "interval": np.quantile(
                        deltas[draw].mean(axis=1), [0.003125, 0.996875], method="linear"
                    ).tolist(),
                }
        a, b = contrast["0"], contrast["1"]
        decision = classify(
            a["recall_0.01"]["estimate"],
            b["recall_0.01"]["estimate"],
            b["recall_0.01"]["group_deltas"],
            a["best"]["estimate"],
            b["best"]["estimate"],
        )
        effects.append(
            {
                "beta": beta,
                "decision": decision,
                "values": {
                    k: feature.average([r["values"][k] for r in rr]) for k in rr[0]["values"]
                },
                "delta_vs_beta": contrast,
            }
        )
    disposition = (
        "continue"
        if any(r["decision"] == "positive" for r in effects)
        else "revise"
        if any(
            r["delta_vs_beta"][ref]["recall_0.01"]["estimate"] > 0
            for r in effects
            for ref in ("0", "1")
        )
        else "stop"
    )
    return {
        "study": STUDY,
        "evidence_stage": "ITERATION",
        "independent_review": "PENDING",
        "arm_states": len(rows),
        "branches": 66 * len(rows),
        "cells": cell_rows,
        "groups": groups,
        "results": effects,
        "disposition": disposition,
    }


def readback(args, cohort, states, source, all_ids):
    records = json.loads((args.output / "private/selections.json").read_bytes())
    rows = evaluate(records, cohort, states, source, base.read_truth(args.input_root, all_ids))
    if (
        base.encode(rows) != (args.output / "private/metrics.json").read_bytes()
        or base.encode(rollup(rows)) != (args.output / "report.json").read_bytes()
    ):
        raise ValueError("分支指标、诊断或汇总回读不一致")
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
        print(json.dumps({"readback": "PASS", "arm_states": len(rows)}))
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
    if prior >= 1800:
        raise ValueError("累计预算耗尽")
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
                or time.monotonic() - started + prior > 1800
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
                os._exit(2)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        cohort, states, source, all_ids = load_source(args)
        base.write(
            args.output / "run-spec.json",
            {
                **entry,
                "states": states,
                "betas": BETAS,
                "feature": "F2",
                "model": base.MODELS[1],
                "python": sys.version,
                "hostname": platform.node(),
                "prior_seconds": prior,
                "environment_freeze_sha256": base.hashlib.sha256(
                    subprocess.check_output([sys.executable, "-m", "pip", "freeze", "--all"])
                ).hexdigest(),
            },
        )
        records = generate(cohort, states, source)
        if args.mode == "smoke" and records != generate(cohort, states, source):
            raise ValueError("smoke 选择非确定性")
        # 所有策略选择完成后才在评价侧读取真值。
        rows = evaluate(records, cohort, states, source, base.read_truth(args.input_root, all_ids))
        report = rollup(rows)
        base.write(args.output / "private/selections.json", records)
        base.write(args.output / "private/metrics.json", rows)
        base.write(args.output / "report.json", report)
        readback(args, cohort, states, source, all_ids)
        entry.update(
            status="succeeded",
            failure_class="none",
            failure_reason=None,
            selection_disposition="diagnostic",
            result_summary={
                "arm_states": len(rows),
                "branches": 66 * len(rows),
                "readback": "PASS",
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
