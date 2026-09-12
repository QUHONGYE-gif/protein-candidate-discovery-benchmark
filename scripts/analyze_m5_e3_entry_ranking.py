"""Frozen-prediction pool/ranking cross; only the public evaluator scores locked batches."""

from __future__ import annotations

import argparse
import gzip
import importlib.metadata
import json
import multiprocessing as mp
import os
import signal
import threading
import time
from pathlib import Path

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"
import numpy as np  # noqa: E402
import run_m5_e3_label_balanced_support as lb  # noqa: E402

e3, hx = lb.e3, lb.hx
PROTOCOL = "m5-e3-entry-ranking-development-v1"
PRIOR_RESULT = "3dd011bd63f5d9c844d3678a4368c8830a4af17add3158a1a227ea2517a88b95"
PRIOR_SOURCE = "239485d48468bcfc467fb6f464a81e23469b3797d7830a706c0f48c1323af080"
PRIOR_EXEC = "d89cca88a90b31850f43d21a7fbe1fabece4c7260f0ea50cbea8cd85a0c9c363"
IDS = ()
SLOTS = {}
read = lb.read


def initialize(ids):
    global IDS
    IDS = tuple(ids)


def within(order, pool, count=16):
    mask = np.zeros(len(order), dtype=bool)
    mask[pool] = True
    return order[mask[order]][:count]


def components(cells):
    aa, ab, ba, bb = (cells[k] for k in ("AA", "AB", "BA", "BB"))
    p = ((ba - aa) + (bb - ab)) / 2
    r = ((ab - aa) + (bb - ba)) / 2
    return {
        "total": bb - aa,
        "pool": p,
        "ranking": r,
        "pool_minus_ranking": p - r,
        "pool_at_old_rank": ba - aa,
        "pool_at_new_rank": bb - ab,
        "ranking_in_old_pool": ab - aa,
        "ranking_in_new_pool": bb - ba,
        "interaction": bb - ba - ab + aa,
    }


def prepare(task):
    index, seed, prior_path, output_path, pred_sha, selection_sha, observed_sha = task
    prior, output = Path(prior_path), Path(output_path)
    pred = prior / f"prediction-{index:03d}.npz"
    sel = prior / f"selection-{index:03d}.json"
    if e3.digest(pred) != pred_sha or e3.digest(sel) != selection_sha:
        raise ValueError("prior cache identity")
    old = read(sel)
    if old["run_seed"] != seed or old["allowed_observation_sha256"] != observed_sha:
        raise ValueError("prior observation identity")
    initial = hx.initial(IDS, seed)
    remaining = np.array([i for i, c in enumerate(IDS) if c not in set(initial)])
    ids = np.asarray(IDS)[remaining]
    if len(remaining) != 8506:
        raise ValueError("remaining size")
    with np.load(pred, allow_pickle=False) as data:
        if not np.array_equal(data["remaining_indices"], remaining):
            raise ValueError("remaining identity")
        reference = data["reference64"].copy()
        if (
            len(reference) != 64
            or len(set(reference)) != 64
            or not np.all((reference >= 0) & (reference < len(remaining)))
        ):
            raise ValueError("reference pool")
        orders = {}
        for model in lb.MODELS:
            for arm in lb.ARMS:
                key = model + "_" + arm
                values = data[key]
                if values.shape != (8506,) or not np.isfinite(values).all():
                    raise ValueError("prediction shape/value")
                orders[key] = np.lexsort((ids, -values))
    pools = {k: o[:64] for k, o in orders.items()}
    own = {k: o[:16] for k, o in orders.items()}
    fixed = {k: within(o, reference) for k, o in orders.items()}
    if old["selected64"] != {k: ids[v].tolist() for k, v in pools.items()} or old[
        "fixed_selected16"
    ] != {k: ids[v].tolist() for k, v in fixed.items()}:
        raise ValueError("prior selections not reconstructed")
    crosses = []
    for model in lb.MODELS:
        for a, b, role in lb.CONTRASTS:
            ka, kb = model + "_" + a, model + "_" + b
            cells = {
                "AA": within(orders[ka], pools[ka]),
                "AB": within(orders[kb], pools[ka]),
                "BA": within(orders[ka], pools[kb]),
                "BB": within(orders[kb], pools[kb]),
            }
            if not np.array_equal(cells["AA"], own[ka]) or not np.array_equal(cells["BB"], own[kb]):
                raise ValueError("self cross mismatch")
            crosses.append(
                {
                    "model": model,
                    "reference": a,
                    "alternative": b,
                    "role": role,
                    "selected": {k: ids[v].tolist() for k, v in cells.items()},
                    "pool_overlap": len(set(pools[ka]) & set(pools[kb])),
                }
            )
    meta = {
        "run_seed": seed,
        "initial_ids": list(initial),
        "allowed_observation_sha256": observed_sha,
        "prediction_sha256": pred_sha,
        "prior_selection_sha256": selection_sha,
        "reference64": ids[reference].tolist(),
        "pools": {k: ids[v].tolist() for k, v in pools.items()},
        "own": {k: ids[v].tolist() for k, v in own.items()},
        "fixed": {k: ids[v].tolist() for k, v in fixed.items()},
        "crosses": crosses,
    }
    all_batches = list(meta["own"].values()) + list(meta["fixed"].values())
    all_batches += [s for c in crosses for s in c["selected"].values()]
    if any(len(s) != 16 or len(set(s)) != 16 or set(s) & set(initial) for s in all_batches):
        raise ValueError("invalid locked batch")
    path = output / f"locked-{index:03d}.json"
    e3.write(path, meta)
    return e3.digest(path)


def interval(values):
    v = np.asarray(values, dtype=float)
    if not np.isfinite(v).all() or not len(v):
        raise ValueError("interval values")
    if len(v) not in SLOTS:
        SLOTS[len(v)] = np.random.Generator(np.random.PCG64(18001)).integers(
            0, len(v), (20000, len(v))
        )
    return {
        "estimate": float(v.mean()),
        "interval": np.percentile(v[SLOTS[len(v)]].mean(1), [0.3125, 99.6875]).tolist(),
        "n": len(v),
        "seed": 18001,
        "draws": 20000,
        "coverage": 0.99375,
    }


def score_locked(path, lock_sha, prior, evaluator, candidate_ids):
    if e3.digest(path) != lock_sha:
        raise ValueError("locked choices modified")
    meta = read(path)
    seed = meta["run_seed"]
    initial = hx.initial(candidate_ids, seed)
    if meta["initial_ids"] != list(initial) or prior["run_seed"] != seed:
        raise ValueError("scoring state")
    memo = {}

    def evaluate(ids):
        key = tuple(sorted(ids))
        if key not in memo:
            ev = evaluator.truth.evaluate_batch(initial, ids)
            high = ev["high_value"]["0.01"]
            hits = high["count"]
            if type(hits) is not int or not 0 <= hits <= 16 or high["recall"] != hits / 86:
                raise ValueError("public evaluator units")
            memo[key] = (hits, ev["best_found_improvement"])
        return memo[key]

    arm_metrics = {}
    for model in lb.MODELS:
        for arm in lb.ARMS:
            key = model + "_" + arm
            old = prior["metrics"][model][arm]
            h, best = evaluate(meta["own"][key])
            f, _ = evaluate(meta["fixed"][key])
            if h != old["hits_16"] or f != old["fixed_hits_16"] or best != old["best_improvement"]:
                raise ValueError("prior outcome reconstruction")
            h64 = old["hits_64"]
            if type(h64) is not int or not h <= h64 <= 64:
                raise ValueError("saved frontier count")
            cap = min(h64, 16)
            arm_metrics[key] = {
                "hits_16": h,
                "hits_64": h64,
                "capacity": cap,
                "unused_capacity": cap - h,
                "fixed_hits_16": f,
                "global_minus_fixed": h - f,
                "fixed_selection_overlap": len(set(meta["own"][key]) & set(meta["fixed"][key])),
                "global16_outside_reference": len(set(meta["own"][key]) - set(meta["reference64"])),
            }
    comparisons = []
    for c in meta["crosses"]:
        cells = {k: evaluate(ids)[0] for k, ids in c["selected"].items()}
        effects = components(cells)
        a = arm_metrics[c["model"] + "_" + c["reference"]]
        b = arm_metrics[c["model"] + "_" + c["alternative"]]
        changes = {k: b[k] - a[k] for k in a}
        if (
            effects["total"] != changes["hits_16"]
            or effects["total"] != effects["pool"] + effects["ranking"]
            or effects["total"] != changes["capacity"] - changes["unused_capacity"]
            or effects["total"] != changes["fixed_hits_16"] + changes["global_minus_fixed"]
        ):
            raise ValueError("decomposition identities")
        comparisons.append(
            {
                **{k: c[k] for k in ("model", "reference", "alternative", "role", "pool_overlap")},
                "cells": cells,
                "effects": effects,
                "changes": changes,
            }
        )
    return {
        "run_seed": seed,
        "locked_sha256": lock_sha,
        "allowed_observation_sha256": meta["allowed_observation_sha256"],
        "prediction_sha256": meta["prediction_sha256"],
        "prior_selection_sha256": meta["prior_selection_sha256"],
        "arm_metrics": arm_metrics,
        "comparisons": comparisons,
        "public_evaluator_calls": len(memo),
    }


def summarize(rows):
    arms = {}
    for key in rows[0]["arm_metrics"]:
        mm = [r["arm_metrics"][key] for r in rows]
        arms[key] = {
            "metrics": {k: interval([r[k] for r in mm]) for k in mm[0]},
            "state_counts": {
                "no_high_in_pool": sum(r["hits_64"] == 0 for r in mm),
                "high_in_pool_but_zero_hits": sum(
                    r["hits_64"] > 0 and r["hits_16"] == 0 for r in mm
                ),
                "any_hit": sum(r["hits_16"] > 0 for r in mm),
            },
        }
    comparisons = []
    for index, first in enumerate(rows[0]["comparisons"]):
        rr = [r["comparisons"][index] for r in rows]
        d = np.array([r["effects"]["total"] for r in rr])
        comparisons.append(
            {
                **{k: first[k] for k in ("model", "reference", "alternative", "role")},
                **{
                    section: {k: interval([r[section][k] for r in rr]) for k in first[section]}
                    for section in ("cells", "effects", "changes")
                },
                "pool_overlap": interval([r["pool_overlap"] for r in rr]),
                "state_counts": {
                    "positive": int((d > 0).sum()),
                    "zero": int((d == 0).sum()),
                    "negative": int((d < 0).sum()),
                    "zero_interaction": sum(r["effects"]["interaction"] == 0 for r in rr),
                    "same_pool": sum(r["pool_overlap"] == 64 for r in rr),
                },
            }
        )
    return {"arms": arms, "comparisons": comparisons}


def selftest():
    c = components({"AA": 1, "AB": 3, "BA": 2, "BB": 4})
    assert (c["total"], c["pool"], c["ranking"], c["interaction"]) == (3, 1, 2, 0)
    c = components({"AA": 1, "AB": 1, "BA": 1, "BB": 5})
    assert (c["total"], c["pool"], c["ranking"], c["interaction"]) == (4, 2, 2, 4)
    order = np.array([5, 1, 8, 3, 0, 2, 4, 6, 7])
    assert within(order, np.array([7, 2, 5, 8]), count=3).tolist() == [5, 8, 2]


def run(args):
    selftest()
    prior_dir = args.previous / "label-balanced-support-full-001"
    if (
        e3.digest(prior_dir / "result.json.gz") != PRIOR_RESULT
        or e3.digest(lb.__file__) != PRIOR_SOURCE
    ):
        raise ValueError("prior source/result identity")
    if e3.digest(prior_dir / "execution.json") != PRIOR_EXEC:
        raise ValueError("prior execution identity")
    execution = read(prior_dir / "execution.json")
    helpers = (lb.ss, lb.rs, hx, lb.gen, lb.rep, lb.cov, e3, e3.rt)
    for module in helpers:
        if e3.digest(module.__file__) != execution["helper_sha256"][Path(module.__file__).name]:
            raise ValueError("helper identity")
    versions = {k: importlib.metadata.version(k) for k in execution["versions"]}
    if versions != execution["versions"]:
        raise ValueError("runtime versions")
    references = {r["run_seed"]: r for r in read(prior_dir / "result.json.gz")["rows"]}
    if sorted(references) != list(range(3000, 3040)):
        raise ValueError("prior states")
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", execution["input_sha256"])
    item = next(r for r in manifest["assays"] if r["assay_id"] == hx.ASSAY)
    if (
        item["held_out_partitions"]
        or item["partition"] != "candidate-pool"
        or (item["label_field"], item["label_direction"]) != ("DMS_score", "higher_is_better")
    ):
        raise ValueError("data boundary")
    ref = item["artifacts"]["candidate"]
    table = e3.rt.load_candidate_table(
        root / ref["path"],
        assay_id=hx.ASSAY,
        expected_sha256=ref["sha256"],
        expected_size=ref["size_bytes"],
    )
    if (
        table.sha256 != execution["input_identity"]["candidate_sha256"]
        or len(table.candidate_ids) != 8570
    ):
        raise ValueError("candidate identity")
    seeds = (3000,) if args.mode == "smoke" else tuple(range(3000, 3040))
    private = args.output / "private"
    private.mkdir()
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "prior_result_sha256": PRIOR_RESULT,
            "prior_source_sha256": PRIOR_SOURCE,
            "prior_execution_sha256": PRIOR_EXEC,
            "helper_sha256": execution["helper_sha256"],
            "versions": versions,
            "input_sha256": execution["input_sha256"],
            "input_identity": execution["input_identity"],
            "state_seeds": list(seeds),
            "models": list(lb.MODELS),
            "arms": list(lb.ARMS),
            "contrasts": [list(c) for c in lb.CONTRASTS],
            "initial_sample_size": 64,
            "training_size": 32,
            "label_cost": 80,
            "remaining_size": 8506,
            "batch_size": 16,
            "pool_size": 64,
            "high_denominator": 86,
            "new_fits": 0,
            "new_labels": 0,
            "application_delta": None,
            "evidence_stage": "ITERATION",
            "worker_inputs": "candidate IDs, cache paths, seed/index, three identity hashes only",
            "scoring": "public evaluate_batch after locked choices and worker exit; no reveal",
        },
    )
    locks = []
    with mp.get_context("spawn").Pool(
        1, initializer=initialize, initargs=(table.candidate_ids,)
    ) as pool:
        for index, seed in enumerate(seeds):
            r = references[seed]
            task = (
                index,
                seed,
                str(prior_dir / "private"),
                str(private),
                r["prediction_sha256"],
                r["selection_sha256"],
                r["allowed_observation_sha256"],
            )
            locks.append(pool.apply(prepare, (task,)))
            e3.event(
                args.output, "choices_locked", index=index, run_seed=seed, locked_sha256=locks[-1]
            )
    e3.event(args.output, "workers_exited")
    evaluator = e3.Evaluator(root, item, table)
    e3.event(args.output, "public_evaluator_started")
    rows = []
    for index, seed in enumerate(seeds):
        rows.append(
            score_locked(
                private / f"locked-{index:03d}.json",
                locks[index],
                references[seed],
                evaluator,
                table.candidate_ids,
            )
        )
    e3.event(args.output, "scoring_completed", calls=sum(r["public_evaluator_calls"] for r in rows))
    result = {
        "protocol": PROTOCOL,
        "mode": args.mode,
        "completed_states": len(rows),
        "new_fits": 0,
        "public_evaluator_calls": sum(r["public_evaluator_calls"] for r in rows),
        "rows": rows,
        **summarize(rows),
        "limitations": [
            "seen-assay diagnostic; original main outcomes reconstructed, not replicated",
            "conditional pool/ranking accounting; not upstream causes or independent mediation",
            "reference gap is net effect, not individual high-candidate displacement",
            "capacity is an ideal-ranking upper bound, not an implementable policy",
            "same-account process separation is not OS isolation",
        ],
    }
    (args.output / "result.json.gz").write_bytes(
        gzip.compress(
            json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":")).encode(),
            mtime=0,
        )
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "previous", "output", "plan"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--mode", choices=("smoke", "full"), required=True)
    p.add_argument("--wall-seconds", type=int, required=True)
    args = p.parse_args()
    if not 0 < args.wall_seconds <= (45 if args.mode == "smoke" else 100):
        p.error("invalid wall cap")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    started, stop, breach = time.monotonic(), threading.Event(), []
    usage = {"peak_rss_bytes": 0, "peak_disk_bytes": 0}

    def terminate(signum, frame):
        raise RuntimeError("interrupted or resource cap")

    def monitor():
        while not stop.wait(0.5):
            rss = e3.tree_rss(os.getpid())
            size = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
            usage["peak_rss_bytes"] = max(usage["peak_rss_bytes"], rss)
            usage["peak_disk_bytes"] = max(usage["peak_disk_bytes"], size)
            if (
                rss > 1610612736
                or size > 67108864
                or time.monotonic() - started > args.wall_seconds
            ):
                breach.append("budget")
                os.kill(os.getpid(), signal.SIGTERM)
                return

    signal.signal(signal.SIGTERM, terminate)
    e3.event(args.output, "attempt_started", mode=args.mode, source_sha256=e3.digest(__file__))
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    status, failure = "failed", None
    try:
        run(args)
        status = "succeeded"
    except BaseException as exc:
        import traceback

        failure = "budget" if breach else type(exc).__name__
        e3.write(
            args.output / "failure.json",
            {
                "failure_class": failure,
                "message": str(exc),
                "frames": [
                    {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                    for f in traceback.extract_tb(exc.__traceback__)
                ],
            },
        )
    finally:
        stop.set()
        thread.join(timeout=3)
        usage["peak_disk_bytes"] = max(
            usage["peak_disk_bytes"],
            sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file()),
        )
        e3.event(args.output, "attempt_finished", status=status, failure_class=failure)
        terminal = {
            "status": status,
            "failure_class": failure,
            "mode": args.mode,
            "wall_seconds": time.monotonic() - started,
            **usage,
            "source_sha256": e3.digest(__file__),
            "artifacts": {p.name: e3.digest(p) for p in args.output.iterdir() if p.is_file()},
        }
        e3.write(args.output / "terminal.json", terminal)
        print(json.dumps(terminal), flush=True)
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
