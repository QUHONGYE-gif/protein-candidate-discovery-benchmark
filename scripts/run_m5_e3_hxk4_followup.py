"""HXK4/64 new-state repeatability and label-blind crossed-frontier diagnosis."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import heapq
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
import run_m5_e3_generalization as gen  # noqa: E402

rep, e3, cov = gen.rep, gen.e3, gen.cov
PROTOCOL = "m5-e3-hxk4-frontier-repeat-development-v1"
ASSAY = "HXK4_HUMAN_Gersing_2022_activity"
NEW_SEEDS = tuple(range(3000, 3040))
ARMS = ("xgb", "ridge")
GEN_SOURCE = "f188cbabccd41d77aa223586d9cca788d9d00bb202d6f9d51d8e48d36135a700"
GEN_RESULT = "3baa407c62c56ec72d6f096c8352317c64377eb481f7d71b68b72e72c99e2c54"
GEN_EXEC = "ef5d45a3e23e7e2b81c4622670364695b713c203bef61b05738d98fb70c6ad49"


def initial(ids, seed):
    if seed not in (*rep.SEEDS, *NEW_SEEDS) or len(set(ids)) != len(ids) or len(ids) < 64:
        raise ValueError("invalid state")
    return tuple(
        cid
        for _, cid in heapq.nsmallest(
            64,
            (
                (
                    hashlib.sha256(
                        json.dumps(
                            [e3.rt.PROTOCOL_ID, ASSAY, seed, cid],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).digest(),
                    cid,
                )
                for cid in ids
            ),
        )
    )


def crossed(ids, predictions):
    orders = {a: np.lexsort((ids, -predictions[a])) for a in ARMS}
    pools = {a: orders[a][:64] for a in ARMS}
    choices = {}
    for p in ARMS:
        mask = np.zeros(len(ids), dtype=bool)
        mask[pools[p]] = True
        for rank in ARMS:
            choices[p[0] + rank[0]] = orders[rank][mask[orders[rank]]][:16]
    assert np.array_equal(choices["xx"], orders["xgb"][:16])
    assert np.array_equal(choices["rr"], orders["ridge"][:16])
    return orders, pools, choices


def fit_select(task):
    assay, ids, x, feature_sha, output = rep.WORKER
    index, seed, observations, baseline = task
    chosen = initial(ids, seed)
    observed = {o.candidate_id: float(o.label) for o in observations}
    if assay != ASSAY or len(observations) != 64 or set(observed) != set(chosen):
        raise ValueError("allowed observation mismatch")
    ordered = sorted(observed)
    lookup = {cid: i for i, cid in enumerate(ids)}
    train = np.array([lookup[c] for c in ordered])
    y = np.array([observed[c] for c in ordered])
    observation_sha = e3.identity(list(zip(ordered, y.tolist(), strict=True)))
    remaining = np.array([i for i, c in enumerate(ids) if c not in observed])
    candidate_ids = np.asarray(ids)[remaining]
    ridge = gen.ridge_fit(x[train], y)
    estimator = e3.rt._fit_model(rep.MODEL, x[train], y)
    predictions = {a: np.empty(len(remaining)) for a in ARMS}
    for start in range(0, len(remaining), 384):
        block = x[remaining[start : start + 384]]
        predictions["xgb"][start : start + len(block)] = e3.rt._predict(estimator, block)
        predictions["ridge"][start : start + len(block)] = gen.ridge_predict(ridge, block)
    if not all(np.isfinite(v).all() for v in predictions.values()):
        raise ValueError("nonfinite predictions")
    if baseline is not None:
        if (
            observation_sha != baseline["observation_sha"]
            or e3.digest(baseline["path"]) != baseline["sha"]
        ):
            raise ValueError("baseline identity mismatch")
        with np.load(baseline["path"], allow_pickle=False) as old:
            if not np.array_equal(remaining, old["remaining_indices"]) or any(
                not np.array_equal(predictions[a], old[a]) for a in ARMS
            ):
                raise ValueError("baseline point reconstruction failed")
    orders, pools, choices = crossed(candidate_ids, predictions)
    path = output / f"prediction-{index:03d}.npz"
    with path.open("xb") as f:
        np.savez(f, remaining_indices=remaining, **predictions)
    ridge_path = output / f"ridge-{index:03d}.npz"
    with ridge_path.open("xb") as f:
        np.savez(f, **ridge)
    config_path = output / f"xgb-config-{index:03d}.json"
    e3.write(config_path, json.loads(estimator.get_booster().save_config()))
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "assay_id": ASSAY,
            "initial_sample_size": 64,
            "run_seed": seed,
            "initial_ids": list(chosen),
            "allowed_observation_sha256": observation_sha,
            "feature_sha256": feature_sha,
            "prediction_sha256": e3.digest(path),
            "ridge_sha256": e3.digest(ridge_path),
            "xgb_config_sha256": e3.digest(config_path),
            "baseline_reconstruction": "EXACT" if baseline else None,
            "selected": {k: candidate_ids[v].tolist() for k, v in choices.items()},
            "pools": {a: candidate_ids[pools[a]].tolist() for a in ARMS},
            "training": {
                "xgb": {"spearman": e3.rho(e3.rt._predict(estimator, x[train]), y)},
                "ridge": {"spearman": e3.rho(gen.ridge_predict(ridge, x[train]), y)},
            },
        },
    )
    return index


def interval(values):
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all() or not len(values):
        raise ValueError("invalid interval data")
    slots = np.random.Generator(np.random.PCG64(14001)).integers(
        0, len(values), (20000, len(values))
    )
    return {
        "estimate": float(values.mean()),
        "interval": np.percentile(values[slots].mean(1), [0.3125, 99.6875]).tolist(),
        "n": len(values),
        "draws": 20000,
        "coverage": 0.99375,
        "seed": 14001,
    }


def difference_groups(a, b):
    rng = np.random.Generator(np.random.PCG64(14002))
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    av = a[rng.integers(0, len(a), (20000, len(a)))].mean(1)
    bv = b[rng.integers(0, len(b), (20000, len(b)))].mean(1)
    return {
        "estimate": float(a.mean() - b.mean()),
        "interval": np.percentile(av - bv, [0.3125, 99.6875]).tolist(),
        "group_sizes": [len(a), len(b)],
        "draws": 20000,
        "seed": 14002,
        "coverage": 0.99375,
    }


def sparse(rows):
    x = np.array([r["cells"]["xx"] for r in rows])
    y = np.array([r["cells"]["rr"] for r in rows])
    d = y - x
    positives = sorted(d[d > 0].tolist(), reverse=True)
    groups = {
        str(k): [
            float(di) for di, r in zip(d, rows, strict=True) if bool(r["initial_high_count"]) == k
        ]
        for k in (False, True)
    }
    return {
        "states": len(d),
        "both_zero": int(((x == 0) & (y == 0)).sum()),
        "ridge_only_hits": int(((x == 0) & (y > 0)).sum()),
        "xgb_only_hits": int(((x > 0) & (y == 0)).sum()),
        "both_hit": int(((x > 0) & (y > 0)).sum()),
        "positive": int((d > 0).sum()),
        "zero": int((d == 0).sum()),
        "negative": int((d < 0).sum()),
        "mean": float(d.mean()),
        "sample_sd": float(d.std(ddof=1)),
        "positive_part_mean": float(np.maximum(d, 0).mean()),
        "negative_part_mean": float(np.minimum(d, 0).mean()),
        "conditional_positive_mean": float(d[d > 0].mean()) if (d > 0).any() else None,
        "conditional_negative_mean": float(d[d < 0].mean()) if (d < 0).any() else None,
        "leave_one_out_range": [
            float((d.sum() - d).min() / (len(d) - 1)),
            float((d.sum() - d).max() / (len(d) - 1)),
        ],
        "largest_positive_states": [
            {
                "k": k,
                "available": min(k, len(positives)),
                "sum": float(sum(positives[:k])),
                "mean_without": float(
                    (d.sum() - sum(positives[:k])) / (len(d) - min(k, len(positives)))
                ),
            }
            for k in (1, 2, 4)
        ],
        "initial_high_groups": {
            k: {"n": len(v), "mean": float(np.mean(v)) if v else None} for k, v in groups.items()
        },
        "initial_high_interaction": difference_groups(groups["True"], groups["False"])
        if min(map(len, groups.values())) >= 8
        else None,
    }


def summarize(rows):
    output = []
    for label, seeds in (
        ("old20", rep.SEEDS),
        ("new40", NEW_SEEDS),
        ("new_first20", NEW_SEEDS[:20]),
        ("new_second20", NEW_SEEDS[20:]),
    ):
        rr = sorted([r for r in rows if r["run_seed"] in seeds], key=lambda r: r["run_seed"])
        if [r["run_seed"] for r in rr] != list(seeds):
            raise ValueError("state matrix mismatch")
        record = {
            "cohort": label,
            "states": len(rr),
            "paired_metrics": {},
            "frontier": {},
            "sparse": sparse(rr),
        }
        for key in rr[0]["metrics"]["xgb"]:
            a = [r["metrics"]["xgb"][key] for r in rr]
            b = [r["metrics"]["ridge"][key] for r in rr]
            defined = sum(x is not None and y is not None for x, y in zip(a, b, strict=True))
            v = {"defined_pairs": defined}
            if defined == len(rr):
                v.update(
                    xgb=float(np.mean(a)), ridge=float(np.mean(b)), change=interval(np.array(b) - a)
                )
            record["paired_metrics"][key] = v
        for section in ("cells", "frontier", "flow"):
            record[section] = {k: interval([r[section][k] for r in rr]) for k in rr[0][section]}
        output.append(record)
    old = [r["cells"]["rr"] - r["cells"]["xx"] for r in rows if r["run_seed"] in rep.SEEDS]
    new = [r["cells"]["rr"] - r["cells"]["xx"] for r in rows if r["run_seed"] in NEW_SEEDS]
    return output, difference_groups(new, old)


def score(path, meta_path, cohort, evaluator, table, old_ref=None):
    meta = json.loads(meta_path.read_text())
    seed = meta["run_seed"]
    chosen = initial(table.candidate_ids, seed)
    if meta["initial_ids"] != list(chosen) or e3.digest(path) != meta["prediction_sha256"]:
        raise ValueError("saved selection/prediction mismatch")
    truth = evaluator.truth._M5CTruthEvaluator__truth
    memberships = evaluator.truth._M5CTruthEvaluator__memberships
    high, top2 = memberships[0.01], memberships[0.02]
    if int(high.sum()) != 86:
        raise ValueError("high-value denominator changed")
    remaining = np.array([i for i, c in enumerate(table.candidate_ids) if c not in set(chosen)])
    with np.load(path, allow_pickle=False) as saved:
        if not np.array_equal(saved["remaining_indices"], remaining):
            raise ValueError("remaining pool identity mismatch")
        ids = np.asarray(table.candidate_ids)[remaining]
        pred = {a: saved[a] for a in ARMS}
        orders, pools, choices = crossed(ids, pred)
        if cohort != "old":
            if meta["selected"] != {k: ids[v].tolist() for k, v in choices.items()} or meta[
                "pools"
            ] != {a: ids[pools[a]].tolist() for a in ARMS}:
                raise ValueError("crossed choices not locked")
        else:
            if any(meta["selected"][a] != ids[orders[a][:64]].tolist() for a in ARMS):
                raise ValueError("old choices mismatch")
        hh = high[remaining]
        union = np.union1d(pools["xgb"], pools["ridge"])
        metrics = {}
        for a in ARMS:
            m = cov.prediction_metrics(pred[a], truth[remaining], hh, top2[remaining], orders[a])
            m["common_frontier_auc"] = e3.auc(pred[a][union], hh[union])
            for k in (8, 16, 32, 64):
                hits = int(hh[orders[a][:k]].sum())
                m.update({f"hits_{k}": hits, f"recall_{k}": hits / 86, f"precision_{k}": hits / k})
            ev = evaluator.truth.evaluate_batch(chosen, ids[orders[a][:16]].tolist())
            m["greedy_recall"] = ev["high_value"]["0.01"]["recall"]
            m["greedy_best"] = ev["best_found_improvement"]
            if m["hits_16"] != ev["high_value"]["0.01"]["count"]:
                raise ValueError("oracle hit mismatch")
            if old_ref and any(m[k] != v for k, v in old_ref["metrics"][a].items()):
                raise ValueError("old metric reconstruction failed")
            metrics[a] = m
        cells = {k: int(hh[v].sum()) for k, v in choices.items()}
        for k, v in choices.items():
            ev = evaluator.truth.evaluate_batch(chosen, ids[v].tolist())
            if cells[k] != ev["high_value"]["0.01"]["count"]:
                raise ValueError("crossed oracle mismatch")
        xx, xr, rx, rr = (cells[k] for k in ("xx", "xr", "rx", "rr"))
        frontier = {
            "pool_component": ((rx - xx) + (rr - xr)) / 2,
            "ranking_component": ((xr - xx) + (rr - rx)) / 2,
            "pool_at_xgb_rank": rx - xx,
            "pool_at_ridge_rank": rr - xr,
            "rank_in_xgb_pool": xr - xx,
            "rank_in_ridge_pool": rr - rx,
            "interaction": rr - rx - xr + xx,
            "xgb_pool_high": int(hh[pools["xgb"]].sum()),
            "ridge_pool_high": int(hh[pools["ridge"]].sum()),
            "common_frontier_high": int(hh[union].sum()),
            "common_frontier_size": len(union),
        }
        frontier.update(
            {
                "xgb_pool_oracle_bound": min(16, frontier["xgb_pool_high"]),
                "ridge_pool_oracle_bound": min(16, frontier["ridge_pool_high"]),
                "pool_high_difference": frontier["ridge_pool_high"] - frontier["xgb_pool_high"],
            }
        )
        common = np.intersect1d(choices["xx"], choices["rr"])
        flow = {"overlap": len(common), "shared_high": int(hh[common].sum())}
        for arm, other in (("ridge", "xgb"), ("xgb", "ridge")):
            exclusive = np.setdiff1d(orders[arm][:16], orders[other][:16])
            ranks = np.empty(len(ids), dtype=int)
            ranks[orders[other]] = np.arange(1, len(ids) + 1)
            for label, low, up in (
                ("17_64", 17, 64),
                ("65_256", 65, 256),
                ("beyond256", 257, len(ids)),
            ):
                index = exclusive[(ranks[exclusive] >= low) & (ranks[exclusive] <= up)]
                flow[arm + "_exclusive_count_" + label] = len(index)
                flow[arm + "_exclusive_hits_" + label] = int(hh[index].sum())
        assert frontier["pool_component"] + frontier["ranking_component"] == rr - xx
        for a, cell in (("xgb", "xx"), ("ridge", "rr")):
            assert (
                flow["shared_high"]
                + sum(flow[a + "_exclusive_hits_" + b] for b in ("17_64", "65_256", "beyond256"))
                == cells[cell]
            )
        return {
            "cohort": cohort,
            "assay_id": ASSAY,
            "initial_sample_size": 64,
            "run_seed": seed,
            "label_cost": 80,
            "remaining_count": len(remaining),
            "high_denominator": 86,
            "initial_high_count": int(high.sum() - hh.sum()),
            "metrics": metrics,
            "cells": cells,
            "frontier": frontier,
            "flow": flow,
            "prediction_sha256": e3.digest(path),
            "selection_sha256": e3.digest(meta_path),
            "allowed_observation_sha256": meta["allowed_observation_sha256"],
            "baseline_reconstruction": "EXACT" if old_ref else meta["baseline_reconstruction"],
        }


def selftest():
    gen.selftest()
    ids = tuple(f"s{i:04d}" for i in range(300))
    for seed in rep.SEEDS:
        assert initial(ids, seed) == rep.initial(ids, ASSAY, seed, 64)
    assert initial(ids, 3000) == initial(ids[::-1], 3000)
    assert not set(NEW_SEEDS) & set((*rep.SEEDS, *e3.RUN_SEEDS))
    pred = {"xgb": np.arange(300.0), "ridge": -np.arange(300.0)}
    orders, pools, choices = crossed(np.asarray(ids), pred)
    assert len(set(choices["xx"]) & set(choices["rr"])) == 0
    for p in ARMS:
        for r in ARMS:
            assert len(choices[p[0] + r[0]]) == 16
            assert set(choices[p[0] + r[0]]) <= set(pools[p])
    for vals in ((0, 2, 3, 4), (3, 0, 0, 2), (0, 0, 0, 0)):
        xx, xr, rx, rr = vals
        assert ((rx - xx) + (rr - xr)) / 2 + ((xr - xx) + (rr - rx)) / 2 == rr - xx
    assert interval(np.ones(40))["interval"] == [1.0, 1.0]


def run(args):
    selftest()
    for path, sha in (
        (gen.__file__, GEN_SOURCE),
        (rep.__file__, gen.REPLICATION_SOURCE_SHA),
        (e3.__file__, rep.HELPER_SHA),
        (cov.__file__, gen.COVERAGE_SHA),
        (e3.rt.__file__, rep.RUNTIME_SHA),
    ):
        if e3.digest(path) != sha:
            raise ValueError("source identity mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("runtime identity mismatch")
    previous = args.previous / "generalization-full-001"
    old_result = previous / "result.json.gz"
    if e3.digest(old_result) != GEN_RESULT:
        raise ValueError("old result mismatch")
    prior = json.loads(gzip.decompress(old_result.read_bytes()))
    refs = {
        r["run_seed"]: r
        for r in prior["rows"]
        if r["assay_id"] == ASSAY and r["initial_sample_size"] == 64
    }
    source = e3.exact_json(previous / "execution.json", GEN_EXEC)
    old_terminal = json.loads((previous / "terminal.json").read_text())
    if old_terminal["status"] != "succeeded" or any(
        e3.digest(previous / k) != old_terminal["artifacts"][k]
        for k in ("execution.json", "result.json.gz")
    ):
        raise ValueError("old terminal mismatch")
    feature_ref = next(r for r in source["input_identities"] if r["assay_id"] == ASSAY)
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    item = next(r for r in manifest["assays"] if r["assay_id"] == ASSAY)
    if (
        item["partition"] != "candidate-pool"
        or item["held_out_partitions"]
        or item["label_direction"] != "higher_is_better"
        or item["label_field"] != "DMS_score"
    ):
        raise ValueError("data/label scope mismatch")
    ref = item["artifacts"]["candidate"]
    table = e3.rt.load_candidate_table(
        root / ref["path"],
        assay_id=ASSAY,
        expected_sha256=ref["sha256"],
        expected_size=ref["size_bytes"],
    )
    if table.sha256 != feature_ref["candidate_sha256"]:
        raise ValueError("feature row mismatch")
    ref = item["artifacts"]["membership"]
    e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
    feature_path = args.previous / "full-001/private" / ASSAY / "features.npy"
    all_seeds = (*rep.SEEDS, *NEW_SEEDS)
    state_ids = [e3.identity(sorted(initial(table.candidate_ids, s))) for s in all_seeds]
    if len(set(state_ids)) != len(state_ids):
        raise ValueError("duplicate initial state")
    seeds = (2000,) if args.mode == "smoke" else NEW_SEEDS
    private = args.output / "private"
    private.mkdir()
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "generalization_source_sha256": GEN_SOURCE,
            "generalization_result_sha256": GEN_RESULT,
            "generalization_execution_sha256": GEN_EXEC,
            "input_sha256": e3.INPUT_SHA,
            "input_identity": feature_ref,
            "versions": versions,
            "mode": args.mode,
            "fit_seeds": list(seeds),
            "old_diagnostic_seeds": list(rep.SEEDS) if args.mode == "full" else [],
            "initial_state_set_sha256": dict(zip(map(str, all_seeds), state_ids, strict=True)),
            "xgb_parameters": dict(e3.rt.model_effective_parameters(rep.MODEL)),
            "ridge_parameters": source["ridge_parameters"],
            "batch_size": 16,
            "pool_size": 64,
            "label_cost": 80,
            "evidence_stage": "ITERATION",
            "application_delta": None,
            "truth_boundary": "only64 allowed labels in worker; lock and exit before score",
        },
    )
    evaluator = e3.Evaluator(root, item, table)
    old_private = previous / "private" / ASSAY
    initargs = (
        ASSAY,
        table.candidate_ids,
        str(feature_path),
        feature_ref["new_feature_sha256"],
        str(private),
    )
    with mp.get_context("spawn").Pool(1, initializer=rep.initialize, initargs=initargs) as pool:
        for index, seed in enumerate(seeds):
            baseline = None
            if args.mode == "smoke":
                baseline = {
                    "path": str(old_private / "prediction-020.npz"),
                    "sha": refs[seed]["prediction_sha256"],
                    "observation_sha": refs[seed]["allowed_observation_sha256"],
                }
            task = (
                index,
                seed,
                evaluator.truth.reveal(initial(table.candidate_ids, seed)),
                baseline,
            )
            if pool.apply(fit_select, (task,)) != index:
                raise ValueError("worker return mismatch")
            e3.event(args.output, "predictions_saved", index=index, run_seed=seed)
    e3.event(args.output, "workers_exited")
    rows = []
    for index, seed in enumerate(seeds):
        meta_path = private / f"selection-{index:03d}.json"
        meta = json.loads(meta_path.read_text())
        for filename, field in (
            (f"ridge-{index:03d}.npz", "ridge_sha256"),
            (f"xgb-config-{index:03d}.json", "xgb_config_sha256"),
        ):
            if e3.digest(private / filename) != meta[field]:
                raise ValueError("saved model hash mismatch")
        rows.append(
            score(
                private / f"prediction-{index:03d}.npz",
                meta_path,
                "smoke" if args.mode == "smoke" else "new",
                evaluator,
                table,
                refs[seed] if args.mode == "smoke" else None,
            )
        )
    if args.mode == "full":
        for index, seed in enumerate(rep.SEEDS, 20):
            path = old_private / f"prediction-{index:03d}.npz"
            meta_path = old_private / f"selection-{index:03d}.json"
            if (
                e3.digest(path) != refs[seed]["prediction_sha256"]
                or e3.digest(meta_path) != refs[seed]["selection_sha256"]
            ):
                raise ValueError("old artifact identity mismatch")
            rows.append(score(path, meta_path, "old", evaluator, table, refs[seed]))
    e3.event(args.output, "scoring_complete", states=len(rows))
    summaries, gap = summarize(rows) if args.mode == "full" else ([], None)
    result = {
        "protocol": PROTOCOL,
        "completed_states": len(rows),
        "new_states": 40 if args.mode == "full" else 0,
        "point_fits": 2 * len(seeds),
        "rows": rows,
        "summaries": summaries,
        "new_minus_old": gap,
        "limitations": [
            "same seen development assay",
            "new states not new measurements",
            "operational K64 decomposition not unique upstream cause",
            "no noise attribution",
            "no E/UQ",
        ],
    }
    with (args.output / "result.json.gz").open("xb") as f:
        f.write(
            gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0)
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "previous", "output", "plan"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--wall-seconds", type=int, default=90)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        print("HXK4 state/selection/decomposition checks PASS")
        return 0
    if any(getattr(args, k) is None for k in ("study", "previous", "output", "plan")):
        parser.error("study, previous, output and plan required")
    if not 0 < args.wall_seconds <= (90 if args.mode == "smoke" else 800):
        parser.error("invalid mode wall cap")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    started, stop, breach = time.monotonic(), threading.Event(), []
    usage = {"peak_rss_bytes": 0, "peak_disk_bytes": 0}

    def terminate(signum, frame):
        raise RuntimeError("interrupted or resource cap")

    def monitor():
        while not stop.wait(1):
            rss = e3.tree_rss(os.getpid())
            size = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
            usage["peak_rss_bytes"] = max(usage["peak_rss_bytes"], rss)
            usage["peak_disk_bytes"] = max(usage["peak_disk_bytes"], size)
            if (
                rss > 1610612736
                or size > 268435456
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
