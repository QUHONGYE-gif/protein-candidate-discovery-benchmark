#!/usr/bin/env python3
"""Fixed-data objective contrast on the three approved benign C assays."""

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
import run_m5_e3_fixed_count_coverage as cov  # noqa: E402
import run_m5_e3_prediction as e3  # noqa: E402
from xgboost import XGBRanker  # noqa: E402

PROTOCOL = "m5-e3-ranking-objective-v1"
MODEL = "xgboost-cpu-v1"
COVERAGE_SOURCE_SHA = "477d9b67b0e2b3985b60e09b356d2585c5d2b1734f06cc9ac1af3e81ec7f131c"
RANK_PARAMETERS = {
    "objective": "rank:pairwise",
    "lambdarank_pair_method": "mean",
    "lambdarank_num_pair_per_sample": 4,
    "lambdarank_normalization": True,
    "lambdarank_score_normalization": True,
    "lambdarank_unbiased": False,
}
WORKER = None


def ordinal_labels(y):
    """Only the supplied allowed observations contribute to these ranks."""
    values = np.asarray(y, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("invalid observed labels")
    return np.unique(values, return_inverse=True)[1]


def rank_fit(x, y):
    parameters = dict(e3.rt.model_effective_parameters(MODEL))
    parameters.update(RANK_PARAMETERS)
    estimator = XGBRanker(**parameters)
    estimator.fit(x, ordinal_labels(y), qid=np.zeros(len(y), dtype=np.int32), verbose=False)
    return estimator


def orders_and_frontier(ids, before, after):
    if len(ids) < 64 or before.shape != after.shape or before.shape != (len(ids),):
        raise ValueError("invalid candidate predictions")
    if not np.isfinite(before).all() or not np.isfinite(after).all():
        raise ValueError("nonfinite prediction")
    old, new = (np.lexsort((ids, -v)) for v in (before, after))
    return old, new, np.union1d(old[:64], new[:64])


def initialize(assay, ids, feature_path, feature_sha, output):
    global WORKER
    features = np.load(feature_path, mmap_mode="r", allow_pickle=False)
    if (
        assay not in e3.ASSAY_IDS
        or e3.digest(feature_path) != feature_sha
        or features.shape != (len(ids), 421)
        or not np.isfinite(features).all()
    ):
        raise ValueError("observable identity mismatch")
    WORKER = assay, tuple(ids), features, feature_sha, Path(output)


def fit_select(task):
    assay, ids, features, feature_sha, output = WORKER
    index, size, seed, observations, old_dir, expected_source = task
    initial = e3.initial_selection(
        ids, assay_id=assay, run_seed=seed, size=size, assay_ids=e3.ASSAY_IDS
    )
    reservoir = e3.initial_selection(
        ids, assay_id=assay, run_seed=seed, size=64, assay_ids=e3.ASSAY_IDS
    )
    observed = {o.candidate_id: float(o.label) for o in observations}
    if len(observations) != size or len(observed) != size or set(observed) != set(initial):
        raise ValueError("fixed observation identity mismatch")
    lookup = {c: i for i, c in enumerate(ids)}
    oi = np.array([lookup[c] for c in sorted(observed)])
    x, y = features[oi], np.array([observed[c] for c in sorted(observed)])
    common = np.array([i for i, c in enumerate(ids) if c not in set(reservoir)])
    meta, remaining, old_mean, _ = cov.load_prediction(old_dir, index)
    if (
        meta["prediction_sha256"] != expected_source["source_prediction_shas"][1]
        or e3.digest(Path(old_dir) / f"selection-{index:03d}.json")
        != expected_source["source_selection_shas"][1]
        or meta["model_id"] != MODEL
        or meta["run_seed"] != seed
        or list(initial) != meta["initial_ids"]
    ):
        raise ValueError("old state identity mismatch")
    positions = np.searchsorted(remaining, common)
    if (positions >= len(remaining)).any() or not np.array_equal(remaining[positions], common):
        raise ValueError("common pool mismatch")
    estimators = (e3.rt._fit_model(MODEL, x, y), rank_fit(x, y))
    values = np.empty((2, len(common)))
    for start in range(0, len(common), 384):
        block = features[common[start : start + 384]]
        for j, estimator in enumerate(estimators):
            values[j, start : start + len(block)] = e3.rt._predict(estimator, block)
    if not np.array_equal(values[0], old_mean[positions]):
        raise ValueError("regression point refit failed exact reconstruction")
    common_ids = np.asarray(ids)[common]
    before, after, frontier = orders_and_frontier(common_ids, *values)
    path = output / f"prediction-{index:03d}.npz"
    with path.open("xb") as handle:
        np.savez(
            handle,
            remaining_indices=common,
            regression=values[0],
            ranking=values[1],
            regression_order=before,
            ranking_order=after,
            frontier=frontier,
        )
    configs = {
        arm: json.loads(est.get_booster().save_config())
        for arm, est in zip(("regression", "ranking"), estimators, strict=True)
    }
    config_path = output / f"model-config-{index:03d}.json"
    e3.write(config_path, configs)
    e3.write(
        output / f"selection-{index:03d}.json",
        {
            "assay_id": assay,
            "model_id": MODEL,
            "run_seed": seed,
            "training_count": size,
            "initial_ids": list(initial),
            "common_initial_ids": list(reservoir),
            "feature_sha256": feature_sha,
            "observation_sha256": e3.identity(sorted(observed.items())),
            "ordinal_observation_sha256": e3.identity(ordinal_labels(y).tolist()),
            "prediction_sha256": e3.digest(path),
            "model_config_sha256": e3.digest(config_path),
            "regression": common_ids[before[:16]].tolist(),
            "ranking": common_ids[after[:16]].tolist(),
            "training_spearman": {
                arm: e3.rho(e3.rt._predict(est, x), y)
                for arm, est in zip(("regression", "ranking"), estimators, strict=True)
            },
            "old_source_identity": expected_source,
        },
    )
    return index


def summarize(rows):
    summaries = []
    for assay in e3.ASSAY_IDS:
        for size in (32, 64):
            cell = sorted(
                [r for r in rows if r["assay_id"] == assay and r["training_count"] == size],
                key=lambda r: r["run_seed"],
            )
            if [r["run_seed"] for r in cell] != list(e3.RUN_SEEDS):
                raise ValueError("paired seed coverage mismatch")
            effects = {}
            for metric in cell[0]["regression"]:
                pairs = [(r["regression"][metric], r["ranking"][metric]) for r in cell]
                defined = sum(a is not None and b is not None for a, b in pairs)
                if defined != 10:
                    effects[metric] = {"defined_pairs": defined}
                else:
                    effects[metric] = {
                        "regression": float(np.mean([a for a, b in pairs])),
                        "ranking": float(np.mean([b for a, b in pairs])),
                        "change": e3.interval([b - a for a, b in pairs], 11200 + size),
                    }
            summaries.append(
                {"assay_id": assay, "training_count": size, "paired_states": 10, "effects": effects}
            )
    return summaries


def selftest():
    e3.selftest()
    y = np.array([-3.2, 9.1, -3.2, 0.4])
    ranks = ordinal_labels(y)
    assert ranks.tolist() == [0, 2, 0, 1]
    assert np.array_equal(ranks, ordinal_labels(10 * y + 7))
    ids = np.array([f"id{i:03d}" for i in range(80)])
    a, b, front = orders_and_frontier(ids, np.zeros(80), np.arange(80.0))
    assert a[:16].tolist() == list(range(16)) and b[0] == 79
    assert set(front) == set(a[:64]) | set(b[:64])
    x = np.arange(32.0).reshape(-1, 1) / 32
    y = np.sin(x[:, 0] * 2) - 3
    model = rank_fit(x, y)
    assert e3.rho(model.predict(x), y) > 0.9
    assert np.array_equal(model.predict(x), rank_fit(x, 10 * y + 3).predict(x))
    assert np.isfinite(rank_fit(x, np.ones(32)).predict(x)).all()


def run(args):
    selftest()
    for path, expected in (
        (e3.__file__, cov.HELPER_SHA),
        (e3.rt.__file__, cov.RUNTIME_SHA),
        (cov.__file__, COVERAGE_SOURCE_SHA),
        (args.previous / "full-001/result.json.gz", cov.INPUT_RESULT_SHA),
        (args.previous / "information-full-001/result.json.gz", cov.INFORMATION_RESULT_SHA),
    ):
        if e3.digest(path) != expected:
            raise ValueError("source/prior identity mismatch")
    versions = {k: importlib.metadata.version(k) for k in ("numpy", "scikit-learn", "xgboost-cpu")}
    if versions != {"numpy": "2.4.6", "scikit-learn": "1.9.0", "xgboost-cpu": "3.2.0"}:
        raise ValueError("runtime mismatch")
    prior = json.loads(
        gzip.decompress((args.previous / "information-full-001/result.json.gz").read_bytes())
    )
    keyed = {(r["assay_id"], r["run_seed"]): r for r in prior["rows"] if r["model_id"] == MODEL}
    source = json.loads(gzip.decompress((args.previous / "full-001/result.json.gz").read_bytes()))
    feature_shas = {r["assay_id"]: r["new_feature_sha256"] for r in source["inputs"]}
    root = args.study / "data-001"
    manifest = e3.exact_json(root / "input.json", e3.INPUT_SHA)
    if tuple(x["assay_id"] for x in manifest["assays"]) != e3.ASSAY_IDS:
        raise ValueError("assay scope mismatch")
    e3.write(
        args.output / "execution.json",
        {
            "protocol": PROTOCOL,
            "mode": args.mode,
            "source_sha256": e3.digest(__file__),
            "plan_sha256": e3.digest(args.plan),
            "helper_sha256": cov.HELPER_SHA,
            "coverage_helper_sha256": COVERAGE_SOURCE_SHA,
            "runtime_sha256": cov.RUNTIME_SHA,
            "input_sha256": e3.INPUT_SHA,
            "prior_result_sha256": cov.INFORMATION_RESULT_SHA,
            "input_result_sha256": cov.INPUT_RESULT_SHA,
            "versions": versions,
            "regression_parameters": dict(e3.rt.model_effective_parameters(MODEL)),
            "ranking_overrides": RANK_PARAMETERS,
            "ranking_target": "dense observed-label ranks",
            "training_counts": [32, 64],
            "label_cost": 80,
            "batch_size": 16,
            "features": 421,
            "common_frontier": "union of prediction-only top64",
            "application_delta": None,
            "evidence_stage": "ITERATION",
            "truth_boundary": "allowed observations; predictions locked before scoring",
        },
    )
    seeds = (1729,) if args.mode == "smoke" else e3.RUN_SEEDS
    indices = [i for i, (m, n, s) in enumerate(e3.SPECS) if m == MODEL and s in seeds]
    rows = []
    for item in manifest["assays"]:
        assay, ref = item["assay_id"], item["artifacts"]["candidate"]
        if (
            item["partition"] != "candidate-pool"
            or item["held_out_partitions"]
            or item["label_direction"] != "higher_is_better"
        ):
            raise ValueError("candidate/label scope mismatch")
        table = e3.rt.load_candidate_table(
            root / ref["path"],
            assay_id=assay,
            expected_sha256=ref["sha256"],
            expected_size=ref["size_bytes"],
        )
        ref = item["artifacts"]["membership"]
        e3.validate_membership(root / ref["path"], ref, table.candidate_ids)
        old_dir = args.previous / "full-001/private" / assay
        private = args.output / "private" / assay
        private.mkdir(parents=True)
        evaluator = e3.Evaluator(root, item, table)
        initargs = (
            assay,
            table.candidate_ids,
            str(old_dir / "features.npy"),
            feature_shas[assay],
            str(private),
        )
        with mp.get_context("spawn").Pool(1, initializer=initialize, initargs=initargs) as pool:
            for index in indices:
                _, size, seed = e3.SPECS[index]
                initial = e3.initial_selection(
                    table.candidate_ids,
                    assay_id=assay,
                    run_seed=seed,
                    size=size,
                    assay_ids=e3.ASSAY_IDS,
                )
                task = (
                    index,
                    size,
                    seed,
                    evaluator.truth.reveal(initial),
                    str(old_dir),
                    keyed[(assay, seed)]["source_identity"][str(size)],
                )
                if pool.apply(fit_select, (task,)) != index:
                    raise ValueError("worker identity mismatch")
                e3.event(args.output, "predictions_and_frontier_saved", assay_id=assay, index=index)
        e3.event(args.output, "fit_workers_exited", assay_id=assay)
        truth = evaluator.truth._M5CTruthEvaluator__truth
        membership = evaluator.truth._M5CTruthEvaluator__memberships
        for index in indices:
            _, size, seed = e3.SPECS[index]
            meta_path = private / f"selection-{index:03d}.json"
            meta = json.loads(meta_path.read_text())
            path = private / f"prediction-{index:03d}.npz"
            if (
                e3.digest(path) != meta["prediction_sha256"]
                or e3.digest(private / f"model-config-{index:03d}.json")
                != meta["model_config_sha256"]
            ):
                raise ValueError("saved output identity mismatch")
            with np.load(path, allow_pickle=False) as data:
                common, frontier = data["remaining_indices"], data["frontier"]
                ids = np.asarray(table.candidate_ids)[common]
                before, after, expected_frontier = orders_and_frontier(
                    ids, data["regression"], data["ranking"]
                )
                if not np.array_equal(frontier, expected_frontier):
                    raise ValueError("frontier reconstruction mismatch")
                arms = {}
                for arm, order in (("regression", before), ("ranking", after)):
                    if (
                        not np.array_equal(order, data[arm + "_order"])
                        or ids[order[:16]].tolist() != meta[arm]
                    ):
                        raise ValueError("selection reconstruction mismatch")
                    score = data[arm]
                    metrics = cov.prediction_metrics(
                        score,
                        truth[common],
                        membership[0.01][common],
                        membership[0.02][common],
                        order,
                    )
                    del metrics["mae"]
                    metrics["common_frontier_auc"] = e3.auc(
                        score[frontier], membership[0.01][common][frontier]
                    )
                    metrics["training_spearman"] = meta["training_spearman"][arm]
                    for cutoff in (32, 64):
                        metrics[f"hits_{cutoff}"] = int(
                            membership[0.01][common][order[:cutoff]].sum()
                        )
                    outcome = evaluator.truth.evaluate_batch(meta["common_initial_ids"], meta[arm])
                    metrics["greedy_hits"] = outcome["high_value"]["0.01"]["count"]
                    metrics["greedy_recall"] = outcome["high_value"]["0.01"]["recall"]
                    metrics["greedy_best"] = outcome["best_found_improvement"]
                    if metrics["greedy_hits"] != metrics["top16_high"]:
                        raise ValueError("rank and batch hits disagree")
                    if arm == "regression":
                        previous = keyed[(assay, seed)]["arms"][f"new{size}"]
                        if any(metrics[k] != v for k, v in previous.items() if k in metrics):
                            raise ValueError("old aggregate reconstruction mismatch")
                    arms[arm] = metrics
                rows.append(
                    {
                        "assay_id": assay,
                        "model_id": MODEL,
                        "run_seed": seed,
                        "training_count": size,
                        "remaining_count": len(common),
                        "label_cost": 80,
                        "high_value_denominator": int(membership[0.01].sum()),
                        "frontier_count": len(frontier),
                        "frontier_high_count": int(membership[0.01][common][frontier].sum()),
                        "top16_overlap": len(set(before[:16]) & set(after[:16])),
                        **arms,
                        "prediction_sha256": meta["prediction_sha256"],
                        "selection_sha256": e3.digest(meta_path),
                        "observation_sha256": meta["observation_sha256"],
                        "model_config_sha256": meta["model_config_sha256"],
                    }
                )
        del evaluator, truth, membership
        e3.event(args.output, "assay_scored", assay_id=assay, completed_states=len(rows))
    result = {
        "protocol": PROTOCOL,
        "completed_states": len(rows),
        "new_fits": 2 * len(rows),
        "new_measurements": 0,
        "scored_branches": 2 * len(rows),
        "baseline_refit": "EXACT",
        "fixed_observations": "PASS",
        "common_pool": "PASS",
        "rows": rows,
        "summaries": summarize(rows) if args.mode == "full" else [],
        "limits": [
            "development",
            "one ranking implementation",
            "ordinal scores not calibrated",
            "no new E/UQ claim",
            "conditional frontier not independent",
            "no application delta",
            "no population CI",
            "joint M5 incomplete",
        ],
    }
    with (args.output / "result.json.gz").open("xb") as handle:
        handle.write(
            gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0)
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "previous", "output", "plan"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--wall-seconds", type=int, default=2300)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selftest()
        print("ranking objective self-test PASS")
        return 0
    if any(getattr(args, k) is None for k in ("study", "previous", "output", "plan")):
        parser.error("study, previous, output and plan required")
    if args.wall_seconds <= 0 or args.wall_seconds > 2400:
        parser.error("invalid wall cap")
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
                or size > 1073741824
                or time.monotonic() - started > args.wall_seconds
            ):
                breach.append("budget")
                os.kill(os.getpid(), signal.SIGTERM)
                return

    signal.signal(signal.SIGTERM, terminate)
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
        terminal = {
            "status": status,
            "failure_class": failure,
            "wall_seconds": time.monotonic() - started,
            "mode": args.mode,
            **usage,
            "source_sha256": e3.digest(__file__),
            "artifacts": {p.name: e3.digest(p) for p in args.output.iterdir() if p.is_file()},
        }
        e3.write(args.output / "terminal.json", terminal)
        print(json.dumps(terminal), flush=True)
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
