#!/usr/bin/env python3
"""复用OOF记录，按可观测训练位点覆盖做有界分层分析。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import run_m5d_oof_diagnostic as oof
import run_m5d_skempi as base

STUDY = "m5d-site-coverage-v1"
PARENT = "b08bb4d2c56dd8e72dabd920aa7b514a973caa7ee3dbdb9595da507da985437f"
CONTRACT = "docs/experiments/M5D_SITE_COVERAGE.md"
STRATA = ("all_seen", "any_unseen")
TARGETS = ("3BT1_A_U", "1A22_A_B")
CONTRASTS = ("mae_delta", "mae_ratio", "sigma_error_rho_delta", "prediction_rho_delta")


def build_masks(metadata, sites):
    """输入只含身份、位点和突变数；任何预测/标签字段均拒绝。"""
    result = []
    for r in metadata:
        if set(r) != {"state", "fold_index", "train_ids", "validation_ids"}:
            raise ValueError("覆盖输入混入非身份字段")
        if set(r["train_ids"]) & set(r["validation_ids"]):
            raise ValueError("训练/验证交叠")
        lookup = sites[r["state"]["complex"]]
        known = set().union(*(lookup[cid]["sites"] for cid in r["train_ids"]))
        for cid in r["validation_ids"]:
            candidate = lookup[cid]
            positions = set(candidate["sites"])
            if not positions:
                raise ValueError("候选位点为空")
            seen = len(positions & known)
            result.append(
                {
                    "state": r["state"],
                    "fold_index": r["fold_index"],
                    "candidate_id": cid,
                    "stratum": "all_seen" if seen == len(positions) else "any_unseen",
                    "coverage": "none_seen"
                    if seen == 0
                    else "all_seen"
                    if seen == len(positions)
                    else "partial_seen",
                    "seen_fraction": seen / len(positions),
                    "mutation_count": candidate["mutation_count"],
                }
            )
    return result


def load_source(args):
    if base.identity(args.oof_root / "terminal.json") != PARENT:
        raise ValueError("OOF终态身份漂移")
    terminal = json.loads((args.oof_root / "terminal.json").read_bytes())
    for name, sha in terminal["artifacts"].items():
        if base.identity(args.oof_root / name) != sha:
            raise ValueError("OOF产物身份漂移")
    old = SimpleNamespace(
        input_root=args.input_root, parent_root=args.feature_root, output=args.oof_root, mode="full"
    )
    cohort, states, features, all_ids = oof.load_source(old)
    oof.readback(old, cohort, states, features, all_ids)
    records = json.loads((args.oof_root / "private/records.json").read_bytes())
    candidates = {
        c["candidate_id"]: c for c in json.loads((args.input_root / "candidates.json").read_bytes())
    }
    sites = {}
    for c in cohort:
        f = json.loads(
            (args.feature_root / "private/features" / (c["complex"] + ".json")).read_bytes()
        )
        columns = f["columns"]["F2"]
        indices = [i for i, name in enumerate(columns) if name.startswith("site:")]
        sites[c["complex"]] = {}
        for cid, row in zip(c["ids"], f["matrices"]["F2"], strict=True):
            positions = sorted(columns[i][5:] for i in indices if row[i] > 0)
            if positions != sorted({t[1:-1] for t in candidates[cid]["mutations"]}):
                raise ValueError("F2位点列与原始突变不一致")
            sites[c["complex"]][cid] = {"sites": positions, "mutation_count": row[40]}
    if args.mode == "smoke":
        states = [
            s
            for s in states
            if s["complex"] in ("3BT1_A_U", "1CHO_EFG_I")
            and s["initial_size"] == 32
            and s["seed"] == 1729
        ]
    wanted = {base.digest(s) for s in states}
    records = [r for r in records if base.digest(r["state"]) in wanted]
    metadata = [
        {k: r[k] for k in ("state", "fold_index", "train_ids", "validation_ids")} for r in records
    ]
    masks = build_masks(metadata, sites)
    return states, records, masks, all_ids


def subset_metrics(rows):
    if not rows:
        return {
            "n": 0,
            "mae": None,
            "mean_sigma": None,
            "prediction_rho": None,
            "sigma_error_rho": None,
        }
    y, mean, sigma = ([r[k] for r in rows] for k in ("target", "mean", "sigma"))
    errors = [abs(a - b) for a, b in zip(y, mean, strict=True)]
    return {
        "n": len(rows),
        "mae": oof.feature.average(errors),
        "mean_sigma": oof.feature.average(sigma),
        "prediction_rho": oof.feature.correlation(mean, y) if len(rows) >= 4 else None,
        "sigma_error_rho": oof.feature.correlation(sigma, errors) if len(rows) >= 4 else None,
    }


def evaluate(states, records, masks, truth):
    expected = [
        (base.digest(r["state"]), r["fold_index"], cid)
        for r in records
        for cid in r["validation_ids"]
    ]
    if [(base.digest(m["state"]), m["fold_index"], m["candidate_id"]) for m in masks] != expected:
        raise ValueError("覆盖掩码未精确对齐OOF记录")
    predictions = {
        (base.digest(r["state"]), cid): (r["mean"][i], r["sigma"][i])
        for r in records
        for i, cid in enumerate(r["validation_ids"])
    }
    revealed = {}
    for state in states:
        sid = base.digest(state)
        ids = [m["candidate_id"] for m in masks if base.digest(m["state"]) == sid]
        if len(ids) != state["initial_size"] or len(set(ids)) != len(ids):
            raise ValueError("初始观测覆盖不守恒")
        revealed[sid] = {
            o.candidate_id: o.label
            for o in oof.HiddenOracle({cid: truth[cid] for cid in ids}).reveal(ids)
        }
    annotated = []
    for m in masks:
        sid, cid = base.digest(m["state"]), m["candidate_id"]
        mean, sigma = predictions[(sid, cid)]
        annotated.append({**m, "mean": mean, "sigma": sigma, "target": revealed[sid][cid]})
    rows = []
    for state in states:
        selected = [r for r in annotated if r["state"] == state]
        for subset in ("all", "single_mutation"):
            rr = selected if subset == "all" else [r for r in selected if r["mutation_count"] == 1]
            strata = {s: subset_metrics([r for r in rr if r["stratum"] == s]) for s in STRATA}
            a, b = (strata[s] for s in STRATA)
            paired = min(a["n"], b["n"]) >= 4
            contrast = (
                {
                    "mae_delta": b["mae"] - a["mae"],
                    "mae_ratio": b["mae"] / a["mae"] if a["mae"] else None,
                }
                if paired
                else None
            )
            if contrast is not None:
                for key in ("sigma_error_rho", "prediction_rho"):
                    contrast[key + "_delta"] = (
                        b[key] - a[key] if a[key] is not None and b[key] is not None else None
                    )
            rows.append(
                {
                    "state": state,
                    "subset": subset,
                    "strata": strata,
                    "paired": paired,
                    "contrast": contrast,
                    "coverage_counts": {
                        k: sum(r["coverage"] == k for r in rr)
                        for k in ("all_seen", "partial_seen", "none_seen")
                    },
                }
            )
    return annotated, rows


def average(values):
    return oof.feature.average(values)


def state_average(rows, get_value):
    """先平均同初始量的种子，再平均有定义的初始量，保留缺失支持。"""
    return average(
        [average([get_value(r) for r in rows if r["state"]["initial_size"] == n]) for n in (32, 64)]
    )


def describe(rows):
    paired = [r for r in rows if r["paired"]]
    return {
        "states": len(rows),
        "paired_states": len(paired),
        "paired_by_initial_size": {
            str(n): sum(r["paired"] for r in rows if r["state"]["initial_size"] == n)
            for n in (32, 64)
        },
        "contrast": {
            k: state_average(paired, lambda r, key=k: r["contrast"][key]) for k in CONTRASTS
        },
        "strata": {
            s: {
                "records": sum(r["strata"][s]["n"] for r in rows),
                "defined_states": {
                    k: sum(r["strata"][s][k] is not None for r in rows)
                    for k in ("mae", "mean_sigma", "prediction_rho", "sigma_error_rho")
                },
                "values": {
                    k: state_average(rows, lambda r, stratum=s, key=k: r["strata"][stratum][key])
                    for k in ("mae", "mean_sigma", "prediction_rho", "sigma_error_rho")
                },
            }
            for s in STRATA
        },
        "coverage_counts": {
            k: sum(r["coverage_counts"][k] for r in rows)
            for k in ("all_seen", "partial_seen", "none_seen")
        },
    }


def summary(rows):
    complexes = []
    for subset in ("all", "single_mutation"):
        for complex_id in sorted({r["state"]["complex"] for r in rows}):
            rr = [r for r in rows if r["subset"] == subset and r["state"]["complex"] == complex_id]
            d = describe(rr)
            complexes.append(
                {
                    "complex": complex_id,
                    "group": rr[0]["state"]["group"],
                    "subset": subset,
                    "eligible": d["paired_states"] >= 10,
                    **d,
                    "by_initial_size": {
                        str(n): describe([r for r in rr if r["state"]["initial_size"] == n])
                        for n in (32, 64)
                    },
                }
            )
    groups = []
    for subset in ("all", "single_mutation"):
        for group in sorted({r["group"] for r in complexes}):
            rr = [
                r
                for r in complexes
                if r["group"] == group and r["subset"] == subset and r["eligible"]
            ]
            groups.append(
                {
                    "group": group,
                    "subset": subset,
                    "eligible_complexes": [r["complex"] for r in rr],
                    "contrast": {k: average([r["contrast"][k] for r in rr]) for k in CONTRASTS},
                }
            )
    targets = [r for r in complexes if r["subset"] == "all" and r["complex"] in TARGETS]
    if len(targets) != 2 or not all(r["eligible"] for r in targets):
        decision = "inconclusive"
    elif all(
        r["contrast"]["mae_ratio"] is not None
        and r["contrast"]["mae_ratio"] >= 1.2
        and r["contrast"]["mae_delta"] > 0
        for r in targets
    ):
        decision = "positive"
    elif all(r["contrast"]["mae_delta"] <= 0 for r in targets):
        decision = "negative"
    else:
        decision = "unstable"
    supported = []
    for subset in ("all", "single_mutation"):
        rr = [g for g in groups if g["subset"] == subset and g["eligible_complexes"]]
        supported.append(
            {
                "subset": subset,
                "eligible_groups": len(rr),
                "contrast": {k: average([g["contrast"][k] for g in rr]) for k in CONTRASTS},
            }
        )
    return {
        "study": STUDY,
        "evidence_stage": "ITERATION",
        "independent_review": "PENDING",
        "decision": decision,
        "disposition": "continue"
        if decision == "positive"
        else "stop"
        if decision == "negative"
        else "revise",
        "states": len(rows) // 2,
        "new_fits": 0,
        "complexes": complexes,
        "groups": groups,
        "supported_groups_only": supported,
        "state_rows": rows,
    }


def compute(args):
    states, records, masks, all_ids = load_source(args)
    annotated, rows = evaluate(states, records, masks, base.read_truth(args.input_root, all_ids))
    return masks, annotated, summary(rows)


def verify(args):
    masks, annotated, report = compute(args)
    for name, value in (
        ("private/masks.json", masks),
        ("private/annotated.json", annotated),
        ("report.json", report),
    ):
        if base.encode(value) != (args.output / name).read_bytes():
            raise ValueError("分层产物回读不一致")
    return report


def main():
    p = argparse.ArgumentParser()
    for name in ("input-root", "feature-root", "oof-root", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--mode", choices=("smoke", "full"), required=True)
    p.add_argument("--verify", action="store_true")
    args = p.parse_args()
    config = base.identity(base.SOURCE / CONTRACT)
    if args.verify:
        t = json.loads((args.output / "terminal.json").read_bytes())
        if t["status"] != "succeeded" or t["config_identity"] != config or t["mode"] != args.mode:
            raise ValueError("终态不匹配")
        for name, sha in t["artifacts"].items():
            if base.identity(args.output / name) != sha:
                raise ValueError("产物哈希漂移")
        r = verify(args)
        print(json.dumps({"readback": "PASS", "states": r["states"]}))
        return
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base.SOURCE, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=base.SOURCE):
        raise ValueError("源码必须干净")
    prior = [json.loads(f.read_bytes()) for f in args.output.parent.glob("*/terminal.json")]
    smoke = [
        t
        for t in prior
        if t["mode"] == "smoke"
        and t["status"] == "succeeded"
        and t["executed_git_commit"] == head
        and t["config_identity"] == config
        and t["source_terminal_sha256"] == PARENT
    ]
    if args.mode == "full" and not smoke:
        raise ValueError("须先通过同源码smoke")
    if args.mode == "full":
        for name, sha in smoke[-1]["artifacts"].items():
            if base.identity(args.output.parent / smoke[-1]["attempt_id"] / name) != sha:
                raise ValueError("smoke产物漂移")
    used = sum(t["elapsed_seconds"] for t in prior)
    if used >= 600:
        raise ValueError("累计预算耗尽")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    peak = [0]
    stop = threading.Event()
    entry = {
        "study_id": STUDY,
        "attempt_id": args.output.name,
        "parent_attempt_id": smoke[-1]["attempt_id"] if args.mode == "full" else None,
        "executed_git_commit": head,
        "command": [sys.executable, *sys.argv],
        "config_identity": config,
        "source_terminal_sha256": PARENT,
        "data_identities": [PARENT, oof.PARENT, base.PROCESSING, base.SPLIT],
        "mode": args.mode,
        "started_at": datetime.now(UTC).isoformat(),
    }

    def ledger(v):
        with (args.output.parent / "attempts.jsonl").open("a") as f:
            f.write(json.dumps(v, sort_keys=True) + "\n")

    ledger({**entry, "status": "running"})

    def monitor():
        while not stop.wait(0.5):
            peak[0] = max(peak[0], base.rss_tree(os.getpid()))
            size = sum(f.stat().st_size for f in args.output.parent.rglob("*") if f.is_file())
            if peak[0] > 1610612736 or time.monotonic() - start + used > 600 or size > 134217728:
                failed = {
                    **entry,
                    "status": "stopped",
                    "failure_class": "budget",
                    "failure_reason": "资源上限",
                    "selection_disposition": "discarded",
                    "result_summary": None,
                    "elapsed_seconds": time.monotonic() - start,
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                base.write(args.output / "terminal.json", failed)
                ledger(failed)
                os._exit(2)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        base.write(
            args.output / "run-spec.json",
            {
                **entry,
                "python": sys.version,
                "prior_seconds": used,
                "minimum_stratum_n": 4,
                "minimum_paired_states": 10,
                "environment_freeze_sha256": base.hashlib.sha256(
                    subprocess.check_output([sys.executable, "-m", "pip", "freeze", "--all"])
                ).hexdigest(),
            },
        )
        masks, annotated, report = compute(args)
        for name, value in (
            ("private/masks.json", masks),
            ("private/annotated.json", annotated),
            ("report.json", report),
        ):
            base.write(args.output / name, value)
        verify(args)
        entry.update(
            status="succeeded",
            failure_class="none",
            failure_reason=None,
            selection_disposition="diagnostic",
            result_summary={
                "states": report["states"],
                "oof_records": len(masks),
                "readback": "PASS",
                "decision": report["decision"],
                "new_fits": 0,
            },
        )
    except Exception as exc:
        entry.update(
            status="failed",
            failure_class="validity" if isinstance(exc, ValueError) else "implementation",
            failure_reason=str(exc),
            selection_disposition="discarded",
            result_summary=None,
        )
        raise
    finally:
        stop.set()
        thread.join()
        entry.update(
            finished_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=time.monotonic() - start,
            peak_tree_rss_bytes=peak[0],
            cos_writes=0,
        )
        entry["artifacts"] = {
            str(f.relative_to(args.output)): base.identity(f)
            for f in args.output.rglob("*")
            if f.is_file()
        }
        base.write(args.output / "terminal.json", entry)
        ledger(entry)
        print(json.dumps({k: v for k, v in entry.items() if k != "artifacts"}))


if __name__ == "__main__":
    main()
